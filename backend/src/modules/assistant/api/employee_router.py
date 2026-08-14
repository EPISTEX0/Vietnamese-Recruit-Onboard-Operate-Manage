"""FastAPI router for the Employee Assistant module.

Defines POST /api/ess/assistant/chat — the single endpoint for the
employee-facing conversational AI Assistant.

Requires an active Employee session (get_current_employee). Every tool
call is scoped to the authenticated employee's own data — the employee_id
is injected from the session, never from the LLM.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.assistant.api.employee_schemas import (
    AssistantFeedbackRequest,
    ChatRequest,
    ChatResponseSchema,
    DraftActionSchema,
    OutgoingMessageSchema,
    SessionEndRequest,
    SessionStartRequest,
    SessionStartResponse,
)
from src.modules.assistant.api.session_access import ChatSessionGuardDep
from src.modules.assistant.application.assistant_service import ChatMessage
from src.modules.assistant.application.context_builder import ContextBuilder
from src.modules.assistant.application.employee_assistant_service import (
    EmployeeAssistantService,
)
from src.modules.assistant.container import (
    get_configured_assistant_llm_client,
    get_configured_assistant_settings,
)
from src.modules.assistant.infrastructure.config import AssistantSettings
from src.modules.assistant.infrastructure.llm_client import AssistantLLMClient
from src.modules.assistant.infrastructure.quality_models import (
    AssistantChatSession,
    AssistantFeedbackEvent,
    FeedbackType,
)
from src.modules.attendance.container import (
    get_attendance_record_repository,
)
from src.modules.attendance.infrastructure.attendance_record_repository import (
    AttendanceRecordRepository,
)
from src.modules.employee.api.dependencies import get_current_employee
from src.modules.employee.application.document_service import DocumentService
from src.modules.employee.application.employee_service import EmployeeService
from src.modules.employee.container import get_document_service, get_employee_service
from src.modules.employee.domain.entities import Employee
from src.modules.employee_request.application.leave_service import LeaveService
from src.modules.employee_request.application.overtime_service import OvertimeService
from src.modules.employee_request.container import get_leave_service, get_overtime_service
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User
from src.modules.payslip.application.payslip_service import PayslipService
from src.modules.payslip.container import get_payslip_service

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

employee_assistant_router = APIRouter(
    prefix="/api/ess/assistant",
    tags=["ess-assistant"],
)


def _require_active_employee(
    employee: Employee | None = Depends(get_current_employee),
) -> Employee:
    """Dependency: ensure the request comes from an active Employee."""
    if employee is None:
        raise HTTPException(
            status_code=403,
            detail="Only active employees can access the Employee Assistant",
        )
    return employee


ActiveEmployeeDep = Annotated[Employee, Depends(_require_active_employee)]

# The employee's login account. ``_require_active_employee`` resolves the
# Employee *from* this user, so FastAPI's per-request dependency cache means
# asking for both costs one lookup, not two.
CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_employee_assistant_service(
    employee: ActiveEmployeeDep,
    llm_client: AssistantLLMClient = Depends(get_configured_assistant_llm_client),
    employee_service: EmployeeService = Depends(get_employee_service),
    document_service: DocumentService = Depends(get_document_service),
    attendance_repo: AttendanceRecordRepository = Depends(
        get_attendance_record_repository,
    ),
    leave_service: LeaveService = Depends(get_leave_service),
    overtime_service: OvertimeService = Depends(get_overtime_service),
    payslip_service: PayslipService = Depends(get_payslip_service),
    settings: AssistantSettings = Depends(get_configured_assistant_settings),
    session: AsyncSession = Depends(get_db_session),
) -> EmployeeAssistantService:
    """Provide an EmployeeAssistantService scoped to the current employee."""
    context_builder = ContextBuilder(
        session=session,
        employee_service=employee_service,
        leave_service=leave_service,
        payslip_service=payslip_service,
        overtime_service=overtime_service,
    )
    return EmployeeAssistantService(
        llm_client=llm_client,
        employee_id=employee.id,
        employee_service=employee_service,
        document_service=document_service,
        attendance_repo=attendance_repo,
        leave_service=leave_service,
        overtime_service=overtime_service,
        payslip_service=payslip_service,
        settings=settings,
        context_builder=context_builder,
    )


EmployeeAssistantServiceDep = Annotated[
    EmployeeAssistantService, Depends(get_employee_assistant_service)
]


@employee_assistant_router.post(
    "/chat",
    response_model=ChatResponseSchema,
)
async def employee_chat(
    body: ChatRequest,
    _employee: ActiveEmployeeDep,
    assistant_service: EmployeeAssistantServiceDep,
    session_guard: ChatSessionGuardDep,
    session: AsyncSession = Depends(get_db_session),
) -> ChatResponseSchema:
    """Chat with the Employee AI Assistant.

    Receives the full conversation history (frontend holds state).
    The last message must be from the user. Returns new assistant
    messages and optionally a Draft Action for the employee to review.

    Requires an active Employee session. All tool calls are scoped
    to the authenticated employee's own data.
    """
    last_msg = body.messages[-1]
    if last_msg.role != "user":
        raise HTTPException(status_code=422, detail="Last message must be from user")

    domain_messages = [
        ChatMessage(
            role=m.role,
            content=m.content,
        )
        for m in body.messages
    ]

    # Telemetry is attached only to a session this caller owns; an id that is
    # stale, unknown, or somebody else's resolves to None and records nothing.
    response = await assistant_service.chat(
        domain_messages,
        session=session,
        chat_session=await session_guard.resolve_optional(body.session_id),
    )

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

    return ChatResponseSchema(
        messages=new_messages,
        draft_action=draft_action,
    )


@employee_assistant_router.post("/feedback", status_code=204)
async def employee_assistant_feedback(
    body: AssistantFeedbackRequest,
    _employee: ActiveEmployeeDep,
    session_guard: ChatSessionGuardDep,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Record employee feedback (thumbs up/down) for an assistant response.

    Raises 404 unless the session belongs to the caller.
    """
    chat_session = await session_guard.require(body.session_id)

    feedback_event = AssistantFeedbackEvent(
        session_id=chat_session.id,
        message_index=body.message_index,
        feedback_type=FeedbackType(body.feedback_type),
        optional_text=body.optional_text,
    )
    session.add(feedback_event)
    await session.commit()


@employee_assistant_router.post("/session/start", response_model=SessionStartResponse)
async def start_employee_assistant_session(
    body: SessionStartRequest,
    _employee: ActiveEmployeeDep,
    _user: CurrentUserDep,
    session: AsyncSession = Depends(get_db_session),
) -> SessionStartResponse:
    """Start an Employee AI Assistant chat session.

    Creates a record in assistant_chat_sessions.
    Called when the frontend ChatInterface mounts.
    """
    # The two columns address two different tables: ``user_id`` is the login
    # account (FK -> users.id, NOT NULL) and ``employee_id`` is the optional
    # link to the personnel record (FK -> employees.id). They are never the
    # same value, so each has to come from its own dependency.
    chat_session = AssistantChatSession(
        user_id=uuid.UUID(str(_user.id)),
        assistant_type="employee",
        employee_id=uuid.UUID(str(_employee.id)),
    )
    session.add(chat_session)
    await session.commit()
    await session.refresh(chat_session)
    return SessionStartResponse(session_id=str(chat_session.id))


@employee_assistant_router.post("/session/end", status_code=204)
async def end_employee_assistant_session(
    body: SessionEndRequest,
    _employee: ActiveEmployeeDep,
    session_guard: ChatSessionGuardDep,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """End an Employee AI Assistant chat session.

    Updates end_at timestamp and message_count.
    Called when the frontend ChatInterface unmounts.

    Raises 404 unless the session belongs to the caller. This handler is where
    the reported IDOR lived: it resolved the id with no owner filter, so any
    authenticated employee could end an HR account's session.
    """
    chat_session = await session_guard.require(body.session_id)
    chat_session.end_at = datetime.now(UTC)
    await session.commit()
