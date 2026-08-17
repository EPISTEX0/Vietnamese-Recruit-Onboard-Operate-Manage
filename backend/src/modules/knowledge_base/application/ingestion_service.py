"""Knowledge Base ingestion service.

ARQ worker task: download file from MinIO, parse text, chunk, embed, index.
Supports both HR and Employee KB via kb_type parameter (Issue #260).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from io import BytesIO

import httpx

from src.modules.employee.infrastructure.minio_client import MinIOClient
from src.modules.knowledge_base.domain.entities import (
    EmployeeKnowledgeBaseChunk,
    KnowledgeBaseChunk,
)
from src.modules.knowledge_base.domain.enums import KnowledgeBaseDocumentStatus
from src.modules.knowledge_base.infrastructure.config import KnowledgeBaseSettings
from src.modules.knowledge_base.infrastructure.repository import (
    KnowledgeBaseRepository,
)

logger = logging.getLogger(__name__)

# Approximate chars-per-token for Vietnamese text.
# GPT-style tokenizers average ~4 chars/token for Vietnamese; we use a slightly
# conservative ratio for the simple character-based chunker.
CHARS_PER_TOKEN = 4

# The embedding service rejects a request carrying more than this many texts
# (``EmbedRequest.texts`` max_length in vroom-embedding/app.py). A staff
# handbook chunks into several hundred pieces at the default 512-token chunk
# size, so anything above this has to be split before it is sent.
EMBEDDING_SERVICE_MAX_TEXTS = 100

# How many chunks we put in one request. Sized from the embedding service's
# worst case rather than its hard cap: at its default configuration it forwards
# to the provider in batches of 10, sequentially, each batch costing up to
# EMBEDDING_TIMEOUT 12s * 2 attempts + 0.5s backoff ≈ 24.5s. Four provider
# batches is ~98s, which fits inside EMBEDDING_REQUEST_TIMEOUT below; a full
# batch of 100 would not, turning a fast 422 into a slow client timeout.
#
# Those service-side numbers are operator-tunable (EMBEDDING_TIMEOUT,
# EMBEDDING_MAX_RETRIES, EMBEDDING_BATCH_SIZE in .env), so this only holds for
# the shipped defaults. Raising EMBEDDING_TIMEOUT without raising
# EMBEDDING_REQUEST_TIMEOUT here just moves the failure from the service's
# 422 to our own read timeout — EMBEDDING_TOTAL_TIMEOUT still bounds the
# document either way, so a misconfigured pair costs a failed ingest, not a
# pinned worker.
EMBEDDING_REQUEST_BATCH_SIZE = 40

# Per-request read timeout. Sized against EMBEDDING_REQUEST_BATCH_SIZE above.
EMBEDDING_REQUEST_TIMEOUT = 120.0

# Ceiling on the embedding phase for one document, across all its batches.
# Below ARQ's default 300s job_timeout on purpose: hitting our own budget
# raises inside the try block, so the document is marked 'error' with a
# readable reason, whereas letting ARQ cancel the job leaves it stuck in
# 'processing' forever. Raise both together if very large documents need
# longer.
EMBEDDING_TOTAL_TIMEOUT = 240.0


def _extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from a PDF file using PyMuPDF (fitz)."""
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except fitz.FileDataError as exc:
        raise ValueError(f"File PDF bị hỏng hoặc không hợp lệ: {exc}") from exc

    pages: list[str] = []
    try:
        for page in doc:
            text = page.get_text()
            if text:
                pages.append(text)
    finally:
        doc.close()

    result = "\n\n".join(pages)
    if not result.strip():
        raise ValueError("Không thể trích xuất văn bản từ file PDF.")
    return result


def _extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text from a DOCX file."""
    try:
        from docx import Document

        doc = Document(BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except Exception as exc:
        raise ValueError(f"Không thể trích xuất văn bản từ file DOCX: {exc}") from exc


def _extract_text_from_txt(file_bytes: bytes) -> str:
    """Extract text from a plain text file."""
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return file_bytes.decode("latin-1")


def extract_text(file_bytes: bytes, mime_type: str) -> str:
    """Extract text from file bytes based on MIME type.

    Args:
        file_bytes: Raw file bytes.
        mime_type: MIME type of the file.

    Returns:
        Extracted plain text.

    Raises:
        ValueError: If the MIME type is unsupported or extraction fails.
    """
    if mime_type == "application/pdf":
        return _extract_text_from_pdf(file_bytes)
    elif mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        return _extract_text_from_docx(file_bytes)
    elif mime_type == "text/plain":
        return _extract_text_from_txt(file_bytes)
    else:
        raise ValueError(f"MIME type không được hỗ trợ: {mime_type}")


def chunk_text(
    text: str,
    chunk_size_tokens: int = 512,
    chunk_overlap_tokens: int = 50,
) -> list[str]:
    """Split text into overlapping chunks of approximately chunk_size_tokens.

    Splits on sentence boundaries (., !, ?, newline) within each chunk to
    produce readable segments. Falls back to character-based chunking for
    text without clear sentence boundaries.

    Args:
        text: The full text to chunk.
        chunk_size_tokens: Target chunk size in tokens (default 512).
        chunk_overlap_tokens: Overlap between consecutive chunks (default 50).

    Returns:
        List of chunk strings.
    """
    chunk_size_chars = chunk_size_tokens * CHARS_PER_TOKEN
    overlap_chars = chunk_overlap_tokens * CHARS_PER_TOKEN
    step = chunk_size_chars - overlap_chars

    if step <= 0:
        raise ValueError(
            f"chunk_overlap_tokens ({chunk_overlap_tokens}) must be less than "
            f"chunk_size_tokens ({chunk_size_tokens})"
        )

    # Split into sentences for natural boundaries
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return []

    chunks: list[str] = []
    current_chunk = ""
    current_length = 0

    for sentence in sentences:
        sentence_len = len(sentence)

        if current_length + sentence_len <= chunk_size_chars:
            # Add to current chunk
            if current_chunk:
                current_chunk += " "
                current_length += 1
            current_chunk += sentence
            current_length += sentence_len
        else:
            # Finalize current chunk
            if current_chunk.strip():
                chunks.append(current_chunk.strip())

            # Start new chunk. If the sentence itself is too long, split it further.
            if sentence_len > chunk_size_chars:
                # Character-based splitting for very long sentences
                for i in range(0, sentence_len, step):
                    sub = sentence[i : i + chunk_size_chars].strip()
                    if sub:
                        chunks.append(sub)
                current_chunk = ""
                current_length = 0
            else:
                # Start new chunk with overlap from previous
                if chunks and overlap_chars > 0:
                    prev = chunks[-1]
                    overlap_text = prev[-overlap_chars:] if len(prev) > overlap_chars else prev
                    current_chunk = overlap_text + " " + sentence
                    current_length = len(current_chunk)
                else:
                    current_chunk = sentence
                    current_length = sentence_len

    # Don't forget the last chunk
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def estimate_token_count(text: str) -> int:
    """Estimate token count based on character count.

    Uses a simple approximation: ~4 characters per token for Vietnamese.
    """
    return max(1, len(text) // CHARS_PER_TOKEN)


async def call_embedding_service(
    texts: list[str],
    embedding_url: str,
    timeout: float = EMBEDDING_REQUEST_TIMEOUT,
    batch_size: int = EMBEDDING_REQUEST_BATCH_SIZE,
    total_timeout: float | None = EMBEDDING_TOTAL_TIMEOUT,
) -> list[list[float]]:
    """Call the vroom-embedding service to get embeddings for a list of texts.

    Splits the input into requests of at most ``batch_size`` texts, because the
    service rejects anything larger than ``EMBEDDING_SERVICE_MAX_TEXTS`` with a
    422. Batching lives here rather than at the call sites so that every caller
    — ingestion and retrieval alike — is covered by one implementation.

    Batches are sent sequentially and their vectors concatenated in order, so
    the result is aligned index-for-index with ``texts``. Each batch's length is
    checked on arrival: a provider that returns the wrong count fails the call
    instead of silently shifting every later chunk onto the wrong vector.

    Args:
        texts: List of text strings to embed.
        embedding_url: URL of the embedding service (e.g., http://vroom-embedding:8080).
        timeout: Per-request timeout in seconds.
        batch_size: Maximum texts per request. Must not exceed the service's
            own limit.
        total_timeout: Overall budget across every batch, so one oversized
            document cannot occupy a worker indefinitely. ``None`` disables it.

    Returns:
        List of embedding vectors, one per input text, in input order.

    Raises:
        ValueError: If ``batch_size`` is unusable, or a batch comes back with a
            different number of vectors than it had texts.
        TimeoutError: If ``total_timeout`` is exhausted before every batch is done.
        httpx.HTTPError: If any embedding service call fails.
    """
    if batch_size <= 0 or batch_size > EMBEDDING_SERVICE_MAX_TEXTS:
        raise ValueError(
            f"batch_size phải nằm trong khoảng 1..{EMBEDDING_SERVICE_MAX_TEXTS}, "
            f"nhận được {batch_size}."
        )
    if not texts:
        return []

    deadline = None if total_timeout is None else time.monotonic() + total_timeout
    embeddings: list[list[float]] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]

            request_timeout = timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Vượt ngân sách thời gian embedding ({total_timeout}s) sau "
                        f"{len(embeddings)}/{len(texts)} đoạn văn bản."
                    )
                # Never let a single request outlive the document's budget.
                request_timeout = min(timeout, remaining)

            response = await client.post(
                f"{embedding_url}/embed",
                json={"texts": batch},
                timeout=request_timeout,
            )
            response.raise_for_status()
            batch_embeddings = response.json()["embeddings"]

            if len(batch_embeddings) != len(batch):
                raise ValueError(
                    f"Số lượng embedding ({len(batch_embeddings)}) không khớp số "
                    f"lượng văn bản ({len(batch)}) trong một lô."
                )
            embeddings.extend(batch_embeddings)

    return embeddings


class IngestionService:
    """Handles the document ingestion pipeline: parse → chunk → embed → index.

    This is called by the ARQ worker task ``ingest_document``.
    Supports both HR and Employee KB (Issue #260).
    """

    def __init__(
        self,
        repo: KnowledgeBaseRepository,
        minio_client: MinIOClient,
        settings: KnowledgeBaseSettings,
    ) -> None:
        self._repo = repo
        self._minio = minio_client
        self._settings = settings

    async def ingest(self, document_id: uuid.UUID, kb_type: str = "hr") -> None:
        """Run the full ingestion pipeline for a document.

        Steps:
        1. Load document and update status to 'processing'.
        2. Download file from MinIO.
        3. Extract text based on MIME type.
        4. Chunk text (~512 tokens, 50 overlap).
        5. Call embedding service for all chunks.
        6. Insert chunks with embeddings into pgvector (correct table).
        7. Update document status to 'ready'.

        On failure, updates status to 'error' with the error message.

        Args:
            document_id: UUID of the document to ingest.
            kb_type: Knowledge base type — 'hr' or 'employee'.
        """
        doc = await self._repo.get_document(document_id, kb_type=kb_type)
        if doc is None:
            raise ValueError(f"Document not found: {document_id}")

        try:
            # Step 1: Mark as processing
            await self._repo.update_document_status(
                document_id,
                KnowledgeBaseDocumentStatus.PROCESSING,
                kb_type=kb_type,
            )

            # Step 2: Download file from MinIO
            file_bytes = await self._minio.download_file(doc.storage_path)

            # Step 3: Extract text
            text = extract_text(file_bytes, doc.mime_type)
            if not text.strip():
                raise ValueError("File không chứa văn bản có thể trích xuất.")

            # Step 4: Chunk
            chunks = chunk_text(
                text,
                chunk_size_tokens=self._settings.chunk_size_tokens,
                chunk_overlap_tokens=self._settings.chunk_overlap_tokens,
            )
            if not chunks:
                raise ValueError("Không thể tạo chunk từ văn bản.")

            # Step 5: Embed all chunks. call_embedding_service splits this into
            # requests the embedding service will accept and reassembles them
            # in order, so a document of any size is one call from here.
            embedding_url = self._settings.embedding_service_url
            embeddings = await call_embedding_service(chunks, embedding_url)

            # Belt and braces: call_embedding_service already checks each batch,
            # so this only fires if that contract is ever broken. Cheap enough
            # to keep as the guard against chunk/vector misalignment reaching
            # pgvector, where it would be silent and permanent.
            if len(embeddings) != len(chunks):
                raise ValueError(
                    f"Số lượng embedding ({len(embeddings)}) không khớp "
                    f"số lượng chunk ({len(chunks)})."
                )

            # Safe-delete check (Issue #261): verify document still exists before inserting chunks.
            # If the document was deleted while ingestion was running, abort
            # to avoid creating orphan chunks.
            doc_still_exists = await self._repo.get_document(document_id, kb_type=kb_type)
            if doc_still_exists is None:
                logger.warning(
                    "Document %s was deleted during ingestion — aborting chunk insert (kb_type=%s)",
                    document_id,
                    kb_type,
                )
                return

            # Step 6: Insert chunks with embeddings — use correct chunk entity
            if kb_type == "employee":
                chunk_entity_class = EmployeeKnowledgeBaseChunk
            else:
                chunk_entity_class = KnowledgeBaseChunk

            chunk_entities: list = []
            for i, (chunk_text_val, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_entities.append(
                    chunk_entity_class(
                        document_id=document_id,
                        chunk_index=i,
                        content=chunk_text_val,
                        embedding=embedding,
                        token_count=estimate_token_count(chunk_text_val),
                    )
                )
            await self._repo.insert_chunks(chunk_entities)

            # Step 7: Mark as ready
            await self._repo.update_document_status(
                document_id,
                KnowledgeBaseDocumentStatus.READY,
                kb_type=kb_type,
                chunk_count=len(chunks),
            )

            logger.info(
                "Document %s ingested successfully: %d chunks (kb_type=%s)",
                document_id,
                len(chunks),
                kb_type,
            )

        except Exception as exc:
            error_msg = str(exc)
            logger.exception(
                "Ingestion failed for document %s (kb_type=%s): %s",
                document_id,
                kb_type,
                error_msg,
            )
            await self._repo.update_document_status(
                document_id,
                KnowledgeBaseDocumentStatus.ERROR,
                kb_type=kb_type,
                error_message=error_msg[:4000],  # Truncate to avoid overflow
            )
