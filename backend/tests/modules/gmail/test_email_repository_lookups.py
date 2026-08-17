"""Unit tests for EmailRepository methods added for #370.

``list_recently_created`` and ``update_processing_status`` back the
``import_service.py``/``intent_classifier.py`` call sites that used to run
raw ``self._session.execute(...)`` queries directly.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.gmail.domain.entities import EmailMessage
from src.modules.gmail.infrastructure.email_repository import EmailRepository


def _make_message(**overrides) -> EmailMessage:
    defaults = {
        "user_id": uuid4(),
        "gmail_message_id": f"msg-{uuid4().hex[:8]}",
        "gmail_thread_id": f"thread-{uuid4().hex[:8]}",
        "received_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return EmailMessage(**defaults)


@pytest.mark.asyncio
async def test_list_recently_created_returns_messages_in_query_order() -> None:
    user_id = uuid4()
    messages = [_make_message(user_id=user_id), _make_message(user_id=user_id)]
    result = MagicMock()
    result.scalars.return_value.all.return_value = messages
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    found = await EmailRepository(session).list_recently_created(user_id, limit=10)

    assert found == messages


@pytest.mark.asyncio
async def test_update_processing_status_returns_none_when_not_found() -> None:
    """Not-found is a normal outcome, not swallowed as an error."""
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    updated = await EmailRepository(session).update_processing_status(
        uuid4(), processing_status="classified", category="recruitment"
    )

    assert updated is None
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_update_processing_status_sets_fields_and_flushes() -> None:
    message = _make_message(processing_status="unprocessed", category=None)
    result = MagicMock()
    result.scalars.return_value.first.return_value = message
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    updated = await EmailRepository(session).update_processing_status(
        message.id, processing_status="classified", category="recruitment"
    )

    assert updated is message
    assert updated is not None
    assert updated.processing_status == "classified"
    assert updated.category == "recruitment"
    session.add.assert_called_once_with(message)
    session.flush.assert_awaited_once()
