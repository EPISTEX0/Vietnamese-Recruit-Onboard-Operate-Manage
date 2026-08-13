"""FastAPI router for calendar conflict endpoints.

Defines the /api/recruitment/calendar-conflicts/* endpoints for listing
unresolved calendar conflicts and resolving them (keep Google or overwrite
Vroom).

When a conditional write (If-Match) to Google Calendar fails with 412, the
service captures the conflict without mutating the Interview or Candidate.
HR can then list unresolved conflicts and resolve each one.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole
from src.modules.recruitment.api.schemas import (
    CalendarConflictListResponse,
    CalendarConflictResponse,
    ResolveConflictRequest,
)
from src.modules.recruitment.application.interview_scheduler_service import (
    InterviewSchedulerService,
)
from src.modules.recruitment.container import get_interview_scheduler_service
from src.modules.recruitment.domain.entities import CalendarConflict
from src.modules.recruitment.domain.exceptions import (
    CalendarConflictNotFoundError,
    CalendarEventConflictError,
)

# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


async def require_hr(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    if current_user.role != UserRole.HR:
        raise HTTPException(status_code=403, detail="HR access required")
    return current_user


HRUserDep = Annotated[User, Depends(require_hr)]
InterviewSchedulerServiceDep = Annotated[
    InterviewSchedulerService, Depends(get_interview_scheduler_service)
]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

conflict_router = APIRouter(
    prefix="/api/recruitment/calendar-conflicts",
    tags=["recruitment-calendar-conflicts"],
)


# ---------------------------------------------------------------------------
# List calendar conflicts
# ---------------------------------------------------------------------------


@conflict_router.get("", response_model=CalendarConflictListResponse)
async def list_calendar_conflicts(
    current_user: HRUserDep,
    interview_scheduler: InterviewSchedulerServiceDep,
    status: str | None = Query(
        default=None,
        description="Filter by status (default: unresolved)",
    ),
    candidate_id: UUID | None = Query(
        default=None,
        description="Filter by candidate ID",
    ),
) -> CalendarConflictListResponse:
    """List calendar conflicts, optionally filtered by status or candidate.

    By default, returns only unresolved conflicts.
    """
    conflicts = await interview_scheduler.list_calendar_conflicts(
        status=status,
        candidate_id=candidate_id,
    )

    return CalendarConflictListResponse(
        conflicts=[CalendarConflictResponse.model_validate(c) for c in conflicts],
        total_count=len(conflicts),
    )


# ---------------------------------------------------------------------------
# Get a single calendar conflict
# ---------------------------------------------------------------------------


@conflict_router.get(
    "/{conflict_id}",
    response_model=CalendarConflictResponse,
)
async def get_calendar_conflict(
    conflict_id: UUID,
    current_user: HRUserDep,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CalendarConflictResponse:
    """Get a single calendar conflict by ID."""
    stmt = select(CalendarConflict).where(CalendarConflict.id == conflict_id)
    result = await session.execute(stmt)
    conflict = result.scalars().first()
    if conflict is None:
        raise HTTPException(
            status_code=404,
            detail=f"Calendar conflict not found: {conflict_id}",
        )
    return CalendarConflictResponse.model_validate(conflict)


# ---------------------------------------------------------------------------
# Resolve a calendar conflict
# ---------------------------------------------------------------------------


@conflict_router.post(
    "/{conflict_id}/resolve",
    response_model=CalendarConflictResponse,
)
async def resolve_calendar_conflict(
    conflict_id: UUID,
    body: ResolveConflictRequest,
    current_user: HRUserDep,
    interview_scheduler: InterviewSchedulerServiceDep,
) -> CalendarConflictResponse:
    """Resolve a calendar conflict.

    ``keep_google``: Update the local Interview to match the remote Google
    Calendar event.

    ``overwrite_vroom``: Push Vroom's current state to Google Calendar using
    the remote event's ETag.
    """
    try:
        conflict = await interview_scheduler.resolve_calendar_conflict(
            conflict_id=conflict_id,
            choice=body.choice,
            acting_user_id=current_user.id,
        )
    except CalendarConflictNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except CalendarEventConflictError as exc:
        # A new conflict occurred during resolution; the service already
        # captured it. Return 409 so the client knows to retry.
        raise HTTPException(
            status_code=409,
            detail=str(exc.message) if hasattr(exc, "message") else str(exc),
        )

    return CalendarConflictResponse.model_validate(conflict)
