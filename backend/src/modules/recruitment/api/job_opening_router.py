"""FastAPI router for the Recruitment Job Opening endpoints.

Defines the /api/recruitment/job-openings/* endpoints for Job Opening
CRUD and lifecycle operations (create, update, open, close, cancel).

Requirements: Recruitment Planning vertical slice - Job Opening management.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select as sqlmodel_select

from src.modules.employee.domain.entities import Position
from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole
from src.modules.recruitment.api.schemas import (
    JobOpeningCreate,
    JobOpeningListItemResponse,
    JobOpeningListResponse,
    JobOpeningMetricsResponse,
    JobOpeningResponse,
    JobOpeningUpdate,
)
from src.modules.recruitment.application.job_opening_service import JobOpeningService
from src.modules.recruitment.domain.enums import JobOpeningStatus
from src.modules.recruitment.infrastructure.repositories import JobOpeningRepository

# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

CurrentUserDep = Annotated[User, Depends(get_current_user)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def require_hr(
    current_user: CurrentUserDep,
) -> User:
    """Verify the current user has the HR role.

    Args:
        current_user: The authenticated User entity from the JWT.

    Returns:
        The authenticated User entity if they have the HR role.

    Raises:
        HTTPException: 403 Forbidden if the user does not have the HR role.
    """
    from fastapi import HTTPException

    if current_user.role != UserRole.HR:
        raise HTTPException(
            status_code=403,
            detail="HR access required",
        )
    return current_user


HRUserDep = Annotated[User, Depends(require_hr)]


def get_job_opening_service(
    session: SessionDep,
    current_user: CurrentUserDep,
) -> JobOpeningService:
    """Provide a JobOpeningService instance with all dependencies.

    Args:
        session: The async database session from DI.
        current_user: The authenticated user.

    Returns:
        A fully configured JobOpeningService.
    """
    job_opening_repo = JobOpeningRepository(session)
    return JobOpeningService(
        session=session,
        job_opening_repo=job_opening_repo,
        user_id=current_user.id,
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

job_opening_router = APIRouter(
    prefix="/api/recruitment/job-openings",
    tags=["recruitment-job-openings"],
)

# ---------------------------------------------------------------------------
# Create Job Opening
# ---------------------------------------------------------------------------


@job_opening_router.post("", response_model=JobOpeningResponse, status_code=201)
async def create_job_opening(
    body: JobOpeningCreate,
    current_user: HRUserDep,
    session: SessionDep,
) -> JobOpeningResponse:
    """Create a new Job Opening.

    Creates a Job Opening with the provided title, position_id, and target_headcount.
    The status defaults to 'draft'. Position must exist.

    Args:
        body: Job Opening creation parameters.
        current_user: The authenticated user.
        session: The async database session.

    Returns:
        The created Job Opening record.
    """
    service = get_job_opening_service(session, current_user)
    job_opening = await service.create_job_opening(
        title=body.title,
        position_id=body.position_id,
        target_headcount=body.target_headcount,
        description=body.description,
    )
    
    return JobOpeningResponse.model_validate(job_opening)


# ---------------------------------------------------------------------------
# List Job Openings
# ---------------------------------------------------------------------------


@job_opening_router.get("", response_model=JobOpeningListResponse)
async def list_job_openings(
    current_user: CurrentUserDep,
    session: SessionDep,
    status: list[JobOpeningStatus] | None = Query(default=None, description="Filter by status"),
    position_id: UUID | None = Query(default=None, description="Filter by position UUID"),
    search: str | None = Query(
        default=None, min_length=1, max_length=200, description="Search by title"
    ),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
) -> JobOpeningListResponse:
    """List Job Openings with pagination and optional filters.

    Returns a paginated list of Job Openings sorted by created_at descending.

    Args:
        current_user: The authenticated user.
        session: The async database session.
        status: Optional status filter.
        position_id: Optional position UUID filter.
        search: Optional search query for title.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        Paginated list of Job Openings with total count.
    """
    service = get_job_opening_service(session, current_user)

    # Convert status enum list to string list
    status_list: list[str] | None = None
    if status:
        status_list = [s.value for s in status]

    result = await service.list_job_openings(
        status=status_list,
        position_id=position_id,
        search=search,
        page=page,
        page_size=page_size,
    )

    job_openings = result[0]
    jo_ids = [jo.id for jo in job_openings]
    counts_by_jo = await service.get_candidate_counts(jo_ids) if jo_ids else {}

    # Resolve position names for display
    position_ids = list({jo.position_id for jo in job_openings})
    position_names: dict[str, str] = {}
    if position_ids:
        from sqlmodel import col as sa_col

        pos_stmt = sqlmodel_select(Position).where(sa_col(Position.id).in_(position_ids))
        pos_result = await session.execute(pos_stmt)
        for p in pos_result.scalars().all():
            position_names[str(p.id)] = p.name

    items = [
        JobOpeningListItemResponse(
            id=jo.id,
            title=jo.title,
            position_id=jo.position_id,
            target_headcount=jo.target_headcount,
            status=JobOpeningStatus(jo.status),
            created_at=jo.created_at,
            position_name=position_names.get(str(jo.position_id), ""),
            total_candidates=sum(counts_by_jo.get(jo.id, {}).values()),
            accepted_count=counts_by_jo.get(jo.id, {}).get("accepted", 0),
            filled=counts_by_jo.get(jo.id, {}).get("accepted", 0) >= jo.target_headcount,
            overfilled=counts_by_jo.get(jo.id, {}).get("accepted", 0) > jo.target_headcount,
            remaining_headcount=jo.target_headcount
            - counts_by_jo.get(jo.id, {}).get("accepted", 0),
        )
        for jo in job_openings
    ]

    return JobOpeningListResponse(
        job_openings=items,
        total_count=result[1],
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# Job Opening metrics
# ---------------------------------------------------------------------------


@job_opening_router.get("/metrics", response_model=JobOpeningMetricsResponse)
async def get_job_opening_metrics(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> JobOpeningMetricsResponse:
    """Return summary metrics for Job Opening lifecycle states.

    Provides aggregated counts of Job Openings by their lifecycle status
    (draft, open, closed, cancelled) to give HR visibility into recruitment
    pipeline health.

    Args:
        current_user: The authenticated user.
        session: The async database session.

    Returns:
        Summary metrics with counts per status.
    """
    from src.modules.recruitment.api.schemas import JobOpeningMetricsResponse

    service = get_job_opening_service(session, current_user)
    metrics = await service.get_summary_metrics()
    return JobOpeningMetricsResponse(**metrics)


# ---------------------------------------------------------------------------
# Get Job Opening detail
# ---------------------------------------------------------------------------


@job_opening_router.get("/{job_opening_id}", response_model=JobOpeningResponse)
async def get_job_opening(
    job_opening_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> JobOpeningResponse:
    """Get a Job Opening by ID.

    Args:
        job_opening_id: UUID of the Job Opening.
        current_user: The authenticated user.
        session: The async database session.

    Returns:
        The Job Opening record.
    """
    service = get_job_opening_service(session, current_user)
    job_opening = await service.get_job_opening(job_opening_id)
    counts_by_jo = await service.get_candidate_counts([job_opening.id])
    candidate_counts = counts_by_jo.get(job_opening.id, {})
    accepted_count = candidate_counts.get("accepted", 0)
    resp = JobOpeningResponse.model_validate(job_opening)
    resp.candidate_counts = candidate_counts
    resp.accepted_count = accepted_count
    resp.filled = accepted_count >= job_opening.target_headcount
    resp.overfilled = accepted_count > job_opening.target_headcount
    resp.remaining_headcount = job_opening.target_headcount - accepted_count
    # Resolve position name
    pos_stmt = sqlmodel_select(Position).where(Position.id == job_opening.position_id)
    pos_result = await session.execute(pos_stmt)
    position = pos_result.scalars().first()
    if position:
        resp.position_name = position.name
    return resp


# ---------------------------------------------------------------------------
# Update Job Opening
# ---------------------------------------------------------------------------


@job_opening_router.patch("/{job_opening_id}", response_model=JobOpeningResponse)
async def update_job_opening(
    job_opening_id: UUID,
    body: JobOpeningUpdate,
    current_user: HRUserDep,
    session: SessionDep,
) -> JobOpeningResponse:
    """Update a Job Opening's editable fields.

    Only title, description, and target_headcount can be updated.
    Status changes use dedicated endpoints.

    Args:
        job_opening_id: UUID of the Job Opening.
        body: Job Opening update parameters.
        current_user: The authenticated user.
        session: The async database session.

    Returns:
        The updated Job Opening record.
    """
    service = get_job_opening_service(session, current_user)
    job_opening = await service.update_job_opening(
        job_opening_id=job_opening_id,
        title=body.title,
        description=body.description,
        target_headcount=body.target_headcount,
    )
    return JobOpeningResponse.model_validate(job_opening)


# ---------------------------------------------------------------------------
# Open Job Opening
# ---------------------------------------------------------------------------


@job_opening_router.post("/{job_opening_id}/open", response_model=JobOpeningResponse)
async def open_job_opening(
    job_opening_id: UUID,
    current_user: HRUserDep,
    session: SessionDep,
) -> JobOpeningResponse:
    """Open a Job Opening for applications.

    Allowed from: draft, closed (reopen).

    Args:
        job_opening_id: UUID of the Job Opening.
        current_user: The authenticated user.
        session: The async database session.

    Returns:
        The updated Job Opening record.
    """
    service = get_job_opening_service(session, current_user)
    job_opening = await service.open_job_opening(job_opening_id)
    return JobOpeningResponse.model_validate(job_opening)


# ---------------------------------------------------------------------------
# Close Job Opening
# ---------------------------------------------------------------------------


@job_opening_router.post("/{job_opening_id}/close", response_model=JobOpeningResponse)
async def close_job_opening(
    job_opening_id: UUID,
    current_user: HRUserDep,
    session: SessionDep,
) -> JobOpeningResponse:
    """Close a Job Opening (filled or no longer hiring).

    Allowed from: open.

    Args:
        job_opening_id: UUID of the Job Opening.
        current_user: The authenticated user.
        session: The async database session.

    Returns:
        The updated Job Opening record.
    """
    service = get_job_opening_service(session, current_user)
    job_opening = await service.close_job_opening(job_opening_id)
    return JobOpeningResponse.model_validate(job_opening)


# ---------------------------------------------------------------------------
# Cancel Job Opening
# ---------------------------------------------------------------------------


@job_opening_router.post("/{job_opening_id}/cancel", response_model=JobOpeningResponse)
async def cancel_job_opening(
    job_opening_id: UUID,
    current_user: HRUserDep,
    session: SessionDep,
) -> JobOpeningResponse:
    """Cancel a Job Opening (terminal state).

    Allowed from: draft, open.

    Args:
        job_opening_id: UUID of the Job Opening.
        current_user: The authenticated user.
        session: The async database session.

    Returns:
        The updated Job Opening record.
    """
    service = get_job_opening_service(session, current_user)
    job_opening = await service.cancel_job_opening(job_opening_id)
    return JobOpeningResponse.model_validate(job_opening)
