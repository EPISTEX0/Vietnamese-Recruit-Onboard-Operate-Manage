"""Knowledge Base document service.

Handles document upload orchestration: validates file, stores in MinIO,
persists metadata, and enqueues the ARQ ingestion job.

Supports both HR and Employee KB via kb_type parameter (Issue #260).
Adds metadata update, file replacement, and hard delete (Issue #261).
"""

from __future__ import annotations

import logging
import uuid
from typing import BinaryIO

from arq import ArqRedis

from src.modules.employee.infrastructure.minio_client import MinIOClient
from src.modules.knowledge_base.domain.entities import (
    EmployeeKnowledgeBaseDocument,
    KnowledgeBaseDocument,
)
from src.modules.knowledge_base.domain.enums import KnowledgeBaseDocumentStatus
from src.modules.knowledge_base.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentFileError,
    InvalidKnowledgeBaseTypeError,
)
from src.modules.knowledge_base.infrastructure.config import KnowledgeBaseSettings
from src.modules.knowledge_base.infrastructure.repository import (
    KnowledgeBaseRepository,
)

logger = logging.getLogger(__name__)

# Allowed MIME types for upload
ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "text/plain",
    }
)

# Max file size (20 MB default)
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024


class DocumentService:
    """Orchestrates document upload and status queries.

    Wired by :func:`~src.modules.knowledge_base.container.get_document_service`.
    """

    def __init__(
        self,
        repo: KnowledgeBaseRepository,
        minio_client: MinIOClient,
        settings: KnowledgeBaseSettings,
        arq_redis: ArqRedis | None = None,
    ) -> None:
        self._repo = repo
        self._minio = minio_client
        self._settings = settings
        self._arq = arq_redis

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_file(mime_type: str, file_size: int, max_size: int) -> None:
        """Validate MIME type and file size. Raises InvalidDocumentFileError on failure."""
        if mime_type not in ALLOWED_MIME_TYPES:
            raise InvalidDocumentFileError(
                f"Loại file không được hỗ trợ: {mime_type}. Chỉ chấp nhận PDF, DOCX, DOC, TXT."
            )
        if file_size > max_size:
            raise InvalidDocumentFileError(
                f"File vượt quá kích thước tối đa {max_size // (1024 * 1024)}MB."
            )

    @staticmethod
    def _validate_kb_type(kb_type: str) -> None:
        """Validate kb_type. Raises InvalidKnowledgeBaseTypeError on failure."""
        if kb_type not in ("hr", "employee"):
            raise InvalidKnowledgeBaseTypeError(
                f"Loại knowledge base không hợp lệ: {kb_type}. Chỉ chấp nhận hr hoặc employee."
            )

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload_document(
        self,
        file: BinaryIO,
        file_name: str,
        mime_type: str,
        display_name: str,
        category: str = "general",
        kb_type: str = "hr",
    ) -> KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument:
        """Upload a document: validate, store in MinIO, persist metadata, enqueue job.

        Args:
            file: A file-like object in binary mode (e.g., from UploadFile).
            file_name: Original file name (for storage path and metadata).
            mime_type: MIME type of the uploaded file.
            display_name: Human-readable name for UI display.
            category: Document category (e.g., 'policy', 'procedure', 'general').
            kb_type: Knowledge base type — 'hr' or 'employee' (Issue #260).

        Returns:
            The created document entity with status='pending'.

        Raises:
            InvalidKnowledgeBaseTypeError: If kb_type is not 'hr' or 'employee'.
            InvalidDocumentFileError: If the file type is not allowed or file is too large.
        """
        self._validate_kb_type(kb_type)

        # Read file bytes
        file_bytes = file.read()
        file_size = len(file_bytes)

        # Validate MIME type and size
        max_size = self._settings.max_file_size_mb * 1024 * 1024
        self._validate_file(mime_type, file_size, max_size)

        # Create document record to get an ID
        doc_id = uuid.uuid4()
        storage_path = f"{kb_type}/{doc_id}/{file_name}"

        # Upload to MinIO
        await self._minio.upload_file(
            path=storage_path,
            file_data=file_bytes,
            content_type=mime_type,
        )

        # Persist metadata — use correct entity class based on kb_type
        if kb_type == "employee":
            doc = EmployeeKnowledgeBaseDocument(
                id=doc_id,
                display_name=display_name,
                category=category,
                file_name=file_name,
                storage_path=storage_path,
                file_size=file_size,
                mime_type=mime_type,
                status=KnowledgeBaseDocumentStatus.PENDING,
            )
        else:
            doc = KnowledgeBaseDocument(
                id=doc_id,
                display_name=display_name,
                category=category,
                file_name=file_name,
                storage_path=storage_path,
                file_size=file_size,
                mime_type=mime_type,
                status=KnowledgeBaseDocumentStatus.PENDING,
            )
        doc = await self._repo.insert_document(doc)

        # Enqueue ARQ ingestion job (pass kb_type so worker knows which table)
        await self._enqueue_ingestion(doc_id, kb_type)

        return doc

    async def _enqueue_ingestion(self, doc_id: uuid.UUID, kb_type: str) -> None:
        """Enqueue an ARQ ingest_document job, or log a warning if unavailable."""
        if self._arq is not None:
            await self._arq.enqueue_job(
                "ingest_document",
                document_id=str(doc_id),
                kb_type=kb_type,
            )
            logger.info(
                "Enqueued ingest_document job for doc %s (kb_type=%s)",
                doc_id,
                kb_type,
            )
        else:
            logger.warning(
                "ARQ Redis not available — document %s uploaded but not enqueued",
                doc_id,
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_document(
        self,
        document_id: uuid.UUID,
        kb_type: str = "hr",
    ) -> KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument | None:
        """Get a document by id."""
        return await self._repo.get_document(document_id, kb_type=kb_type)

    async def list_documents(
        self,
        kb_type: str = "hr",
        page: int = 1,
        page_size: int = 20,
        category: str | None = None,
        status: str | None = None,
    ) -> tuple[list, int]:
        """List documents with pagination and optional filters (Issue #261)."""
        docs, total = await self._repo.list_documents(
            kb_type=kb_type,
            page=page,
            page_size=page_size,
            category=category,
            status=status,
        )
        return list(docs), total

    # ------------------------------------------------------------------
    # Update metadata (PATCH — Issue #261)
    # ------------------------------------------------------------------

    async def update_metadata(
        self,
        document_id: uuid.UUID,
        *,
        kb_type: str = "hr",
        display_name: str | None = None,
        category: str | None = None,
        description: str | None = None,
    ) -> KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument | None:
        """Update document metadata without re-indexing.

        Only updates provided (non-None) fields. Does not touch file,
        chunks, or ingestion status (Issue #261, KB-05).
        """
        return await self._repo.update_document_metadata(
            document_id,
            kb_type=kb_type,
            display_name=display_name,
            category=category,
            description=description,
        )

    # ------------------------------------------------------------------
    # Replace file (PUT — Issue #261)
    # ------------------------------------------------------------------

    async def replace_file(
        self,
        document_id: uuid.UUID,
        file: BinaryIO,
        file_name: str,
        mime_type: str,
        kb_type: str = "hr",
    ) -> KnowledgeBaseDocument | EmployeeKnowledgeBaseDocument:
        """Replace a document's file: delete old chunks, upload new file, re-index.

        Steps:
        1. Verify document exists
        2. Read new file bytes, validate MIME type and size
        3. Delete old chunks from pgvector
        4. Delete old file from MinIO
        5. Upload new file to MinIO
        6. Update document metadata (file_name, storage_path, file_size, mime_type)
        7. Reset status to "pending" and clear error_message
        8. Enqueue ARQ re-index job

        Args:
            document_id: UUID of the existing document.
            file: A file-like object with the new document content.
            file_name: Original file name of the new file.
            mime_type: MIME type of the new file.
            kb_type: Knowledge base type.

        Returns:
            The updated document entity.

        Raises:
            InvalidKnowledgeBaseTypeError: If kb_type is not 'hr' or 'employee'.
            DocumentNotFoundError: If the document is not found.
            InvalidDocumentFileError: If file validation fails.
        """
        self._validate_kb_type(kb_type)

        # 1. Verify document exists
        doc = await self._repo.get_document(document_id, kb_type=kb_type)
        if doc is None:
            raise DocumentNotFoundError()

        old_storage_path = doc.storage_path

        # 2. Read and validate new file
        file_bytes = file.read()
        file_size = len(file_bytes)
        max_size = self._settings.max_file_size_mb * 1024 * 1024
        self._validate_file(mime_type, file_size, max_size)

        # 3. Delete old chunks
        await self._repo.delete_chunks_by_document(document_id, kb_type=kb_type)

        # 4. Delete old file from MinIO (best-effort)
        try:
            await self._minio.delete_file(old_storage_path)
        except Exception:
            logger.warning(
                "Failed to delete old file from MinIO: %s (doc %s)",
                old_storage_path,
                document_id,
            )

        # 5. Upload new file to MinIO
        new_storage_path = f"{kb_type}/{document_id}/{file_name}"
        await self._minio.upload_file(
            path=new_storage_path,
            file_data=file_bytes,
            content_type=mime_type,
        )

        # 6 & 7. Update document metadata and reset status
        doc.file_name = file_name
        doc.storage_path = new_storage_path
        doc.file_size = file_size
        doc.mime_type = mime_type
        doc.status = KnowledgeBaseDocumentStatus.PENDING
        doc.error_message = None
        doc.chunk_count = 0

        from datetime import UTC, datetime

        doc.updated_at = datetime.now(UTC)
        await self._repo.flush()

        # 8. Enqueue re-index
        await self._enqueue_ingestion(document_id, kb_type)

        return doc

    # ------------------------------------------------------------------
    # Delete (DELETE — Issue #261)
    # ------------------------------------------------------------------

    async def delete_document(
        self,
        document_id: uuid.UUID,
        kb_type: str = "hr",
    ) -> str | None:
        """Hard-delete a document: delete chunks, MinIO file, and document row.

        Steps:
        1. Verify document exists
        2. Delete chunks from pgvector
        3. Delete file from MinIO (best-effort)
        4. Delete document row from DB

        The ARQ worker's ingest_document task checks for document existence
        before inserting chunks, so if a job is running concurrently and the
        document is deleted, the job will abort without creating orphan chunks
        (Issue #261 safe-delete guarantee).

        Args:
            document_id: UUID of the document to delete.
            kb_type: Knowledge base type.

        Returns:
            The storage_path of the deleted file for logging.

        Raises:
            InvalidKnowledgeBaseTypeError: If kb_type is not 'hr' or 'employee'.
            DocumentNotFoundError: If the document is not found.
        """
        self._validate_kb_type(kb_type)

        # 1. Verify document exists
        doc = await self._repo.get_document(document_id, kb_type=kb_type)
        if doc is None:
            raise DocumentNotFoundError()

        storage_path = doc.storage_path

        # 2. Delete document (chunks first, then row)
        await self._repo.delete_document(document_id, kb_type=kb_type)

        # 3. Delete file from MinIO (best-effort — file may already be gone)
        try:
            await self._minio.delete_file(storage_path)
        except Exception:
            logger.warning(
                "Failed to delete file from MinIO: %s (doc %s)",
                storage_path,
                document_id,
            )

        logger.info(
            "Document %s (kb_type=%s) deleted successfully",
            document_id,
            kb_type,
        )

        return storage_path
