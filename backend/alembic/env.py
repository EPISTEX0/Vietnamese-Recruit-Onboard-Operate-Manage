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

# Import SQLModel metadata so Alembic can detect tables.
#
# This used to be a hand-written list of ``from src.modules.… import Entity``
# lines, and it fell behind the source tree twice over: ``employee_requests``
# (holding live rows) and ``attendance_records`` were both absent, so
# autogenerate saw tables with no model and proposed ``op.drop_table()`` for
# them. Discovery is derived from ``src`` now, so a new module cannot be
# forgotten here. ``tests/test_alembic_metadata_complete.py`` guards it.
from sqlmodel import SQLModel  # noqa: E402

from src.shared.model_registry import import_all_entity_modules  # noqa: E402

import_all_entity_modules()

# Tables that exist in the database but deliberately have no model, so
# autogenerate must not propose dropping them. See ``docs/schema-drift-audit.md``.
UNMANAGED_TABLES = {
    # The Gmail label feature (model, repository, service, routes, tests) was
    # removed wholesale in 76e9143, but no migration ever dropped the table it
    # left behind. Dev is empty, yet the feature shipped in 008 and ran from
    # May to July 2026, so deployed databases may still hold rows. Dropping is
    # irreversible; excluding it costs nothing. Remove this entry together with
    # a drop migration once the deployed table is confirmed empty.
    "gmail_label_mappings",
}


def include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Keep unmanaged tables out of autogenerate's comparison."""
    if type_ == "table" and name in UNMANAGED_TABLES:
        return False
    if type_ == "index" and getattr(getattr(obj, "table", None), "name", None) in UNMANAGED_TABLES:
        return False
    return True


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
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

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
