"""Runtime health API — status check for all infrastructure services.

Checks: Redis, PostgreSQL, MinIO, Gmail Worker heartbeat, Onboarding Worker heartbeat.
Mounted under /api/admin/runtime for admin-only access.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.employee.infrastructure.config import EmployeeSettings
from src.modules.identity.api.admin_router import require_system_admin
from src.modules.identity.container import get_db_session
from src.modules.identity.domain.entities import User
from src.modules.identity.infrastructure.config import AuthSettings

runtime_router = APIRouter(prefix="/api/system-admin/runtime", tags=["system-admin-runtime"])

HEARTBEAT_TTL_SECONDS = 600  # must match worker TTL


@dataclass
class ServiceStatus:
    name: str
    status: str  # "healthy" | "unhealthy" | "degraded"
    latency_ms: float | None = None
    detail: str | None = None


@runtime_router.get("/health")
async def runtime_health(
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    """Check runtime health of all infrastructure services.

    Returns overall status and per-service details.
    """
    auth_settings = AuthSettings()  # type: ignore[call-arg]
    services: list[ServiceStatus] = []

    r = redis.from_url(  # type: ignore[no-untyped-call]
        auth_settings.redis_url, decode_responses=True
    )

    # 1. Redis
    try:
        start = time.monotonic()
        await r.ping()
        latency = (time.monotonic() - start) * 1000
        services.append(ServiceStatus(name="redis", status="healthy", latency_ms=round(latency, 1)))
    except Exception as exc:
        services.append(ServiceStatus(name="redis", status="unhealthy", detail=str(exc)))

    # 2. PostgreSQL
    try:
        start = time.monotonic()
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))
        latency = (time.monotonic() - start) * 1000
        services.append(
            ServiceStatus(
                name="postgresql",
                status="healthy",
                latency_ms=round(latency, 1),
            )
        )
    except Exception as exc:
        services.append(ServiceStatus(name="postgresql", status="unhealthy", detail=str(exc)))

    # 3. MinIO
    try:
        start = time.monotonic()
        emp_settings = EmployeeSettings()
        minio_url = f"http://{emp_settings.minio_endpoint}/minio/health/live"
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(minio_url)
        latency = (time.monotonic() - start) * 1000
        if resp.status_code == 200:
            services.append(
                ServiceStatus(
                    name="minio",
                    status="healthy",
                    latency_ms=round(latency, 1),
                )
            )
        else:
            services.append(
                ServiceStatus(
                    name="minio",
                    status="degraded",
                    detail=f"HTTP {resp.status_code}",
                )
            )
    except Exception as exc:
        services.append(ServiceStatus(name="minio", status="unhealthy", detail=str(exc)))

    # 4. Gmail Worker heartbeat
    try:
        ts = await r.get("runtime:heartbeat:gmail-worker")
        if ts:
            services.append(
                ServiceStatus(
                    name="gmail-worker",
                    status="healthy",
                    detail=f"last beat: {ts}",
                )
            )
        else:
            services.append(
                ServiceStatus(
                    name="gmail-worker",
                    status="unhealthy",
                    detail="no heartbeat",
                )
            )
    except Exception as exc:
        services.append(ServiceStatus(name="gmail-worker", status="unhealthy", detail=str(exc)))

    # 5. Onboarding Worker heartbeat
    try:
        ts = await r.get("runtime:heartbeat:onboarding-worker")
        if ts:
            services.append(
                ServiceStatus(
                    name="onboarding-worker",
                    status="healthy",
                    detail=f"last beat: {ts}",
                )
            )
        else:
            services.append(
                ServiceStatus(
                    name="onboarding-worker",
                    status="unhealthy",
                    detail="no heartbeat",
                )
            )
    except Exception as exc:
        services.append(
            ServiceStatus(
                name="onboarding-worker",
                status="unhealthy",
                detail=str(exc),
            )
        )

    await r.aclose()

    overall = "healthy"
    if any(s.status == "unhealthy" for s in services):
        overall = "unhealthy"
    elif any(s.status == "degraded" for s in services):
        overall = "degraded"

    return {
        "status": overall,
        "services": [
            {
                "name": s.name,
                "status": s.status,
                "latency_ms": s.latency_ms,
                "detail": s.detail,
            }
            for s in services
        ],
    }
