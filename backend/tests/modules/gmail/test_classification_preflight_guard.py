"""``raise_if_classification_not_configured`` measures the real configured-ness check (#352).

Historical import's ``start_import`` calls this before enqueuing a job, so an
import that would otherwise run for minutes and fail only once it reaches
classification instead fails immediately with a 4xx (``GmailImportException``,
raised by the caller from the ``RuntimeError`` this function raises).

The check has to be exactly the one ``build_classification_service`` applies
via ``_build_ai_classifier``, or the two could drift -- a pre-flight that
passes but a real build that still fails, or vice versa. This module proves
that against a real, unconfigured Organization AI Configuration table (no
row, and a row with an empty ``api_key_enc``) and a real, correctly-encrypted
one, mirroring the seeding in ``test_classification_service_builder_wiring.py``.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import delete

from src.modules.gmail.container import raise_if_classification_not_configured
from src.modules.gmail.infrastructure.config import GmailSettings
from src.modules.identity.domain.entities import OrganizationAIConfiguration
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils

pytestmark = pytest.mark.integration

# Matches the key ``tests/env_isolation.py`` pins for AUTH_OAUTH_TOKEN_ENCRYPTION_KEY,
# so ciphertext built here decrypts through the real, unpatched container too.
_TEST_KEY_B64 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


@asynccontextmanager
async def _session_on(url: str) -> AsyncGenerator[AsyncSession]:
    engine = create_async_engine(url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as db_session:
            yield db_session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(postgres_async_url: str) -> AsyncIterator[AsyncSession]:
    """One real session, with the singleton config row cleared before and after."""
    async with _session_on(postgres_async_url) as db_session:
        await db_session.execute(delete(OrganizationAIConfiguration))
        await db_session.commit()
        try:
            yield db_session
        finally:
            await db_session.rollback()
            await db_session.execute(delete(OrganizationAIConfiguration))
            await db_session.commit()


@pytest.fixture
def settings() -> GmailSettings:
    return GmailSettings()


async def test_raises_when_no_organization_ai_config_row(
    session: AsyncSession, settings: GmailSettings
) -> None:
    with pytest.raises(RuntimeError, match="AI classification is not configured"):
        await raise_if_classification_not_configured(session, settings)


async def test_raises_when_config_row_has_no_api_key(
    session: AsyncSession, settings: GmailSettings
) -> None:
    """A config row exists (e.g. the key was revoked) but carries no key."""
    config = OrganizationAIConfiguration(
        provider="openai",
        base_url="http://llm.invalid/v1",
        model="test-model",
        api_key_enc="",
    )
    session.add(config)
    await session.commit()

    with pytest.raises(RuntimeError, match="AI classification is not configured"):
        await raise_if_classification_not_configured(session, settings)


async def test_passes_when_configured(session: AsyncSession, settings: GmailSettings) -> None:
    """Proof by mutation: pin ``_build_ai_classifier`` to always raise and this goes red."""
    config = OrganizationAIConfiguration(
        provider="openai",
        base_url="http://llm.invalid/v1",
        model="test-model",
        api_key_enc=CryptoUtils(_TEST_KEY_B64).encrypt("test-provider-key"),
    )
    session.add(config)
    await session.commit()

    await raise_if_classification_not_configured(session, settings)
