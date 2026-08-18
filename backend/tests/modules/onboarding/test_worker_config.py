"""Tests for onboarding worker configuration.

Verifies the OnboardingWorkerSettings ARQ config: startup/shutdown hooks,
registered task functions, Redis settings, and max_tries bound.

Requirements: runtime backbone wiring
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.onboarding.container import process_candidate_accepted
from src.modules.onboarding.worker import OnboardingWorkerSettings, shutdown


class TestOnboardingWorkerSettings:
    """Verify ARQ worker settings are properly configured."""

    def test_has_startup_hook(self) -> None:
        assert callable(OnboardingWorkerSettings.on_startup)

    def test_has_shutdown_hook(self) -> None:
        assert callable(OnboardingWorkerSettings.on_shutdown)

    def test_has_process_candidate_accepted_function(self) -> None:
        function_names = [
            f.__name__ if hasattr(f, "__name__") else str(f)
            for f in OnboardingWorkerSettings.functions
        ]
        assert "process_candidate_accepted" in function_names

    def test_redis_settings_is_configured(self) -> None:
        assert OnboardingWorkerSettings.redis_settings is not None

    def test_max_tries_is_three(self) -> None:
        assert OnboardingWorkerSettings.max_tries == 3


class TestProcessCandidateAccepted:
    """Unit tests for the process_candidate_accepted ARQ task."""

    @pytest.mark.asyncio
    async def test_rejects_malformed_event(self) -> None:
        """A payload missing 'email' is rejected without calling the service."""
        session_maker = MagicMock()
        session = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session_maker.return_value.__aenter__ = AsyncMock(return_value=session)
        session_maker.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = {
            "session_maker": session_maker,
            "job_id": "job-1",
            "job_try": 1,
        }

        # Missing 'email'
        payload = {"candidate_id": "cand-123", "name": "Test"}

        result = await process_candidate_accepted(ctx, payload)
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_event_calls_service(self) -> None:
        """A valid event builds the service and calls start_from_event."""
        session = AsyncMock()
        session_maker = MagicMock()
        session_maker.return_value.__aenter__ = AsyncMock(return_value=session)
        session_maker.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = {
            "session_maker": session_maker,
            "job_id": "job-2",
            "job_try": 1,
        }

        payload = {
            "candidate_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
            "name": "Test Candidate",
            "email": "test@example.com",
        }

        mock_service = MagicMock()
        mock_service.start_from_event = AsyncMock()

        with pytest.MonkeyPatch.context() as m:
            m.setattr(
                "src.modules.onboarding.container._build_service",
                lambda session: mock_service,
            )
            await process_candidate_accepted(ctx, payload)

        mock_service.start_from_event.assert_awaited_once()


class TestOnboardingWorkerShutdown:
    """shutdown() must close the Redis connection even if clearing the heartbeat fails.

    Regression guard for #386 C2: delete() and aclose() used to share one try
    block, so a failing delete() skipped aclose() entirely and leaked the
    connection until the heartbeat's own ex=600 TTL expired.
    """

    @pytest.mark.asyncio
    async def test_aclose_called_and_logged_when_delete_raises(self, caplog) -> None:
        redis_client = AsyncMock()
        redis_client.delete = AsyncMock(side_effect=RuntimeError("redis unreachable"))
        ctx = {"engine": None, "redis_client": redis_client}

        with caplog.at_level(logging.WARNING):
            await shutdown(ctx)

        redis_client.aclose.assert_awaited_once()
        assert "Failed to clear heartbeat on shutdown" in caplog.text

    @pytest.mark.asyncio
    async def test_aclose_called_when_delete_succeeds(self) -> None:
        redis_client = AsyncMock()
        ctx = {"engine": None, "redis_client": redis_client}

        await shutdown(ctx)

        redis_client.delete.assert_awaited_once_with("runtime:heartbeat:onboarding-worker")
        redis_client.aclose.assert_awaited_once()
