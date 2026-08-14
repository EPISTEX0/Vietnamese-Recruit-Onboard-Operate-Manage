"""The knowledge base must not re-dial a dead Redis on every request.

``get_document_service`` awaits ``get_arq_redis()`` for every knowledge-base
endpoint, including the read-only ones. The pool is cached on success, but a
failure was not remembered at all, so while Redis was down each request paid
the full arq connect budget -- about five seconds against a closed port -- and
logged a traceback. A queue outage therefore became an API outage: requests
pile up on a blocking dial instead of degrading to "not enqueued".

These tests pin the recovery shape: remember the failure briefly, keep serving,
and try again once the cooldown expires.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.modules.knowledge_base import container


@pytest.fixture(autouse=True)
def _clean_arq_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test a container with no pool and no remembered failure."""
    monkeypatch.setattr(container, "_arq_pool", None)
    monkeypatch.setattr(container, "_arq_last_failure_at", None)


def _counting_failure(calls: list[str]) -> Any:
    """Return a ``create_pool`` stand-in that records and then fails."""

    async def _create_pool(*_args: object, **_kwargs: object) -> None:
        calls.append("attempt")
        raise ConnectionError("redis is down")

    return _create_pool


@pytest.mark.unit
async def test_dead_redis_is_dialled_once_per_cooldown(monkeypatch: pytest.MonkeyPatch):
    """A failed connection is remembered, so the next request does not re-dial."""
    calls: list[str] = []
    monkeypatch.setattr(container.arq, "create_pool", _counting_failure(calls))

    assert await container.get_arq_redis() is None
    assert await container.get_arq_redis() is None
    assert await container.get_arq_redis() is None

    assert calls == ["attempt"]


@pytest.mark.unit
async def test_redis_is_retried_once_the_cooldown_expires(monkeypatch: pytest.MonkeyPatch):
    """The failure is remembered briefly, not permanently — Redis can come back."""
    calls: list[str] = []
    monkeypatch.setattr(container.arq, "create_pool", _counting_failure(calls))

    now = 1_000.0
    monkeypatch.setattr(container.time, "monotonic", lambda: now)
    assert await container.get_arq_redis() is None

    now += container.ARQ_RETRY_COOLDOWN_SECONDS + 1
    assert await container.get_arq_redis() is None

    assert calls == ["attempt", "attempt"]


@pytest.mark.unit
async def test_successful_pool_is_cached(monkeypatch: pytest.MonkeyPatch):
    """The happy path still dials once and reuses the pool."""
    calls: list[str] = []
    sentinel = object()

    async def _create_pool(*_args: object, **_kwargs: object) -> object:
        calls.append("attempt")
        return sentinel

    monkeypatch.setattr(container.arq, "create_pool", _create_pool)

    assert await container.get_arq_redis() is sentinel
    assert await container.get_arq_redis() is sentinel

    assert calls == ["attempt"]


@pytest.mark.unit
async def test_recovery_after_cooldown_caches_the_new_pool(monkeypatch: pytest.MonkeyPatch):
    """Once Redis answers again, the pool is cached and the dialling stops."""
    calls: list[str] = []
    sentinel = object()
    now = 2_000.0
    monkeypatch.setattr(container.time, "monotonic", lambda: now)

    async def _create_pool(*_args: object, **_kwargs: object) -> object:
        calls.append("attempt")
        if len(calls) == 1:
            raise ConnectionError("redis is down")
        return sentinel

    monkeypatch.setattr(container.arq, "create_pool", _create_pool)

    assert await container.get_arq_redis() is None

    now += container.ARQ_RETRY_COOLDOWN_SECONDS + 1
    assert await container.get_arq_redis() is sentinel
    assert await container.get_arq_redis() is sentinel

    assert calls == ["attempt", "attempt"]
