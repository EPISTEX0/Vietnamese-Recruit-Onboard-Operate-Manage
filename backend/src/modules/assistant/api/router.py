"""FastAPI router for the AI Assistant module.

Defines POST /api/assistant/chat — the single endpoint for the
conversational AI Assistant.

Requires admin role. Conversation history is
held in frontend memory; backend processes each turn statelessly.
"""

from __future__ import annotations

import json
import logging
import typing
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.assistant.api.schemas import (
    AssistantFeedbackRequest,
    ChatRequest,
    ChatResponseSchema,
    DraftActionSchema,
    DraftDecisionRequest,
    OutgoingMessageSchema,
    SessionEndRequest,
    SessionStartRequest,
    SessionStartResponse,
)
from src.modules.assistant.application.assistant_service import (
    AssistantService,
    ChatMessage,
)
from src.modules.assistant.container import get_assistant_service
from src.modules.assistant.infrastructure.quality_models import (
    AssistantChatSession,
    AssistantFeedbackEvent,
)
from src.modules.assistant.infrastructure.tool_config_repository import (
    ToolConfigRepository,
)
from src.modules.identity.api.admin_router import require_admin
from src.modules.identity.application.audit_service import AuditService
from src.modules.identity.container import get_audit_service, get_db_session
from src.modules.identity.domain.entities import AuditActionType, User

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

AdminUserDep = Annotated[User, Depends(require_admin)]
AssistantServiceDep = Annotated[AssistantService, Depends(get_assistant_service)]
AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.post(
    "/chat",
    response_model=ChatResponseSchema,
)
async def chat(
    body: ChatRequest,
    _user: AdminUserDep,
    assistant_service: AssistantServiceDep,
    audit_service: AuditServiceDep,
    session: AsyncSession = Depends(get_db_session),
) -> ChatResponseSchema:
    """Chat with the AI Assistant.

    Receives the full conversation history (frontend holds state).
    The last message must be from the user. Returns new assistant
    messages and optionally a Draft Action for HR to review.

    Requires admin role. Logs audit when a Draft Action is returned.
    """
    # Validate last message is from user
    last_msg = body.messages[-1]
    if last_msg.role != "user":
        raise HTTPException(status_code=422, detail="Last message must be from user")

    # Fetch enabled tool names from DB config
    tool_config_repo = ToolConfigRepository(session)
    enabled_tool_names = await tool_config_repo.get_enabled_tool_names()

    # Convert schema to domain messages (only role + content — tool fields are
    # never accepted from the client, per ADR-0006)
    domain_messages = [
        ChatMessage(
            role=m.role,
            content=m.content,
        )
        for m in body.messages
    ]

    # Run the assistant with filtered tools
    response = await assistant_service.chat(
        domain_messages,
        enabled_tool_names=enabled_tool_names,
        session=session,
        session_id=uuid.UUID(body.session_id) if body.session_id else None,
    )

    # Convert back to schema
    new_messages = [
        OutgoingMessageSchema(
            role=m.role,
            content=m.content,
            tool_calls=m.tool_calls,
            tool_call_id=m.tool_call_id,
            name=m.name,
        )
        for m in response.messages
    ]

    draft_action = None
    if response.draft_action:
        draft_action = DraftActionSchema(**response.draft_action)

        # Audit: log when a Draft Action is returned (HR used a Draft-Tool)
        await audit_service.log_action(
            admin=_user,
            action_type=AuditActionType.ASSISTANT_CHAT,
            details={
                "action": "draft_action_returned",
                "draft_type": response.draft_action.get("action_type"),
            },
        )

    return ChatResponseSchema(
        messages=new_messages,
        draft_action=draft_action,
    )


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    _user: AdminUserDep,
    assistant_service: AssistantServiceDep,
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """Chat with the AI Assistant via SSE streaming.

    Same logic as POST /chat, but streams the response as Server-Sent Events.
    The frontend reads events (``text_delta``, ``tool_start``, ``tool_end``,
    ``draft_action``, ``done``, ``error``) for real-time UI updates.

    Requires admin role.
    """
    # Validate last message is from user
    last_msg = body.messages[-1]
    if last_msg.role != "user":
        raise HTTPException(status_code=422, detail="Last message must be from user")

    # Fetch enabled tool names from DB config
    tool_config_repo = ToolConfigRepository(session)
    enabled_tool_names = await tool_config_repo.get_enabled_tool_names()

    # Convert schema to domain messages
    domain_messages = [
        ChatMessage(
            role=m.role,
            content=m.content,
        )
        for m in body.messages
    ]

    async def event_stream() -> typing.AsyncGenerator[bytes, None]:
        """Generate SSE-formatted bytes from the assistant chat_stream generator."""
        try:
            async for event in assistant_service.chat_stream(
                domain_messages,
                enabled_tool_names=enabled_tool_names,
                session=session,
                session_id=uuid.UUID(body.session_id) if body.session_id else None,
            ):
                event_name = event["event"]
                event_data = json.dumps(event["data"], ensure_ascii=False)
                yield f"event: {event_name}\ndata: {event_data}\n\n".encode()
        except Exception as exc:
            logger.exception("chat_stream error")
            error_data = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n".encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/feedback", status_code=204)
async def assistant_feedback(
    body: AssistantFeedbackRequest,
    _user: AdminUserDep,
    audit_service: AuditServiceDep,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Record user feedback (thumbs up/down) for an assistant response."""
    await audit_service.log_action(
        admin=_user,
        action_type=AuditActionType.ASSISTANT_CHAT,
        details={
            "event": "message_feedback",
            "session_id": body.session_id,
            "message_index": body.message_index,
            "feedback_type": body.feedback_type,
            "optional_text": body.optional_text,
        },
    )
    from src.modules.assistant.infrastructure.quality_models import (
        FeedbackType,
    )

    feedback_event = AssistantFeedbackEvent(
        session_id=uuid.UUID(body.session_id),
        message_index=body.message_index,
        feedback_type=FeedbackType(body.feedback_type),
        optional_text=body.optional_text,
    )
    session.add(feedback_event)
    await session.commit()


@router.post("/draft-decision", status_code=204)
async def record_draft_decision(
    body: DraftDecisionRequest,
    _user: AdminUserDep,
    audit_service: AuditServiceDep,
) -> None:
    """Record HR's confirm/reject decision without storing conversation text."""
    await audit_service.log_action(
        admin=_user,
        action_type=AuditActionType.ASSISTANT_CHAT,
        details={
            "event": "draft_action_decision",
            "decision": body.decision,
            "action_type": body.action_type,
            "scope": body.provenance.get("scope", "unknown"),
            "tool": body.provenance.get("tool", "unknown"),
            "version": "assistant-v1",
            "status": "confirmed" if body.decision == "confirm" else "rejected",
            "confirm_endpoint": body.confirm_endpoint,
            "parameters": {"candidate_id": body.provenance.get("candidate_id")},
        },
    )


@router.post("/session/start", response_model=SessionStartResponse)
async def start_assistant_session(
    body: SessionStartRequest,
    _user: AdminUserDep,
    session: AsyncSession = Depends(get_db_session),
) -> SessionStartResponse:
    """Start an AI Assistant chat session.

    Creates a record in assistant_chat_sessions.
    Called when the frontend ChatInterface mounts.
    """
    chat_session = AssistantChatSession(
        user_id=uuid.UUID(str(_user.id)),
        assistant_type=body.assistant_type,
    )
    session.add(chat_session)
    await session.commit()
    await session.refresh(chat_session)
    return SessionStartResponse(session_id=str(chat_session.id))


@router.post("/session/end", status_code=204)
async def end_assistant_session(
    body: SessionEndRequest,
    _user: AdminUserDep,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """End an AI Assistant chat session.

    Updates end_at timestamp and message_count.
    Called when the frontend ChatInterface unmounts.
    """
    from sqlmodel import select

    result = await session.execute(
        select(AssistantChatSession).where(
            AssistantChatSession.id == uuid.UUID(body.session_id),
        )
    )
    chat_session = result.scalar_one_or_none()
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    chat_session.end_at = datetime.now(UTC)
    await session.commit()
