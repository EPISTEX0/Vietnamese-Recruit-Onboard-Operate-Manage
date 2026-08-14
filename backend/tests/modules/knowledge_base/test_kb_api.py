"""Integration tests for Knowledge Base API endpoints.

Tests the API endpoints against a testcontainers PostgreSQL instance
with a FastAPI TestClient. These tests require Docker to be running.

For unit-level tests of the chunking/parsing logic, see test_kb_worker.py.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.postgres_support import make_postgres_container

# testcontainers / docker are integration-only dependencies.
docker = pytest.importorskip("docker")

BACKEND_DIR = Path(__file__).resolve().parents[3]


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


@pytest.fixture(scope="module")
def test_app() -> Iterator[TestClient]:
    """Start pgvector PostgreSQL, apply migrations, wire a FastAPI TestClient."""
    if not _docker_available():
        pytest.skip("Docker is not available for the KB API integration test")

    with make_postgres_container() as postgres:
        sync_url = postgres.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")

        _run_alembic_upgrade_head(async_url)

        # Override DATABASE_URL so the app connects to testcontainers
        os.environ["DATABASE_URL"] = async_url
        # Disable auto-seed to avoid test pollution
        os.environ["AUTH_AUTO_SEED_SAMPLE_DATA"] = "false"
        # Use a dummy JWT secret for tests
        os.environ["AUTH_JWT_SECRET_KEY"] = os.environ.get(
            "AUTH_JWT_SECRET_KEY", "test-secret-key-for-integration-tests"
        )
        os.environ["AUTH_JWT_ALGORITHM"] = "HS256"

        import importlib

        import src.main as main_module

        importlib.reload(main_module)
        from src.main import app

        client = TestClient(app)
        yield client

        # Cleanup
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("AUTH_AUTO_SEED_SAMPLE_DATA", None)


@pytest.mark.integration
class TestKnowledgeBaseAPIMigration:
    """Verify the schema was created correctly by migrations."""

    def test_tables_exist(self, test_app: TestClient):
        """AC: Migration creates hr_knowledge_base_documents and hr_knowledge_base_chunks tables."""
        from sqlalchemy import create_engine, inspect

        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            pytest.skip("DATABASE_URL not set")

        sync_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        engine = create_engine(sync_url)
        inspector = inspect(engine)

        tables = inspector.get_table_names()
        assert "hr_knowledge_base_documents" in tables, (
            f"Table hr_knowledge_base_documents not found in {tables}"
        )
        assert "hr_knowledge_base_chunks" in tables, (
            f"Table hr_knowledge_base_chunks not found in {tables}"
        )

    def test_employee_tables_exist(self, test_app: TestClient):
        """AC: Migration 079 creates employee_knowledge_base_documents and
        employee_knowledge_base_chunks tables."""
        from sqlalchemy import create_engine, inspect

        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            pytest.skip("DATABASE_URL not set")

        sync_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        engine = create_engine(sync_url)
        inspector = inspect(engine)

        tables = inspector.get_table_names()
        assert "employee_knowledge_base_documents" in tables, (
            f"Table employee_knowledge_base_documents not found in {tables}"
        )
        assert "employee_knowledge_base_chunks" in tables, (
            f"Table employee_knowledge_base_chunks not found in {tables}"
        )

    def test_vector_extension_enabled(self, test_app: TestClient):
        """AC: pgvector extension was already enabled by migration 077."""
        from sqlalchemy import create_engine, text

        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            pytest.skip("DATABASE_URL not set")

        sync_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))
            row = result.fetchone()
            assert row is not None, "pgvector extension 'vector' is not enabled"


@pytest.mark.integration
class TestKnowledgeBaseAPIHealth:
    """Verify the API health and basic endpoint structure."""

    def test_health_endpoint(self, test_app: TestClient):
        """The /health endpoint should respond 200."""
        response = test_app.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_router_registered(self, test_app: TestClient):
        """The knowledge-base router is registered (returns 401 not 404 when unauthenticated)."""
        response = test_app.get("/api/knowledge-base/documents?kb_type=hr")
        # Without auth, we expect 401 (not 404 which would mean router not registered)
        assert response.status_code in (200, 401, 403), (
            f"Expected 200/401/403, got {response.status_code}. "
            f"404 would mean router not registered."
        )

    def test_employee_kb_router_registered(self, test_app: TestClient):
        """The knowledge-base router accepts kb_type=employee
        (returns 401 not 404 when unauthenticated)."""
        response = test_app.get("/api/knowledge-base/documents?kb_type=employee")
        assert response.status_code in (200, 401, 403), (
            f"Expected 200/401/403, got {response.status_code}. "
            f"404 would mean router or kb_type=employee not supported."
        )
