"""Alembic environment configuration with async SQLAlchemy support."""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
# ``disable_existing_loggers`` defaults to True, which switches off every
# logger already created in the process. Harmless for the deployed image
# (migrations run as their own command before uvicorn starts), but test
# fixtures call ``alembic upgrade head`` in-process, so the default leaves the
# application's loggers dead for the rest of the session.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Import SQLModel metadata so Alembic can detect tables
from sqlmodel import SQLModel  # noqa: E402

from src.modules.assistant.domain.entities import (  # noqa: E402, F401
    AssistantToolConfig,
)
from src.modules.assistant.infrastructure.quality_models import (  # noqa: E402, F401
    AssistantChatSession,
    AssistantFeedbackEvent,
    AssistantToolCallEvent,
)
from src.modules.employee.domain.entities import (  # noqa: E402, F401
    Department,
    Employee,
    EmployeeDocument,
    Position,
)
from src.modules.gmail.domain.entities import (  # noqa: E402, F401
    EmailAttachment,
    EmailMessage,
    GmailAuditLog,
    OutboundEmail,
    SyncCursor,
)
from src.modules.identity.domain.entities import (  # noqa: E402, F401
    OAuthGrant,
    RefreshToken,
    User,
)
from src.modules.knowledge_base.domain.entities import (  # noqa: E402, F401
    KnowledgeBaseChunk,
    KnowledgeBaseDocument,
)
from src.modules.onboarding.domain.entities import (  # noqa: E402, F401
    OnboardingAuditLog,
    OnboardingProcess,
    OnboardingTask,
)
from src.modules.payslip.domain.entities import (  # noqa: E402, F401
    Payslip,
)
from src.modules.recruitment.domain.entities import (  # noqa: E402, F401
    Candidate,
    CVDocument,
    Interview,
    InterviewParticipant,
    JobApplication,
    OrganizationSettings,
    RecruitmentAuditLog,
    RecruitmentInboxItem,
)

target_metadata = SQLModel.metadata

# Override sqlalchemy.url from environment variable if available
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
