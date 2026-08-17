"""Domain enums for the Knowledge Base module."""

from enum import StrEnum


class KnowledgeBaseDocumentStatus(StrEnum):
    """Ingestion status of a knowledge base document.

    Shared by :class:`KnowledgeBaseDocument` and
    :class:`EmployeeKnowledgeBaseDocument` — both entities track the same
    upload → chunk/embed pipeline.

    - pending: Uploaded, awaiting ingestion.
    - processing: Chunking and embedding in progress.
    - ready: Ingestion complete; chunks are searchable.
    - error: Ingestion failed; see ``error_message``.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"
