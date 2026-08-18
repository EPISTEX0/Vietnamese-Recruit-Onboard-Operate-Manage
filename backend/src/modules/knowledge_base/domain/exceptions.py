"""Domain exceptions for the Knowledge Base module.

Precedent: ``recruitment/domain/exceptions.py``. Unlike that hierarchy,
``message`` here IS the text shown to the client — every existing
knowledge_base message is a hand-written Vietnamese literal aimed at a user,
not an interpolated third-party string, so there is nothing to keep out of
the response body.

The router (:mod:`src.modules.knowledge_base.api.router`) maps these to an
HTTP status by catching ``KnowledgeBaseError`` and reading ``status_code`` —
not by matching text in the message (issue #398).
"""

from __future__ import annotations


class KnowledgeBaseError(Exception):
    """Base exception for the knowledge_base module.

    Attributes:
        status_code: HTTP status code the router should return.
        error_code: Machine-readable error identifier.
        message: Human-readable, user-facing error description.
    """

    status_code: int = 400
    error_code: str = "KNOWLEDGE_BASE_ERROR"
    message: str = "A knowledge base module error occurred"

    def __init__(self, message: str | None = None) -> None:
        """Initialize KnowledgeBaseError.

        Args:
            message: Optional message override. If not provided, the
                class-level default message is used.
        """
        if message is not None:
            self.message = message
        super().__init__(self.message)


class DocumentNotFoundError(KnowledgeBaseError):
    """Document does not exist for the given id and kb_type."""

    status_code = 404
    error_code = "DOCUMENT_NOT_FOUND"
    message = "Không tìm thấy tài liệu."


class InvalidKnowledgeBaseTypeError(KnowledgeBaseError):
    """``kb_type`` is not one of the supported values."""

    status_code = 400
    error_code = "INVALID_KB_TYPE"


class InvalidDocumentFileError(KnowledgeBaseError):
    """Uploaded file fails MIME type or size validation."""

    status_code = 400
    error_code = "INVALID_DOCUMENT_FILE"
