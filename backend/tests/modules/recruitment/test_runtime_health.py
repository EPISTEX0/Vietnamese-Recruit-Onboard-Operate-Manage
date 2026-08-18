"""Tests for the runtime health API endpoint.

Verifies the GET /api/system-admin/runtime/health endpoint returns correct status
for all infrastructure services (Redis, PostgreSQL, MinIO, Gmail Worker,
Onboarding Worker) and handles unhealthy states properly.

Uses ``app.dependency_overrides`` to bypass auth and DB dependencies,
matching the pattern used by other endpoint tests in this codebase.

Requirements: runtime backbone wiring
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_overrides():
    """Clear dependency overrides after every test."""
    yield
    app.dependency_overrides.clear()


def _make_admin() -> User:
    return User(
        id=uuid4(),
        email="admin@test.com",
        role=UserRole.SYSTEM_ADMIN,
        is_active=True,
    )


def _make_db_session() -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = 1
    session.execute = AsyncMock(return_value=result)
    return session


# ─── Helper ────────────────────────────────────────────────────────────


async def _call_health(
    *,
    redis_get_return: str | None = "1717800000.0",
    redis_ping_side_effect: BaseException | None = None,
    minio_status: int = 200,
) -> dict:
    """Set up DI overrides and patched modules, then GET /api/system-admin/runtime/health."""
    admin = _make_admin()
    db_session = _make_db_session()
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_db_session] = lambda: db_session

    mock_redis_client = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value=redis_get_return)
    if redis_ping_side_effect:
        mock_redis_client.ping = AsyncMock(side_effect=redis_ping_side_effect)
    else:
        mock_redis_client.ping = AsyncMock()
    mock_redis_client.aclose = AsyncMock()

    with (
        patch(
            "src.modules.recruitment.api.runtime_router.redis",
        ) as mock_redis_mod,
        patch(
            "src.modules.recruitment.api.runtime_router.AuthSettings",
        ) as mock_auth_cls,
        patch(
            "src.modules.recruitment.api.runtime_router.EmployeeSettings",
        ) as mock_emp_cls,
        patch(
            "src.modules.recruitment.api.runtime_router.httpx",
        ) as mock_httpx,
    ):
        mock_redis_mod.from_url.return_value = mock_redis_client

        auth_s = MagicMock()
        auth_s.redis_url = "redis://localhost:6379/0"
        mock_auth_cls.return_value = auth_s

        emp_s = MagicMock()
        emp_s.minio_endpoint = "localhost:9000"
        mock_emp_cls.return_value = emp_s

        mock_response = MagicMock()
        mock_response.status_code = minio_status
        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_http_client

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/system-admin/runtime/health")
        return resp.json()


# ─── Tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_returns_healthy_when_all_ok():
    """All services healthy → overall status is 'healthy'."""
    data = await _call_health()
    assert data["status"] == "healthy"
    assert len(data["services"]) == 5
    names = [s["name"] for s in data["services"]]
    assert names == ["redis", "postgresql", "minio", "gmail-worker", "onboarding-worker"]
    for svc in data["services"]:
        assert svc["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_returns_unhealthy_when_redis_down():
    """Redis ping fails → overall status is 'unhealthy'."""
    data = await _call_health(redis_ping_side_effect=Exception("Connection refused"))
    assert data["status"] == "unhealthy"
    redis_svc = next(s for s in data["services"] if s["name"] == "redis")
    assert redis_svc["status"] == "unhealthy"
    assert "Connection refused" in redis_svc["detail"]


@pytest.mark.asyncio
async def test_health_logs_warning_server_side_when_redis_down(
    caplog: pytest.LogCaptureFixture,
):
    """A service failure must leave a server-side trace to correlate against.

    The client response already carries ``detail``, but nothing previously
    recorded the failure anywhere queryable after that single response.
    """
    logger_name = "src.modules.recruitment.api.runtime_router"
    with caplog.at_level(logging.WARNING, logger=logger_name):
        await _call_health(redis_ping_side_effect=Exception("Connection refused"))
    matching = [r for r in caplog.records if r.name == logger_name]
    assert len(matching) == 1
    record = matching[0]
    assert record.levelno == logging.WARNING
    assert "redis" in record.getMessage()
    assert "Connection refused" in record.getMessage()


@pytest.mark.asyncio
async def test_health_returns_unhealthy_when_worker_no_heartbeat():
    """Worker heartbeat missing → overall status is 'unhealthy'."""
    data = await _call_health(redis_get_return=None)
    assert data["status"] == "unhealthy"
    gmail = next(s for s in data["services"] if s["name"] == "gmail-worker")
    onb = next(s for s in data["services"] if s["name"] == "onboarding-worker")
    assert gmail["status"] == "unhealthy"
    assert gmail["detail"] == "no heartbeat"
    assert onb["status"] == "unhealthy"
    assert onb["detail"] == "no heartbeat"


@pytest.mark.asyncio
async def test_health_worker_healthy_when_heartbeat_present():
    """Worker heartbeat present → worker status is 'healthy'."""
    data = await _call_health(redis_get_return="1717800000.0")
    gmail = next(s for s in data["services"] if s["name"] == "gmail-worker")
    onb = next(s for s in data["services"] if s["name"] == "onboarding-worker")
    assert gmail["status"] == "healthy"
    assert "last beat" in gmail["detail"]
    assert onb["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_minio_degraded_on_non_200():
    """MinIO returns non-200 → status is 'degraded'."""
    data = await _call_health(minio_status=503)
    minio = next(s for s in data["services"] if s["name"] == "minio")
    assert minio["status"] == "degraded"
    assert "503" in minio["detail"]
