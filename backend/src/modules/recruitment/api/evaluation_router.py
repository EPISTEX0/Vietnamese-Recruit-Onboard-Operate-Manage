"""FastAPI router for evaluation management (GH #187).

Defines endpoints for managing versioned evaluation sets and viewing
evaluation samples. All endpoints require HR-level authentication.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole
from src.modules.recruitment.application.evaluation_service import (
    CorrectionEvaluationService,
    CorrectionRecordNotFoundError,
    EvaluationSetNotFoundError,
)
from src.modules.recruitment.infrastructure.repositories import (
    CorrectionRecordRepository,
    EvaluationSampleRepository,
    EvaluationSetRepository,
    RecruitmentInboxItemRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recruitment/evaluation", tags=["recruitment-evaluation"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateEvaluationSetRequest(BaseModel):
    """Request schema for creating a new evaluation set."""

    version: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=500)


class EvaluationSetResponse(BaseModel):
    """Response schema for an evaluation set."""

    id: UUID
    version: str
    description: str
    created_at: str
    sample_count: int = 0


class EvaluationSampleResponse(BaseModel):
    """Response schema for a redacted evaluation sample."""

    id: UUID
    correction_record_id: UUID
    evaluation_set_id: UUID
    redacted_subject: str
    redacted_snippet: str
    redacted_sender_name: str
    redacted_sender_email: str
    ground_truth_intent: str
    model_version: str | None = None
    prompt_version: str | None = None
    policy_version: str | None = None
    cohorts: list[str] = Field(default_factory=list)
    redacted_at: str
    created_at: str


class CommitToEvaluationSetRequest(BaseModel):
    """Request schema for committing a correction to an evaluation set."""

    evaluation_set_id: UUID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def require_hr(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Verify the current user has the HR role."""
    if current_user.role != UserRole.HR:
        raise HTTPException(
            status_code=403,
            detail="Chỉ HR mới có quyền truy cập evaluation endpoints",
        )
    return current_user


def _build_evaluation_service(
    session: AsyncSession,
) -> CorrectionEvaluationService:
    """Build a CorrectionEvaluationService for a database session."""
    return CorrectionEvaluationService(
        session=session,
        correction_repo=CorrectionRecordRepository(session),
        evaluation_set_repo=EvaluationSetRepository(session),
        evaluation_sample_repo=EvaluationSampleRepository(session),
        inbox_repo=RecruitmentInboxItemRepository(session),
    )


CurrentUserDep = Annotated[User, Depends(get_current_user)]
HRUserDep = Annotated[User, Depends(require_hr)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/sets", response_model=list[EvaluationSetResponse])
async def list_evaluation_sets(
    current_user: HRUserDep,
    session: DbSessionDep,
) -> list[EvaluationSetResponse]:
    """List all evaluation sets, newest first."""
    eval_service = _build_evaluation_service(session)
    sets = await eval_service.list_evaluation_sets()

    result: list[EvaluationSetResponse] = []
    for es in sets:
        samples = await eval_service._eval_samples.list_by_evaluation_set_id(es.id)
        result.append(
            EvaluationSetResponse(
                id=es.id,
                version=es.version,
                description=es.description,
                created_at=es.created_at.isoformat() if es.created_at else "",
                sample_count=len(samples),
            )
        )
    return result


@router.post("/sets", response_model=EvaluationSetResponse)
async def create_evaluation_set(
    body: CreateEvaluationSetRequest,
    current_user: HRUserDep,
    session: DbSessionDep,
) -> EvaluationSetResponse:
    """Create a new versioned evaluation set."""
    eval_service = _build_evaluation_service(session)
    es = await eval_service.create_evaluation_set(
        version=body.version,
        description=body.description,
    )
    return EvaluationSetResponse(
        id=es.id,
        version=es.version,
        description=es.description,
        created_at=es.created_at.isoformat() if es.created_at else "",
        sample_count=0,
    )


@router.get("/sets/{set_id}/samples", response_model=list[EvaluationSampleResponse])
async def list_evaluation_samples(
    set_id: UUID,
    current_user: HRUserDep,
    session: DbSessionDep,
) -> list[EvaluationSampleResponse]:
    """List all redacted samples in an evaluation set."""
    eval_service = _build_evaluation_service(session)
    samples = await eval_service.list_samples_for_set(set_id)

    return [
        EvaluationSampleResponse(
            id=s.id,
            correction_record_id=s.correction_record_id,
            evaluation_set_id=s.evaluation_set_id,
            redacted_subject=s.redacted_subject,
            redacted_snippet=s.redacted_snippet,
            redacted_sender_name=s.redacted_sender_name,
            redacted_sender_email=s.redacted_sender_email,
            ground_truth_intent=s.ground_truth_intent,
            model_version=s.model_version,
            prompt_version=s.prompt_version,
            policy_version=s.policy_version,
            cohorts=list(s.cohorts or []),
            redacted_at=s.redacted_at.isoformat() if s.redacted_at else "",
            created_at=s.created_at.isoformat() if s.created_at else "",
        )
        for s in samples
    ]


@router.post(
    "/corrections/{correction_id}/commit",
    response_model=EvaluationSampleResponse,
)
async def commit_correction_to_evaluation_set(
    correction_id: UUID,
    body: CommitToEvaluationSetRequest,
    current_user: HRUserDep,
    session: DbSessionDep,
) -> EvaluationSampleResponse:
    """Commit a redacted correction to an evaluation set.

    The correction record must have been selected for evaluation first.
    """
    eval_service = _build_evaluation_service(session)

    try:
        sample = await eval_service.commit_to_evaluation_set(
            correction_record_id=correction_id,
            evaluation_set_id=body.evaluation_set_id,
        )
    except CorrectionRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except EvaluationSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return EvaluationSampleResponse(
        id=sample.id,
        correction_record_id=sample.correction_record_id,
        evaluation_set_id=sample.evaluation_set_id,
        redacted_subject=sample.redacted_subject,
        redacted_snippet=sample.redacted_snippet,
        redacted_sender_name=sample.redacted_sender_name,
        redacted_sender_email=sample.redacted_sender_email,
        ground_truth_intent=sample.ground_truth_intent,
        model_version=sample.model_version,
        prompt_version=sample.prompt_version,
        policy_version=sample.policy_version,
        cohorts=list(sample.cohorts or []),
        redacted_at=sample.redacted_at.isoformat() if sample.redacted_at else "",
        created_at=sample.created_at.isoformat() if sample.created_at else "",
    )
