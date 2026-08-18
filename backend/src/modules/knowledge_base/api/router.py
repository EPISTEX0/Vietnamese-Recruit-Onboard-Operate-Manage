"""FastAPI router for the Knowledge Base module.

Defines endpoints for HR and Employee document upload, list, detail,
metadata update (PATCH), file replacement (PUT), and deletion (DELETE).
Requires admin role for all endpoints.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from src.modules.identity.api.admin_router import require_hr
from src.modules.identity.domain.entities import User
from src.modules.knowledge_base.api.schemas import (
    DocumentDetailResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentReplaceResponse,
    DocumentUpdateRequest,
    DocumentUpdateResponse,
    DocumentUploadResponse,
    MessageResponse,
)
from src.modules.knowledge_base.application.document_service import DocumentService
from src.modules.knowledge_base.container import get_document_service
from src.modules.knowledge_base.domain.exceptions import KnowledgeBaseError

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

HRUserDep = Annotated[User, Depends(require_hr)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/knowledge-base", tags=["knowledge-base"])


@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=201,
)
async def upload_document(
    file: UploadFile = File(...),
    display_name: str = Form(..., description="Tên hiển thị của tài liệu"),
    category: str = Form(default="general", description="Danh mục tài liệu"),
    kb_type: str = Form(default="hr", description="Loại knowledge base: hr | employee"),
    _user: HRUserDep = None,
    service: DocumentServiceDep = None,
) -> DocumentUploadResponse:
    """Tải lên tài liệu vào Knowledge Base (HR hoặc Nhân viên).

    Nhận file PDF/DOCX/DOC/TXT qua multipart form. File được lưu vào MinIO,
    metadata được ghi vào DB (bảng tương ứng với kb_type), và một ARQ job
    được enqueue để xử lý ingestion bất đồng bộ (parse → chunk → embed → index pgvector).

    Yêu cầu quyền admin.
    """
    # Validate file presence
    if not file.filename:
        raise HTTPException(status_code=400, detail="Tên file không được để trống.")

    mime_type = file.content_type or "application/octet-stream"

    try:
        doc = await service.upload_document(
            file=file.file,
            file_name=file.filename,
            mime_type=mime_type,
            display_name=display_name,
            category=category,
            kb_type=kb_type,
        )
    except KnowledgeBaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return DocumentUploadResponse(
        document_id=doc.id,
        display_name=doc.display_name,
        status=doc.status,
        category=doc.category,
        file_name=doc.file_name,
        file_size=doc.file_size,
        mime_type=doc.mime_type,
        created_at=doc.created_at,
    )


@router.get(
    "/documents",
    response_model=DocumentListResponse,
)
async def list_documents(
    kb_type: str = Query(default="hr", description="Loại knowledge base: hr | employee"),
    page: int = Query(default=1, ge=1, description="Số trang"),
    page_size: int = Query(default=20, ge=1, le=100, description="Số bản ghi mỗi trang"),
    category: str | None = Query(default=None, description="Lọc theo danh mục"),
    status: str | None = Query(
        default=None, description="Lọc theo trạng thái: pending | processing | ready | error"
    ),
    _user: HRUserDep = None,
    service: DocumentServiceDep = None,
) -> DocumentListResponse:
    """Danh sách tài liệu trong Knowledge Base.

    Trả về danh sách documents có phân trang, lọc theo kb_type, category, và status.
    Sắp xếp theo ngày tải lên mới nhất.

    Yêu cầu quyền admin.
    """
    docs, total = await service.list_documents(
        kb_type=kb_type,
        page=page,
        page_size=page_size,
        category=category if category and category != "all" else None,
        status=status if status and status != "all" else None,
    )

    items = [
        DocumentListItem(
            id=doc.id,
            display_name=doc.display_name,
            category=doc.category,
            status=doc.status,
            file_name=doc.file_name,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            chunk_count=doc.chunk_count,
            description=doc.description,
            error_message=doc.error_message,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
        for doc in docs
    ]

    return DocumentListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetailResponse,
)
async def get_document_detail(
    document_id: UUID,
    kb_type: str = Query(default="hr", description="Loại knowledge base: hr | employee"),
    _user: HRUserDep = None,
    service: DocumentServiceDep = None,
) -> DocumentDetailResponse:
    """Chi tiết một tài liệu trong Knowledge Base.

    Trả về metadata đầy đủ bao gồm trạng thái ingestion, số lượng chunk,
    và thông báo lỗi nếu có.

    Yêu cầu quyền admin.
    """
    doc = await service.get_document(document_id, kb_type=kb_type)
    if doc is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")

    return DocumentDetailResponse(
        id=doc.id,
        display_name=doc.display_name,
        category=doc.category,
        status=doc.status,
        file_name=doc.file_name,
        storage_path=doc.storage_path,
        file_size=doc.file_size,
        mime_type=doc.mime_type,
        chunk_count=doc.chunk_count,
        description=doc.description,
        error_message=doc.error_message,
        kb_type=doc.kb_type,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


# ---------------------------------------------------------------------------
# PATCH — Update metadata (Issue #261, KB-05)
# ---------------------------------------------------------------------------


@router.patch(
    "/documents/{document_id}",
    response_model=DocumentUpdateResponse,
)
async def update_document_metadata(
    document_id: UUID,
    body: DocumentUpdateRequest,
    kb_type: str = Query(default="hr", description="Loại knowledge base: hr | employee"),
    _user: HRUserDep = None,
    service: DocumentServiceDep = None,
) -> DocumentUpdateResponse:
    """Cập nhật metadata của tài liệu (không re-index).

    Cho phép sửa tên hiển thị, danh mục, và mô tả mà không cần upload lại file.
    Không ảnh hưởng đến file, chunks, hoặc trạng thái ingestion.

    Yêu cầu quyền admin. Chỉ HR được sửa document thuộc KB họ quản lý.
    """
    doc = await service.update_metadata(
        document_id,
        kb_type=kb_type,
        display_name=body.display_name,
        category=body.category,
        description=body.description,
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")

    return DocumentUpdateResponse(
        id=doc.id,
        display_name=doc.display_name,
        category=doc.category,
        status=doc.status,
        description=doc.description,
        updated_at=doc.updated_at,
    )


# ---------------------------------------------------------------------------
# PUT — Replace file (Issue #261, KB-05)
# ---------------------------------------------------------------------------


@router.put(
    "/documents/{document_id}",
    response_model=DocumentReplaceResponse,
)
async def replace_document_file(
    document_id: UUID,
    file: UploadFile = File(...),
    kb_type: str = Form(default="hr", description="Loại knowledge base: hr | employee"),
    _user: HRUserDep = None,
    service: DocumentServiceDep = None,
) -> DocumentReplaceResponse:
    """Thay thế file của tài liệu và re-index.

    Upload file mới → xóa toàn bộ chunks cũ → lưu file mới lên MinIO →
    reset trạng thái về "pending" → enqueue ARQ job re-index.

    Yêu cầu quyền admin.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Tên file không được để trống.")

    mime_type = file.content_type or "application/octet-stream"

    try:
        doc = await service.replace_file(
            document_id=document_id,
            file=file.file,
            file_name=file.filename,
            mime_type=mime_type,
            kb_type=kb_type,
        )
    except KnowledgeBaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return DocumentReplaceResponse(
        document_id=doc.id,
        display_name=doc.display_name,
        status=doc.status,
        category=doc.category,
        file_name=doc.file_name,
        file_size=doc.file_size,
        mime_type=doc.mime_type,
        created_at=doc.created_at,
    )


# ---------------------------------------------------------------------------
# DELETE — Hard delete (Issue #261, KB-05)
# ---------------------------------------------------------------------------


@router.delete(
    "/documents/{document_id}",
    response_model=MessageResponse,
    status_code=200,
)
async def delete_document(
    document_id: UUID,
    kb_type: str = Query(default="hr", description="Loại knowledge base: hr | employee"),
    _user: HRUserDep = None,
    service: DocumentServiceDep = None,
) -> MessageResponse:
    """Xóa vĩnh viễn tài liệu khỏi Knowledge Base.

    Xóa toàn bộ chunks trong pgvector → xóa file trong MinIO → xóa document row.
    Hard delete, không soft delete. Nếu ARQ job đang chạy cho document bị xóa,
    job sẽ phát hiện document không tồn tại và abort (không tạo chunks mồ côi).

    Yêu cầu quyền admin.
    """
    try:
        await service.delete_document(document_id, kb_type=kb_type)
    except KnowledgeBaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return MessageResponse(
        message="Đã xóa tài liệu thành công.",
        document_id=document_id,
    )
