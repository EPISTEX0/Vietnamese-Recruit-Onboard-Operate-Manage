"""Regression test for the worker's ``EmailSyncService`` wiring (#361).

``EmailSyncService`` used to fall back to reconstructing its own
``OrganizationGoogleConnectionRepository`` from ``email_repo.session`` when no
``connection_repo`` was injected -- the exact reach-through #361 removes.
``poll_gmail_emails`` (``src/modules/gmail/worker.py``) already builds its own
``connection_repo`` locally, to check the connection status before deciding
whether to poll, but never threaded that instance (or its ``session``) into
``EmailSyncService`` -- it relied on the now-removed fallback instead. Nothing
exercised ``poll_gmail_emails`` end-to-end to catch that, so this test is that
exercise: it fails loudly (``TypeError``, missing required argument) if the
worker ever again omits ``session``/``connection_repo`` from the service it
builds, rather than the silent double-construction the fallback used to mask.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.gmail import worker as gmail_worker


@pytest.mark.asyncio
async def test_poll_gmail_emails_threads_session_and_connection_repo_into_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    connection = MagicMock()
    connection.status = "connected"
    connection.connected_by_user_id = user_id

    fake_connection_repo = AsyncMock()
    fake_connection_repo.get_singleton = AsyncMock(return_value=connection)
    monkeypatch.setattr(
        gmail_worker,
        "OrganizationGoogleConnectionRepository",
        MagicMock(return_value=fake_connection_repo),
    )

    fake_session = AsyncMock()

    @asynccontextmanager
    async def _session_cm() -> AsyncGenerator[Any]:
        yield fake_session

    captured: dict[str, Any] = {}

    class _StubEmailSyncService:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def poll_emails(self, _user_id: Any) -> int:
            return 0

    monkeypatch.setattr(gmail_worker, "EmailSyncService", _StubEmailSyncService)

    ctx = {
        "session_maker": lambda: _session_cm(),
        "redis_client": AsyncMock(),
        "http_client": MagicMock(),
        "crypto": MagicMock(),
        "quota_tracker": MagicMock(),
        "auth_settings": MagicMock(google_client_id="cid", google_client_secret="secret"),
        "gmail_settings": MagicMock(),
    }

    await gmail_worker.poll_gmail_emails(ctx)

    assert captured["session"] is fake_session, (
        "poll_gmail_emails must pass its own session into EmailSyncService"
    )
    assert captured["connection_repo"] is fake_connection_repo, (
        "poll_gmail_emails must pass its own connection_repo into EmailSyncService "
        "-- EmailSyncService no longer reconstructs one from email_repo.session"
    )
