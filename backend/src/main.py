"""Vroom HR Backend - FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Load .env file before any settings are instantiated.
load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from src.modules.assistant.api.employee_router import employee_assistant_router  # noqa: E402
from src.modules.assistant.api.error_handler import (  # noqa: E402
    register_assistant_error_handlers,
)
from src.modules.assistant.api.router import router as assistant_router  # noqa: E402
from src.modules.attendance.api.error_handler import (  # noqa: E402
    register_attendance_error_handlers,
)
from src.modules.attendance.api.router import attendance_router  # noqa: E402
from src.modules.employee.api.error_handler import (  # noqa: E402
    register_employee_error_handlers,
)
from src.modules.employee.api.router import router as employee_router  # noqa: E402
from src.modules.employee_request.api.admin_router import (
    admin_employee_request_router,  # noqa: E402
)
from src.modules.employee_request.api.error_handler import (  # noqa: E402
    register_employee_request_error_handlers,
)
from src.modules.employee_request.api.router import employee_request_router  # noqa: E402
from src.modules.gmail.api.error_handler import (  # noqa: E402
    register_gmail_error_handlers,
)
from src.modules.gmail.api.outbound_router import router as outbound_email_router
from src.modules.gmail.api.router import router as gmail_router  # noqa: E402
from src.modules.identity.api.admin_router import admin_router  # noqa: E402
from src.modules.identity.api.error_handler import (  # noqa: E402
    register_auth_error_handlers,
)
from src.modules.identity.api.hr_ai_config_router import hr_ai_config_router  # noqa: E402
from src.modules.identity.api.router import router as auth_router  # noqa: E402
from src.modules.identity.container import get_settings  # noqa: E402
from src.modules.knowledge_base.api.router import router as kb_router  # noqa: E402
from src.modules.onboarding.api.error_handler import (  # noqa: E402
    register_onboarding_error_handlers,
)
from src.modules.onboarding.api.router import onboarding_router  # noqa: E402
from src.modules.payslip.api.admin_router import admin_payslip_router  # noqa: E402
from src.modules.payslip.api.employee_router import employee_payslip_router
from src.modules.payslip.api.error_handler import register_payslip_error_handlers  # noqa: E402
from src.modules.recruitment.api.candidate_router import candidate_router  # noqa: E402
from src.modules.recruitment.api.conflict_router import conflict_router  # noqa: E402
from src.modules.recruitment.api.cv_review_router import cv_review_router  # noqa: E402
from src.modules.recruitment.api.error_handler import (  # noqa: E402
    register_recruitment_error_handlers,
)
from src.modules.recruitment.api.evaluation_router import router as evaluation_router
from src.modules.recruitment.api.inbox_router import router as inbox_router
from src.modules.recruitment.api.job_application_router import router as job_application_router
from src.modules.recruitment.api.job_opening_router import job_opening_router  # noqa: E402
from src.modules.recruitment.api.metrics_router import metrics_router  # noqa: E402
from src.modules.recruitment.api.runtime_router import runtime_router

logger = logging.getLogger(__name__)


async def _bootstrap_super_admin() -> None:
    """Bootstrap the super admin user at application startup.

    If AUTH_SUPER_ADMIN_EMAIL is configured, ensures that user has the admin
    role. If not configured and no admin exists, logs a warning.
    """
    from sqlalchemy import func
    from sqlmodel import select

    from src.modules.identity.application.role_service import RoleService
    from src.modules.identity.container import _get_async_session_maker, get_settings
    from src.modules.identity.domain.entities import User, UserRole

    settings = get_settings()
    super_admin_email = settings.super_admin_email

    session_maker = _get_async_session_maker()
    async with session_maker() as session:
        if super_admin_email:
            role_service = RoleService(session=session, super_admin_email=super_admin_email)
            await role_service.ensure_super_admin(super_admin_email)
            await session.commit()
            logger.info("Super admin bootstrap completed for '%s'.", super_admin_email)
        else:
            # Check if any system_admin exists in the database.
            statement = (
                select(func.count()).select_from(User).where(User.role == UserRole.SYSTEM_ADMIN)
            )
            result = await session.execute(statement)
            admin_count = result.scalar_one()
            if admin_count == 0:
                logger.warning(
                    "No AUTH_SUPER_ADMIN_EMAIL configured and no admin user exists. "
                    "Set AUTH_SUPER_ADMIN_EMAIL environment variable to bootstrap "
                    "the first administrator."
                )


async def _seed_assistant_tool_configs() -> None:
    """Seed default assistant tool configs at startup.

    Ensures all TOOL_DEFINITIONS have a row in assistant_tool_config
    with enabled=True. Only inserts missing rows -- never overwrites
    existing admin toggles.
    """
    from sqlmodel import select

    from src.modules.assistant.domain.entities import AssistantToolConfig
    from src.modules.assistant.domain.tools import TOOL_DEFINITIONS
    from src.modules.identity.container import _get_async_session_maker

    session_maker = _get_async_session_maker()
    async with session_maker() as session:
        result = await session.execute(select(AssistantToolConfig))
        existing = {row.tool_name for row in result.scalars().all()}
        to_insert = [
            AssistantToolConfig(tool_name=t.name, enabled=True)
            for t in TOOL_DEFINITIONS
            if t.name not in existing
        ]
        if to_insert:
            session.add_all(to_insert)
            await session.commit()
            logger.info(
                "Seeded %d assistant tool configs: %s",
                len(to_insert),
                [t.tool_name for t in to_insert],
            )
        else:
            logger.info("Assistant tool configs already present -- skipping seed.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup and shutdown events."""
    # Startup
    await _bootstrap_super_admin()
    await _seed_assistant_tool_configs()
    yield
    # Shutdown (nothing to clean up currently)


app = FastAPI(
    title="Vroom HR",
    description="Vietnamese Recruit-Onboard-Operate-Manage platform",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure CORS for cross-origin frontend access (prod and local dev).
_cors_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", _cors_settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register module routers.
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(hr_ai_config_router)
app.include_router(employee_router)
app.include_router(gmail_router)
app.include_router(outbound_email_router)
app.include_router(candidate_router)
app.include_router(conflict_router)
app.include_router(cv_review_router)
app.include_router(metrics_router)
app.include_router(onboarding_router)
app.include_router(attendance_router)
app.include_router(inbox_router)
app.include_router(evaluation_router)
app.include_router(job_application_router)
app.include_router(job_opening_router)
app.include_router(runtime_router)
app.include_router(assistant_router)
app.include_router(employee_assistant_router)
app.include_router(employee_request_router)
app.include_router(admin_employee_request_router)
app.include_router(employee_payslip_router)

app.include_router(admin_payslip_router)
app.include_router(kb_router)

# Register exception handlers.
register_auth_error_handlers(app)
register_employee_error_handlers(app)
register_gmail_error_handlers(app)
register_recruitment_error_handlers(app)
register_onboarding_error_handlers(app)
register_attendance_error_handlers(app)
register_assistant_error_handlers(app)
register_payslip_error_handlers(app)
register_employee_request_error_handlers(app)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint for Docker healthcheck."""
    return {"status": "ok"}
