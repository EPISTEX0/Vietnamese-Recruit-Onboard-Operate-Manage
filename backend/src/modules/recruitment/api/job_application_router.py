"""FastAPI router for Job Application assignment and promotion (GH #186).

Defines the /api/recruitment/job-applications/{id}/assignment and
/api/recruitment/job-applications/{id}/promote endpoints for HR operations.

All endpoints require HR-level authentication.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole
from src.modules.recruitment.api.schemas import (
    AssignJobApplicationRequest,
    CorrectJobApplicationSourceRequest,
    JobApplicationAssignmentResponse,
    JobApplicationPromoteResponse,
    JobApplicationSourceResponse,
    PromoteJobApplicationRequest,
)
from src.modules.recruitment.application.job_application_decision_service import (
    JobApplicationDecisionService,
)
from src.modules.recruitment.domain.exceptions import RecruitmentError
from src.modules.recruitment.infrastructure.repositories import (
    CandidateRepository,
    JobApplicationRepository,
    JobOpeningRepository,
)
from src.shared.messages import get_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/api/recruitment/job-applications",
    tags=["recruitment-job-applications"],
)

# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


async def require_hr(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Verify the current user has the HR role."""
    if current_user.role != UserRole.HR:
        raise HTTPException(
            status_code=403,
            detail="Chỉ HR mới có quyền quản lý Job Application",
        )
    return current_user


async def _build_service(
    session: AsyncSession = Depends(get_db_session),
) -> JobApplicationDecisionService:
    """Build a JobApplicationService with promotion/assignment dependencies."""
    return JobApplicationDecisionService(
        session=session,
        job_application_repo=JobApplicationRepository(session),
        candidate_repo=CandidateRepository(session),
        job_opening_repo=JobOpeningRepository(session),
    )


# ---------------------------------------------------------------------------
# Type aliases for injected dependencies
# ---------------------------------------------------------------------------

CurrentUserDep = Annotated[User, Depends(get_current_user)]
HRUserDep = Annotated[User, Depends(require_hr)]
ServiceDep = Annotated[JobApplicationDecisionService, Depends(_build_service)]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{job_application_id}/source",
    response_model=JobApplicationSourceResponse,
)
async def correct_job_application_source(
    job_application_id: UUID,
    body: CorrectJobApplicationSourceRequest,
    current_user: HRUserDep,
    service: ServiceDep,
) -> JobApplicationSourceResponse:
    """Correct the source classification with an HR audit trail."""
    application = await service.correct_source(
        job_application_id, body.source, user_id=current_user.id
    )
    return JobApplicationSourceResponse.model_validate(application)


@router.post(
    "/{job_application_id}/assignment",
    response_model=JobApplicationAssignmentResponse,
)
async def assign_job_application(
    job_application_id: UUID,
    body: AssignJobApplicationRequest,
    current_user: HRUserDep,
    service: ServiceDep,
) -> JobApplicationAssignmentResponse:
    """Assign or unassign a Job Application to/from a Job Opening.

    Only OPEN Job Openings accept assignments.
    Dismissed Job Applications cannot be assigned.
    Passing null for job_opening_id unassigns the current assignment.

    Args:
        job_application_id: UUID of the Job Application.
        body: Assignment request with optional job_opening_id.
        current_user: The authenticated user.
        service: The JobApplicationService.

    Returns:
        Updated Job Application with job_opening_id and candidate_id.

    Raises:
        404: If Job Application not found.
        409: If assignment is blocked or Job Opening not open.
    """

    try:
        result = await service.assign_to_job_opening(
            job_application_id=job_application_id,
            job_opening_id=body.job_opening_id,
            user_id=current_user.id,
        )
    except RecruitmentError:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to assign job application %s to job opening %s",
            job_application_id,
            body.job_opening_id,
        )
        raise HTTPException(status_code=500, detail=get_message("INTERNAL_ERROR")) from exc

    return JobApplicationAssignmentResponse(
        id=result.id,
        job_opening_id=result.job_opening_id,
        candidate_id=result.candidate_id,
        status=result.status,
    )


@router.post(
    "/{job_application_id}/promote",
    response_model=JobApplicationPromoteResponse,
)
async def promote_job_application(
    job_application_id: UUID,
    body: PromoteJobApplicationRequest,
    current_user: HRUserDep,
    service: ServiceDep,
) -> JobApplicationPromoteResponse:
    """Promote a valid Job Application to exactly one Candidate.

    Idempotent: if the Job Application already has a candidate_id set,
    returns the existing Candidate without creating a duplicate.
    Missing applicant_name/applicant_email is invalid.

    Args:
        job_application_id: UUID of the Job Application.
        body: Promotion request with applicant_name, applicant_email,
            and optional job_opening_id.
        current_user: The authenticated user.
        service: The JobApplicationService.

    Returns:
        Promotion result with candidate details.

    Raises:
        404: If Job Application not found.
        409: If promotion is blocked (dismissed, missing fields).
    """

    try:
        app, candidate = await service.promote_to_candidate(
            job_application_id=job_application_id,
            applicant_name=body.applicant_name,
            applicant_email=body.applicant_email,
            job_opening_id=body.job_opening_id,
            user_id=current_user.id,
        )
    except RecruitmentError:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to promote job application %s to candidate (job_opening_id=%s)",
            job_application_id,
            body.job_opening_id,
        )
        raise HTTPException(status_code=500, detail=get_message("INTERNAL_ERROR")) from exc

    return JobApplicationPromoteResponse(
        id=app.id,
        candidate_id=candidate.id,
        candidate_name=candidate.name,
        candidate_email=candidate.email,
        job_opening_id=app.job_opening_id,
        status=app.status,
    )
