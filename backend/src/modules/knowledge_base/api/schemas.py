"""Knowledge Base API schemas.

Pydantic models for request/response serialization of knowledge base endpoints.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.modules.knowledge_base.domain.enums import KnowledgeBaseDocumentStatus


class DocumentUploadResponse(BaseModel):
    """Response after a successful document upload."""

    document_id: UUID
    display_name: str
    status: KnowledgeBaseDocumentStatus
    category: str
    file_name: str
    file_size: int
    mime_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentListItem(BaseModel):
    """A single document in the list response."""

    id: UUID
    display_name: str
    category: str
    status: KnowledgeBaseDocumentStatus
    file_name: str
    file_size: int
    mime_type: str
    chunk_count: int
    description: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    """Paginated document list response."""

    items: list[DocumentListItem]
    total: int
    page: int
    page_size: int


class DocumentDetailResponse(BaseModel):
    """Full document detail including error info."""

    id: UUID
    display_name: str
    category: str
    status: KnowledgeBaseDocumentStatus
    file_name: str
    storage_path: str
    file_size: int
    mime_type: str
    chunk_count: int
    description: str | None = None
    error_message: str | None = None
    kb_type: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentUpdateRequest(BaseModel):
    """Request body for PATCH /documents/{id} — metadata-only update."""

    display_name: str | None = Field(default=None, description="Tên hiển thị mới")
    category: str | None = Field(default=None, description="Danh mục mới")
    description: str | None = Field(default=None, description="Mô tả mới")


class DocumentUpdateResponse(BaseModel):
    """Response after a metadata update (PATCH)."""

    id: UUID
    display_name: str
    category: str
    status: KnowledgeBaseDocumentStatus
    description: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentReplaceResponse(BaseModel):
    """Response after a file replacement (PUT)."""

    document_id: UUID
    display_name: str
    status: KnowledgeBaseDocumentStatus
    category: str
    file_name: str
    file_size: int
    mime_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    """Simple message response for DELETE and other actions."""

    message: str
    document_id: UUID
