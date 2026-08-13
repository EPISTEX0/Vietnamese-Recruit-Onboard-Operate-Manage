"""Authenticated HR decisions for Job Applications.

This module is intentionally separate from the AI ingestion service so
classifier and automation code receive no Candidate-promotion interface.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.recruitment.domain.entities import Candidate, JobApplication
from src.modules.recruitment.domain.enums import (
    ApplicationSource,
    CandidateStatus,
    JobApplicationStatus,
    JobOpeningStatus,
)
from src.modules.recruitment.domain.exceptions import (
    JobApplicationAssignmentBlockedError,
    JobApplicationNotFoundError,
    JobApplicationPromotionBlockedError,
    JobOpeningNotFoundError,
    JobOpeningNotOpenError,
)
from src.modules.recruitment.infrastructure.repositories import (
    CandidateRepository,
    JobApplicationRepository,
    JobOpeningRepository,
)


class JobApplicationDecisionService:
    """Apply serialized, auditable HR assignment and promotion decisions."""

    def __init__(
        self,
        session: AsyncSession,
        job_application_repo: JobApplicationRepository,
        candidate_repo: CandidateRepository,
        job_opening_repo: JobOpeningRepository,
    ) -> None:
        self._session = session
        self._applications = job_application_repo
        self._candidates = candidate_repo
        self._job_openings = job_opening_repo

    async def correct_source(
        self,
        job_application_id: UUID,
        corrected_source: ApplicationSource,
        user_id: UUID | None = None,
    ) -> JobApplication:
        """Correct a Job Application source without changing its lifecycle."""
        application = await self._applications.get_by_id_for_update(job_application_id)
        if application is None:
            raise JobApplicationNotFoundError(f"Job Application not found: {job_application_id}")
        if application.status == JobApplicationStatus.DISMISSED:
            raise JobApplicationAssignmentBlockedError("Job Application is dismissed")

        previous_source = application.source
        application.source = corrected_source
        history = list(application.audit_history or [])
        history.append(
            {
                "action": "source_corrected",
                "previous_source": previous_source,
                "corrected_source": corrected_source,
                "performed_by_user_id": str(user_id) if user_id else None,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )
        application.audit_history = history
        updated = await self._applications.update(application)
        await self._session.commit()
        return updated

    async def assign_to_job_opening(
        self,
        job_application_id: UUID,
        job_opening_id: UUID | None,
        user_id: UUID | None = None,
    ) -> JobApplication:
        """Assign an unpromoted application to one open Job Opening, or none."""
        application = await self._applications.get_by_id_for_update(job_application_id)
        if application is None:
            raise JobApplicationNotFoundError(f"Job Application not found: {job_application_id}")
        if application.status == JobApplicationStatus.DISMISSED:
            raise JobApplicationAssignmentBlockedError("Job Application is dismissed")
        if application.status == JobApplicationStatus.PROMOTED:
            raise JobApplicationAssignmentBlockedError("Job Application is already promoted")

        if job_opening_id is not None:
            opening = await self._job_openings.get_by_id(job_opening_id)
            if opening is None:
                raise JobOpeningNotFoundError(f"Job Opening not found: {job_opening_id}")
            if opening.status != JobOpeningStatus.OPEN:
                raise JobOpeningNotOpenError(job_opening_id, opening.status)

        previous_id = application.job_opening_id
        application.job_opening_id = job_opening_id
        history = list(application.audit_history or [])
        history.append(
            {
                "action": "assignment_updated",
                "previous_job_opening_id": str(previous_id) if previous_id else None,
                "new_job_opening_id": str(job_opening_id) if job_opening_id else None,
                "performed_by_user_id": str(user_id) if user_id else None,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )
        application.audit_history = history
        updated = await self._applications.update(application)
        await self._session.commit()
        return updated

    async def promote_to_candidate(
        self,
        job_application_id: UUID,
        applicant_name: str,
        applicant_email: str,
        job_opening_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> tuple[JobApplication, Candidate]:
        """Promote once, returning the linked Candidate on repeated requests."""
        application = await self._applications.get_by_id_for_update(job_application_id)
        if application is None:
            raise JobApplicationNotFoundError(f"Job Application not found: {job_application_id}")
        if application.status == JobApplicationStatus.DISMISSED:
            raise JobApplicationPromotionBlockedError(
                "Job Application is dismissed", reason_code="PROMOTION_BLOCKED_DISMISSED"
            )

        name = applicant_name.strip()
        email = applicant_email.strip()
        if not name:
            raise JobApplicationPromotionBlockedError(
                "applicant_name is required", reason_code="PROMOTION_BLOCKED_NAME_REQUIRED"
            )
        if not email:
            raise JobApplicationPromotionBlockedError(
                "applicant_email is required", reason_code="PROMOTION_BLOCKED_EMAIL_REQUIRED"
            )

        if application.candidate_id is not None:
            candidate = await self._candidates.get_by_id(application.candidate_id)
            if candidate is None:
                raise JobApplicationPromotionBlockedError(
                    "linked Candidate no longer exists",
                    reason_code="PROMOTION_BLOCKED_CANDIDATE_GONE",
                )
            return application, candidate

        effective_opening_id = (
            job_opening_id if job_opening_id is not None else application.job_opening_id
        )
        if job_opening_id is not None:
            opening = await self._job_openings.get_by_id(job_opening_id)
            if opening is None:
                raise JobOpeningNotFoundError(f"Job Opening not found: {job_opening_id}")
            if opening.status != JobOpeningStatus.OPEN:
                raise JobOpeningNotOpenError(job_opening_id, opening.status)

        candidate = await self._candidates.create(
            Candidate(
                name=name,
                email=email,
                source_email_message_id=application.source_email_message_id,
                job_opening_id=effective_opening_id,
                status=CandidateStatus.NEW,
            )
        )
        previous_status = application.status
        application.applicant_name = name
        application.applicant_email = email
        application.job_opening_id = effective_opening_id
        application.candidate_id = candidate.id
        application.status = JobApplicationStatus.PROMOTED
        history = list(application.audit_history or [])
        history.append(
            {
                "action": "promoted_to_candidate",
                "candidate_id": str(candidate.id),
                "previous_status": previous_status,
                "new_status": JobApplicationStatus.PROMOTED,
                "applicant_name": name,
                "applicant_email": email,
                "job_opening_id": str(effective_opening_id) if effective_opening_id else None,
                "performed_by_user_id": str(user_id) if user_id else None,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )
        application.audit_history = history
        updated = await self._applications.update(application)
        await self._session.commit()
        return updated, candidate
