"""FastAPI router for the Recruitment Inbox.

Defines the /api/recruitment/inbox/* endpoints for listing, viewing,
correcting intent, and dismissing inbox items. All endpoints require
HR-level authentication.

Recruitment Inbox is the unified HR surface for email and Job Application
work; it is not a Gmail inbox or an AI error queue.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.identity.container import get_current_user, get_db_session
from src.modules.identity.domain.entities import User, UserRole
from src.modules.recruitment.api.schemas import (
    CorrectIntentRequest,
    InboxItemResponse,
    InboxListResponse,
    JobApplicationResponse,
    LinkProposalResponse,
    ProposeLinkRequest,
    ResolveLinkProposalRequest,
    SplitInboxItemRequest,
    SplitInboxItemResponse,
)
from src.modules.recruitment.application.evaluation_service import (
    CorrectionEvaluationService,
    CorrectionRecordNotFoundError,
)
from src.modules.recruitment.application.inbox_service import (
    InboxItemDismissedError,
    InboxService,
    InvalidLinkProposalError,
    JobApplicationNotFoundError,
    LinkProposalNotFoundError,
    RecruitmentInboxItemNotFoundError,
    SplitApplicant,
)
from src.modules.recruitment.domain.enums import InboxStatus
from src.modules.recruitment.infrastructure.repositories import (
    CorrectionRecordRepository,
    EvaluationSampleRepository,
    EvaluationSetRepository,
    JobApplicationLinkProposalRepository,
    JobApplicationRepository,
    RecruitmentInboxItemRepository,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/recruitment/inbox", tags=["recruitment-inbox"])

# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


async def require_hr(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Verify the current user has the HR role."""
    if current_user.role != UserRole.HR:
        raise HTTPException(
            status_code=403,
            detail="Chỉ HR mới có quyền truy cập Recruitment Inbox",
        )
    return current_user


def _build_inbox_service(
    session: AsyncSession,
    inbox_repo: RecruitmentInboxItemRepository,
) -> InboxService:
    """Build an InboxService for a database session and repository."""
    return InboxService(
        session=session,
        inbox_repo=inbox_repo,
        job_application_repo=JobApplicationRepository(session),
        link_proposal_repo=JobApplicationLinkProposalRepository(session),
    )


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


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

CurrentUserDep = Annotated[User, Depends(get_current_user)]
HRUserDep = Annotated[User, Depends(require_hr)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def get_inbox_repo(
    session: AsyncSession = Depends(get_db_session),
) -> RecruitmentInboxItemRepository:
    """Provide a RecruitmentInboxItemRepository via DI."""
    return RecruitmentInboxItemRepository(session)


# ---------------------------------------------------------------------------
# Valid filter values for documentation
# ---------------------------------------------------------------------------

_VALID_FILTERS = [
    InboxStatus.NEEDS_CLASSIFICATION,
    InboxStatus.NEEDS_INFORMATION,
    InboxStatus.READY_FOR_REVIEW,
    InboxStatus.RESOLVED,
]
_VALID_FILTER_STR = ", ".join(v.value for v in _VALID_FILTERS)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=InboxListResponse)
async def list_inbox(
    current_user: HRUserDep,
    session: DbSessionDep,
    inbox_repo: RecruitmentInboxItemRepository = Depends(get_inbox_repo),
    inbox_status: str | None = Query(
        default=None,
        description=f"Filter by inbox status ({_VALID_FILTER_STR})",
    ),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page (1-100)"),
) -> InboxListResponse:
    """List Recruitment Inbox items with optional status filter.

    Filters:
    - needs_classification: Emails below policy threshold or exhausted retry.
    - needs_information: Emails needing additional information.
    - ready_for_review: Job Applications ready for HR review.
    - resolved: Items that have been handled.

    By default, returns all non-dismissed items. Dismissed items are
    excluded from the default list but retained for audit.
    """

    # Validate filter value if provided
    if inbox_status is not None:
        valid_values = {v.value for v in _VALID_FILTERS}
        if inbox_status not in valid_values:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid inbox_status '{inbox_status}'. Valid values: {_VALID_FILTER_STR}"
                ),
            )

    service = _build_inbox_service(session, inbox_repo)
    items, total = await service.list_inbox(
        inbox_status=inbox_status,
        page=page,
        page_size=page_size,
    )

    return InboxListResponse(
        items=[InboxItemResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{item_id}", response_model=InboxItemResponse)
async def get_inbox_item(
    item_id: UUID,
    current_user: HRUserDep,
    session: DbSessionDep,
    inbox_repo: RecruitmentInboxItemRepository = Depends(get_inbox_repo),
) -> InboxItemResponse:
    """Get a single Recruitment Inbox item with full detail.

    Shows prediction, calibrated confidence, evidence, source hints,
    attachment metadata, correction history, and dismissal audit.
    """

    service = _build_inbox_service(session, inbox_repo)
    try:
        item = await service.get_item(item_id)
    except RecruitmentInboxItemNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Recruitment Inbox item not found",
        )

    return InboxItemResponse.model_validate(item)


@router.post("/{item_id}/correct-intent", response_model=InboxItemResponse)
async def correct_inbox_intent(
    item_id: UUID,
    body: CorrectIntentRequest,
    current_user: HRUserDep,
    session: DbSessionDep,
    inbox_repo: RecruitmentInboxItemRepository = Depends(get_inbox_repo),
) -> InboxItemResponse:
    """Correct the routing intent of an inbox item.

    Records the correction in audit history and resolves the item.
    Also creates a CorrectionRecord for safe evaluation feedback.
    Dismissed items cannot be corrected.
    """

    service = _build_inbox_service(session, inbox_repo)
    eval_service = _build_evaluation_service(session)

    try:
        item = await service.correct_intent(
            item_id=item_id,
            corrected_intent=body.corrected_intent,
            user_id=current_user.id,
        )

        # Record a CorrectionRecord for safe evaluation feedback
        # using only safe metadata — no raw email body, thread, or attachments
        await eval_service.record_correction(
            source_type="inbox_item",
            source_id=item_id,
            prediction_intent=item.prediction_intent,
            corrected_intent=body.corrected_intent,
            corrected_by_user_id=current_user.id,
            confidence_raw=item.confidence_raw,
            confidence_calibrated=item.confidence_calibrated,
            previous_inbox_status=item.inbox_status,
            evidence=item.evidence,
            source_hints=item.source_hints,
        )

    except RecruitmentInboxItemNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Recruitment Inbox item not found",
        )
    except InboxItemDismissedError:
        raise HTTPException(
            status_code=409,
            detail="Cannot modify a dismissed inbox item",
        )

    return InboxItemResponse.model_validate(item)


@router.post("/{item_id}/dismiss", response_model=InboxItemResponse)
async def dismiss_inbox_item(
    item_id: UUID,
    current_user: HRUserDep,
    session: DbSessionDep,
    inbox_repo: RecruitmentInboxItemRepository = Depends(get_inbox_repo),
) -> InboxItemResponse:
    """Dismiss an inbox item.

    Dismissed items retain their audit record and are protected from
    worker retry recreation. The action is idempotent — dismissing an
    already dismissed item returns the current state without error.
    """

    service = _build_inbox_service(session, inbox_repo)
    try:
        item = await service.dismiss_item(
            item_id=item_id,
            user_id=current_user.id,
        )
    except RecruitmentInboxItemNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Recruitment Inbox item not found",
        )
    except InboxItemDismissedError:
        # Idempotent: already dismissed is not an error
        item = await service.get_item(item_id)

    return InboxItemResponse.model_validate(item)


@router.post("/{item_id}/split", response_model=SplitInboxItemResponse)
async def split_inbox_item(
    item_id: UUID,
    body: SplitInboxItemRequest,
    current_user: HRUserDep,
    session: DbSessionDep,
    inbox_repo: RecruitmentInboxItemRepository = Depends(get_inbox_repo),
) -> SplitInboxItemResponse:
    """Create one Job Application per HR-identified applicant."""
    service = _build_inbox_service(session, inbox_repo)
    try:
        applications = await service.split_item(
            item_id=item_id,
            applicants=[
                SplitApplicant(
                    name=applicant.name,
                    email=str(applicant.email) if applicant.email else None,
                    job_opening_id=applicant.job_opening_id,
                )
                for applicant in body.applicants
            ],
            source=body.source,
            user_id=current_user.id,
        )
    except RecruitmentInboxItemNotFoundError:
        raise HTTPException(status_code=404, detail="Recruitment Inbox item not found")
    except InboxItemDismissedError:
        raise HTTPException(status_code=409, detail="Cannot split a dismissed inbox item")

    return SplitInboxItemResponse(
        applications=[
            JobApplicationResponse.model_validate(application) for application in applications
        ]
    )


@router.post("/{item_id}/link-proposals", response_model=LinkProposalResponse)
async def propose_cross_thread_link(
    item_id: UUID,
    body: ProposeLinkRequest,
    current_user: HRUserDep,
    session: DbSessionDep,
    inbox_repo: RecruitmentInboxItemRepository = Depends(get_inbox_repo),
) -> LinkProposalResponse:
    """Propose, but do not apply, a link outside the Gmail thread."""
    service = _build_inbox_service(session, inbox_repo)
    try:
        proposal = await service.propose_cross_thread_link(
            item_id=item_id,
            target_job_application_id=body.target_job_application_id,
            user_id=current_user.id,
        )
    except (RecruitmentInboxItemNotFoundError, JobApplicationNotFoundError):
        raise HTTPException(status_code=404, detail="Inbox item or Job Application not found")
    except (InboxItemDismissedError, InvalidLinkProposalError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return LinkProposalResponse.model_validate(proposal)


@router.post(
    "/link-proposals/{proposal_id}/resolve",
    response_model=LinkProposalResponse,
)
async def resolve_cross_thread_link(
    proposal_id: UUID,
    body: ResolveLinkProposalRequest,
    current_user: HRUserDep,
    session: DbSessionDep,
    inbox_repo: RecruitmentInboxItemRepository = Depends(get_inbox_repo),
) -> LinkProposalResponse:
    """Confirm or reject a pending cross-thread link proposal."""
    service = _build_inbox_service(session, inbox_repo)
    try:
        proposal = await service.resolve_link_proposal(
            proposal_id=proposal_id,
            decision=body.decision,
            user_id=current_user.id,
        )
    except (
        LinkProposalNotFoundError,
        JobApplicationNotFoundError,
        RecruitmentInboxItemNotFoundError,
    ):
        raise HTTPException(status_code=404, detail="Link proposal or Job Application not found")
    except InvalidLinkProposalError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return LinkProposalResponse.model_validate(proposal)


# ---------------------------------------------------------------------------
# Evaluation endpoints (GH #187)
# ---------------------------------------------------------------------------


@router.get("/{item_id}/corrections", response_model=list[dict[str, object]])
async def list_item_corrections(
    item_id: UUID,
    current_user: HRUserDep,
    session: DbSessionDep,
) -> list[dict[str, object]]:
    """List all correction records for an inbox item."""
    eval_service = _build_evaluation_service(session)
    records = await eval_service.list_corrections_for_source(item_id)
    return [
        {
            "id": str(r.id),
            "prediction_intent": r.prediction_intent,
            "corrected_intent": r.corrected_intent,
            "confidence_raw": r.confidence_raw,
            "confidence_calibrated": r.confidence_calibrated,
            "corrected_by_user_id": str(r.corrected_by_user_id),
            "corrected_at": r.corrected_at.isoformat() if r.corrected_at else None,
            "model_version": r.model_version,
            "prompt_version": r.prompt_version,
            "policy_version": r.policy_version,
            "evaluation_status": r.evaluation_status,
            "triggers_online_learning": r.triggers_online_learning,
        }
        for r in records
    ]


@router.post(
    "/{item_id}/corrections/{correction_id}/select-for-evaluation",
    response_model=dict[str, object],
)
async def select_correction_for_evaluation(
    item_id: UUID,
    correction_id: UUID,
    current_user: HRUserDep,
    session: DbSessionDep,
) -> dict[str, object]:
    """Opt a correction record into evaluation for this inbox item."""
    eval_service = _build_evaluation_service(session)

    # Build redacted subject/snippet from the inbox item
    inbox_repo = RecruitmentInboxItemRepository(session)
    inbox_item = await inbox_repo.get_by_id(item_id)
    redacted_subject = _redact_email_field(inbox_item.subject if inbox_item else "")
    redacted_snippet = _redact_email_field(inbox_item.snippet if inbox_item else "")

    try:
        record = await eval_service.select_for_evaluation(
            correction_record_id=correction_id,
            redacted_subject=redacted_subject,
            redacted_snippet=redacted_snippet,
        )
    except CorrectionRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Correction record not found")

    return {
        "id": str(record.id),
        "evaluation_status": record.evaluation_status,
        "redacted_subject": record.redacted_subject,
        "redacted_snippet": record.redacted_snippet,
    }


def _redact_email_field(text: str) -> str:
    """Simple redaction: replace email addresses and phone numbers.

    This is a lightweight redaction pass applied to subject/snippet when
    opting into evaluation. Full PII redaction uses PIIRedactor for
    evaluation set samples.
    """
    # Redact email addresses
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[EMAIL]", text)
    # Redact phone numbers (Vietnamese: 10-11 digits, optional +84 prefix)
    text = re.sub(r"(\+84|0)\d{9,10}", "[PHONE]", text)
    return text
