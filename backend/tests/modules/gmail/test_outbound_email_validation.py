"""Regression tests for input validation in POST /api/gmail/outbound.

`create_outbound_email` raises `HTTPException` for blank recipient/body, but
`HTTPException` was never imported at module level. Because
`per-file-ignores` silenced F821 for this router, the undefined name went
unnoticed: instead of the intended 422, the guard raised `NameError`, which
surfaces to the client as a 500.

These tests pin the intended behaviour — a validation failure is a 422 and the
outbound service is never invoked.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from src.modules.gmail.api.router import create_outbound_email
from src.modules.gmail.api.schemas import OutboundEmailCreateRequest


def _create_test_app():
    """Create a minimal FastAPI app with only the gmail router for testing."""
    from fastapi import FastAPI

    from src.modules.gmail.api.router import router as gmail_router

    app = FastAPI()
    app.include_router(gmail_router)
    return app


def _make_mock_user():
    """Create a mock HR user with the attributes the endpoint reads."""
    user = MagicMock()
    user.id = uuid4()
    user.email = "hr@example.com"
    return user


class TestCreateOutboundEmailValidation:
    """The 422 guards in create_outbound_email must not raise NameError."""

    async def test_blank_recipient_in_to_returns_422(self) -> None:
        """A `to` list holding only a blank address passes schema validation
        (the list itself is non-empty) but resolves to an empty recipient.
        The endpoint guard must answer 422, not blow up with NameError/500.
        """
        app = _create_test_app()

        from src.modules.gmail.container import get_outbound_email_service
        from src.modules.identity.container import get_current_user

        mock_outbound_service = MagicMock()
        mock_outbound_service.create_outbound = AsyncMock()

        app.dependency_overrides[get_current_user] = lambda: _make_mock_user()
        app.dependency_overrides[get_outbound_email_service] = lambda: mock_outbound_service

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/gmail/outbound",
                json={"to": [""], "subject": "Interview invite", "body_text": "Hello"},
            )

        assert response.status_code == 422
        assert "recipient_email" in response.json()["detail"]
        mock_outbound_service.create_outbound.assert_not_awaited()

    async def test_missing_body_html_raises_http_422(self) -> None:
        """The body_html guard is unreachable over HTTP — the schema's
        model_validator rejects a request with neither body_html nor body_text
        first. Call the endpoint directly with a validator-bypassing payload so
        the guard itself is exercised: it must raise HTTPException(422) rather
        than NameError.
        """
        body = OutboundEmailCreateRequest.model_construct(
            candidate_id=None,
            to=["candidate@example.com"],
            cc=None,
            body_text=None,
            reply_to_message_id=None,
            recipient_email=None,
            subject="Interview invite",
            body_html=None,
        )
        mock_outbound_service = MagicMock()
        mock_outbound_service.create_outbound = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await create_outbound_email(
                body=body,
                current_user=_make_mock_user(),
                outbound_service=mock_outbound_service,
            )

        assert exc_info.value.status_code == 422
        assert "body_html" in exc_info.value.detail
        mock_outbound_service.create_outbound.assert_not_awaited()
