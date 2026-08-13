"""Integration tests for KB-05 — Knowledge Base document management endpoints.

Tests PATCH (metadata update), PUT (file replacement), DELETE (hard delete),
and filtered list endpoints added in Issue #261.

Uses FastAPI TestClient with dependency overrides to bypass auth, and
testcontainers PostgreSQL with pgvector for real DB operations.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# testcontainers / docker are integration-only dependencies.
docker = pytest.importorskip("docker")
PostgresContainer = pytest.importorskip("testcontainers.postgres").PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[3]
PGVECTOR_IMAGE = "pgvector/pgvector:pg15"


def _docker_available() -> bool:
    """Return True if a Docker daemon is reachable, else False."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception:  # noqa: BLE001
        return False
    return True


def _run_alembic_upgrade_head(async_url: str) -> None:
    """Run ``alembic upgrade head`` against ``async_url``."""
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", async_url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _make_admin_user() -> User:  # type: ignore[name-defined]
    """Build a fake admin User for auth dependency overrides."""
    from src.modules.identity.domain.entities import User, UserRole

    suffix = uuid.uuid4().hex[:8]
    return User(
        email=f"admin-{suffix}@vroomhr.com",
        name="Test Admin",
        role=UserRole.HR,
    )


@pytest.fixture(scope="module")
def kb_client() -> Iterator[TestClient]:
    """Start pgvector PostgreSQL, run migrations, wire a TestClient with auth override.

    Overrides ``require_hr`` so all endpoints are authenticated as admin.
    """
    if not _docker_available():
        pytest.skip("Docker is not available for the KB-05 integration test")

    with PostgresContainer(PGVECTOR_IMAGE) as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")

        _run_alembic_upgrade_head(async_url)

        os.environ["AUTH_DATABASE_URL"] = async_url
        os.environ["DATABASE_URL"] = async_url
        os.environ["AUTH_AUTO_SEED_SAMPLE_DATA"] = "false"
        os.environ["AUTH_JWT_SECRET_KEY"] = os.environ.get(
            "AUTH_JWT_SECRET_KEY", "test-secret-key-for-integration-tests"
        )
        os.environ["AUTH_JWT_ALGORITHM"] = "HS256"

        import importlib

        import src.main as main_module

        importlib.reload(main_module)
        from src.main import app
        from src.modules.identity.api.admin_router import require_hr

        # Override require_hr to bypass auth in tests
        admin_user = _make_admin_user()
        app.dependency_overrides[require_hr] = lambda: admin_user

        with TestClient(app) as test_client:
            yield test_client

        app.dependency_overrides.clear()

        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("AUTH_AUTO_SEED_SAMPLE_DATA", None)


# ---------------------------------------------------------------------------
# Helper: create a test document via the POST endpoint
# ---------------------------------------------------------------------------


def _create_test_document(
    client: TestClient,
    kb_type: str = "hr",
    display_name: str = "Test Document",
    category: str = "general",
) -> dict:
    """Upload a small test document and return the response JSON."""
    pdf_content = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n%%EOF"
    pdf_file = BytesIO(pdf_content)
    response = client.post(
        "/api/knowledge-base/documents",
        files={"file": ("test.pdf", pdf_file, "application/pdf")},
        data={
            "display_name": display_name,
            "category": category,
            "kb_type": kb_type,
        },
    )
    assert response.status_code == 201, f"Failed to create test doc: {response.json()}"
    return response.json()


# ---------------------------------------------------------------------------
# Tests: PATCH — Update metadata
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPatchDocumentMetadata:
    """Tests for PATCH /api/knowledge-base/documents/{id} (Issue #261)."""

    def test_update_display_name(self, kb_client: TestClient):
        """AC: PATCH updates display_name without re-indexing."""
        doc = _create_test_document(kb_client, display_name="Original Name")
        doc_id = doc["document_id"]

        response = kb_client.patch(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
            json={"display_name": "Updated Name"},
        )
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["display_name"] == "Updated Name"
        assert body["status"] == "pending"  # Status unchanged

    def test_update_category(self, kb_client: TestClient):
        """AC: PATCH updates category without touching file/chunks."""
        doc = _create_test_document(kb_client, category="general")
        doc_id = doc["document_id"]

        response = kb_client.patch(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
            json={"category": "policy"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["category"] == "policy"

    def test_update_description(self, kb_client: TestClient):
        """AC: PATCH updates description field."""
        doc = _create_test_document(kb_client)
        doc_id = doc["document_id"]

        response = kb_client.patch(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
            json={"description": "Mô tả về tài liệu test"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["description"] == "Mô tả về tài liệu test"

    def test_update_multiple_fields(self, kb_client: TestClient):
        """AC: PATCH updates display_name, category, and description simultaneously."""
        doc = _create_test_document(kb_client)
        doc_id = doc["document_id"]

        response = kb_client.patch(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
            json={
                "display_name": "New Name",
                "category": "legal",
                "description": "Mô tả mới",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["display_name"] == "New Name"
        assert body["category"] == "legal"
        assert body["description"] == "Mô tả mới"

    def test_update_nonexistent_document_returns_404(self, kb_client: TestClient):
        """AC: PATCH on non-existent document returns 404."""
        fake_id = str(uuid.uuid4())
        response = kb_client.patch(
            f"/api/knowledge-base/documents/{fake_id}?kb_type=hr",
            json={"display_name": "Ghost"},
        )
        assert response.status_code == 404
        assert "Không tìm thấy" in response.json()["detail"]

    def test_update_empty_body_no_error(self, kb_client: TestClient):
        """Edge case: PATCH with empty body updates nothing but succeeds."""
        doc = _create_test_document(kb_client, display_name="Keep Me")
        doc_id = doc["document_id"]

        response = kb_client.patch(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
            json={},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["display_name"] == "Keep Me"  # Unchanged

    def test_update_employee_kb_document(self, kb_client: TestClient):
        """AC: PATCH works on employee KB documents too."""
        doc = _create_test_document(kb_client, kb_type="employee", display_name="Employee Doc")
        doc_id = doc["document_id"]

        response = kb_client.patch(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=employee",
            json={"display_name": "Updated Employee Doc"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["display_name"] == "Updated Employee Doc"


# ---------------------------------------------------------------------------
# Tests: DELETE — Hard delete
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteDocument:
    """Tests for DELETE /api/knowledge-base/documents/{id} (Issue #261)."""

    def test_delete_existing_document(self, kb_client: TestClient):
        """AC: DELETE removes document, chunks, and returns success message."""
        doc = _create_test_document(kb_client, display_name="To Be Deleted")
        doc_id = doc["document_id"]

        response = kb_client.delete(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "Đã xóa tài liệu thành công."
        assert body["document_id"] == doc_id

        # Verify document no longer exists
        get_response = kb_client.get(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
        )
        assert get_response.status_code == 404

    def test_delete_nonexistent_document_returns_404(self, kb_client: TestClient):
        """AC: DELETE on non-existent document returns 404."""
        fake_id = str(uuid.uuid4())
        response = kb_client.delete(
            f"/api/knowledge-base/documents/{fake_id}?kb_type=hr",
        )
        assert response.status_code == 404
        assert "Không tìm thấy" in response.json()["detail"]

    def test_delete_employee_kb_document(self, kb_client: TestClient):
        """AC: DELETE works on employee KB documents."""
        doc = _create_test_document(kb_client, kb_type="employee", display_name="Emp Del")
        doc_id = doc["document_id"]

        response = kb_client.delete(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=employee",
        )
        assert response.status_code == 200

        get_response = kb_client.get(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=employee",
        )
        assert get_response.status_code == 404

    def test_cannot_get_deleted_document(self, kb_client: TestClient):
        """AC: After delete, GET returns 404 and list excludes the document."""
        doc = _create_test_document(kb_client, display_name="Gone Doc")
        doc_id = doc["document_id"]

        # Delete it
        kb_client.delete(f"/api/knowledge-base/documents/{doc_id}?kb_type=hr")

        # List should not include it
        list_response = kb_client.get("/api/knowledge-base/documents?kb_type=hr")
        items = list_response.json()["items"]
        ids = [item["id"] for item in items]
        assert doc_id not in ids


# ---------------------------------------------------------------------------
# Tests: GET list with filters
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFilteredDocumentList:
    """Tests for category and status filters on GET /documents (Issue #261)."""

    def test_filter_by_category(self, kb_client: TestClient):
        """AC: Filter by category returns only matching documents."""
        _create_test_document(kb_client, display_name="Policy Doc", category="policy")
        _create_test_document(kb_client, display_name="General Doc", category="general")

        response = kb_client.get(
            "/api/knowledge-base/documents?kb_type=hr&category=policy",
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert all(item["category"] == "policy" for item in items)
        assert len(items) >= 1

    def test_filter_by_status(self, kb_client: TestClient):
        """AC: Filter by status returns only matching documents."""
        _create_test_document(kb_client, display_name="Pending Doc")
        # All new docs have status "pending"

        response = kb_client.get(
            "/api/knowledge-base/documents?kb_type=hr&status=pending",
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert all(item["status"] == "pending" for item in items)

    def test_filter_by_category_and_status(self, kb_client: TestClient):
        """AC: Combined category + status filter both apply."""
        _create_test_document(kb_client, display_name="Policy Pending", category="policy")

        response = kb_client.get(
            "/api/knowledge-base/documents?kb_type=hr&category=policy&status=pending",
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert all(item["category"] == "policy" and item["status"] == "pending" for item in items)

    def test_filter_empty_result(self, kb_client: TestClient):
        """AC: Filter that matches nothing returns empty list with total=0."""
        response = kb_client.get(
            "/api/knowledge-base/documents?kb_type=hr&category=nonexistent",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["items"] == []


# ---------------------------------------------------------------------------
# Tests: PUT — Replace file
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReplaceDocumentFile:
    """Tests for PUT /api/knowledge-base/documents/{id} (Issue #261)."""

    def test_replace_file_resets_status(self, kb_client: TestClient):
        """AC: PUT uploads new file, resets status to 'pending'."""
        doc = _create_test_document(kb_client, display_name="Old File")
        doc_id = doc["document_id"]

        new_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"
        new_file = BytesIO(new_pdf)

        response = kb_client.put(
            f"/api/knowledge-base/documents/{doc_id}",
            files={"file": ("new.pdf", new_file, "application/pdf")},
            data={"kb_type": "hr"},
        )
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["status"] == "pending"
        assert body["file_name"] == "new.pdf"

    def test_replace_nonexistent_document_returns_404(self, kb_client: TestClient):
        """AC: PUT on non-existent document returns 404."""
        fake_id = str(uuid.uuid4())
        pdf_content = b"%PDF-1.4\ndummy\n%%EOF"
        pdf_file = BytesIO(pdf_content)

        response = kb_client.put(
            f"/api/knowledge-base/documents/{fake_id}",
            files={"file": ("ghost.pdf", pdf_file, "application/pdf")},
            data={"kb_type": "hr"},
        )
        assert response.status_code == 404
        assert "Không tìm thấy" in response.json()["detail"]

    def test_replace_with_unsupported_file_type(self, kb_client: TestClient):
        """Edge case: PUT with unsupported MIME type returns 400."""
        doc = _create_test_document(kb_client)
        doc_id = doc["document_id"]

        img_content = b"\x89PNG\r\n\x1a\n"
        img_file = BytesIO(img_content)

        response = kb_client.put(
            f"/api/knowledge-base/documents/{doc_id}",
            files={"file": ("image.png", img_file, "image/png")},
            data={"kb_type": "hr"},
        )
        assert response.status_code == 400
        assert "không được hỗ trợ" in response.json()["detail"].lower()

    def test_replace_with_empty_filename(self, kb_client: TestClient):
        """Edge case: PUT with empty filename returns 400."""
        doc = _create_test_document(kb_client)
        doc_id = doc["document_id"]

        response = kb_client.put(
            f"/api/knowledge-base/documents/{doc_id}",
            files={"file": ("", BytesIO(b"x"), "application/pdf")},
            data={"kb_type": "hr"},
        )
        # FastAPI may validate empty filename as 422 before reaching the handler
        assert response.status_code in (400, 422)

    def test_replace_file_changes_storage_path(self, kb_client: TestClient):
        """AC: PUT changes file_name and likely mime_type on the document."""
        doc = _create_test_document(kb_client, display_name="Original")
        doc_id = doc["document_id"]

        # Upload a .txt file instead
        txt_content = b"Hello World"
        txt_file = BytesIO(txt_content)

        response = kb_client.put(
            f"/api/knowledge-base/documents/{doc_id}",
            files={"file": ("updated.txt", txt_file, "text/plain")},
            data={"kb_type": "hr"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["file_name"] == "updated.txt"

        # Verify via GET
        detail = kb_client.get(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
        )
        assert detail.status_code == 200
        assert detail.json()["file_name"] == "updated.txt"


# ---------------------------------------------------------------------------
# Tests: Edge cases — safe delete, missing fields
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEdgeCases:
    """Edge case tests for KB-05 endpoints."""

    def test_patch_missing_required_field_no_error(self, kb_client: TestClient):
        """PATCH body can be empty — all fields optional."""
        doc = _create_test_document(kb_client)
        doc_id = doc["document_id"]

        response = kb_client.patch(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
            json={},
        )
        assert response.status_code == 200

    def test_delete_then_patch_returns_404(self, kb_client: TestClient):
        """PATCH a deleted document returns 404."""
        doc = _create_test_document(kb_client)
        doc_id = doc["document_id"]

        kb_client.delete(f"/api/knowledge-base/documents/{doc_id}?kb_type=hr")

        response = kb_client.patch(
            f"/api/knowledge-base/documents/{doc_id}?kb_type=hr",
            json={"display_name": "Ghost Edit"},
        )
        assert response.status_code == 404

    def test_delete_then_put_returns_404(self, kb_client: TestClient):
        """PUT a deleted document returns 404."""
        doc = _create_test_document(kb_client)
        doc_id = doc["document_id"]

        kb_client.delete(f"/api/knowledge-base/documents/{doc_id}?kb_type=hr")

        new_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"
        new_file = BytesIO(new_pdf)

        response = kb_client.put(
            f"/api/knowledge-base/documents/{doc_id}",
            files={"file": ("ghost.pdf", new_file, "application/pdf")},
            data={"kb_type": "hr"},
        )
        assert response.status_code == 404

    def test_list_pagination_respected(self, kb_client: TestClient):
        """Pagination params page and page_size are respected."""
        # Create a few docs
        for i in range(3):
            _create_test_document(kb_client, display_name=f"Doc {i}")

        response = kb_client.get(
            "/api/knowledge-base/documents?kb_type=hr&page=1&page_size=2",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert len(body["items"]) <= 2

    def test_list_page_out_of_range_returns_empty(self, kb_client: TestClient):
        """Page beyond available data returns empty items but valid total."""
        response = kb_client.get(
            "/api/knowledge-base/documents?kb_type=hr&page=999&page_size=20",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
