"""Application service for Job Opening management in recruitment planning.

Provides CRUD operations and lifecycle state machine for Job Openings,
with full audit logging for every write action.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.recruitment.domain.entities import JobOpening
from src.modules.recruitment.domain.enums import JobOpeningStatus
from src.modules.recruitment.domain.exceptions import (
    JobOpeningInvalidStatusTransitionError,
    JobOpeningNotFoundError,
    PositionNotFoundError,
)
from src.modules.recruitment.infrastructure.audit_repository import log_audit
from src.modules.recruitment.infrastructure.repositories import JobOpeningRepository

logger = logging.getLogger(__name__)

# Valid status transitions for Job Opening
_JOB_OPENING_TRANSITIONS: dict[str, list[str]] = {
    JobOpeningStatus.DRAFT: [JobOpeningStatus.OPEN, JobOpeningStatus.CANCELLED],
    JobOpeningStatus.OPEN: [JobOpeningStatus.CLOSED, JobOpeningStatus.CANCELLED],
    JobOpeningStatus.CLOSED: [JobOpeningStatus.OPEN],
    JobOpeningStatus.CANCELLED: [],  # Terminal state
}


class JobOpeningService:
    """Application service for Job Opening CRUD and lifecycle operations.

    Manages the Job Opening lifecycle state machine and ensures audit
    logging for every write action.
    """

    def __init__(
        self,
        session: AsyncSession,
        job_opening_repo: JobOpeningRepository,
        user_id: UUID,
    ) -> None:
        """Initialize the service with dependencies.

        Args:
            session: The async database session.
            job_opening_repo: Repository for Job Opening persistence.
            user_id: UUID of the authenticated user performing the action.
        """
        self.session = session
        self.job_opening_repo = job_opening_repo
        self.user_id = user_id

    async def create_job_opening(
        self,
        title: str,
        position_id: UUID,
        target_headcount: int,
        description: str = "",
        status: str = JobOpeningStatus.DRAFT,
    ) -> JobOpening:
        """Create a new Job Opening.

        Args:
            title: Title for the Job Opening (required).
            position_id: UUID of the Position this opening is for (required).
            target_headcount: Number of people to hire (required, >= 1).
            description: Optional description of the Job Opening.
            status: Initial status (default: draft).

        Returns:
            The created JobOpening entity.

        Raises:
            PositionNotFoundError: If position_id does not reference an existing Position.
        """
        # Validate position exists
        from src.modules.employee.infrastructure.position_repository import PositionRepository

        position = await PositionRepository(self.session).get_by_id(position_id)
        if position is None:
            raise PositionNotFoundError(f"Position not found: {position_id}")

        job_opening = JobOpening(
            title=title,
            position_id=position_id,
            target_headcount=target_headcount,
            description=description,
            status=status,
        )

        # If status is open on creation, set opened_at
        if status == JobOpeningStatus.OPEN:
            job_opening.opened_at = datetime.now(UTC)

        created = await self.job_opening_repo.create(job_opening)

        # Audit log
        await log_audit(
            session=self.session,
            operation_type="job_opening_create",
            entity_type="job_opening",
            entity_id=created.id,
            user_id=self.user_id,
            new_value={
                "id": str(created.id),
                "title": created.title,
                "position_id": str(created.position_id),
                "target_headcount": created.target_headcount,
                "status": created.status,
            },
            change_summary=f"Created Job Opening '{created.title}'",
        )

        return created

    async def update_job_opening(
        self,
        job_opening_id: UUID,
        title: str | None = None,
        description: str | None = None,
        target_headcount: int | None = None,
    ) -> JobOpening:
        """Update an existing Job Opening's editable fields.

        Only title, description, and target_headcount can be updated.
        Status changes use the dedicated lifecycle methods.

        Args:
            job_opening_id: UUID of the Job Opening to update.
            title: New title (optional).
            description: New description (optional).
            target_headcount: New target headcount (optional, >= 1).

        Returns:
            The updated JobOpening entity.

        Raises:
            JobOpeningNotFoundError: If the Job Opening does not exist.
        """
        job_opening = await self.job_opening_repo.get_by_id(job_opening_id)
        if job_opening is None:
            raise JobOpeningNotFoundError(f"Job Opening not found: {job_opening_id}")

        previous_value = {
            "title": job_opening.title,
            "description": job_opening.description,
            "target_headcount": job_opening.target_headcount,
        }

        if title is not None:
            job_opening.title = title
        if description is not None:
            job_opening.description = description
        if target_headcount is not None:
            job_opening.target_headcount = target_headcount

        updated = await self.job_opening_repo.update(job_opening)

        # Audit log
        await log_audit(
            session=self.session,
            operation_type="job_opening_update",
            entity_type="job_opening",
            entity_id=updated.id,
            user_id=self.user_id,
            previous_value=previous_value,
            new_value={
                "title": updated.title,
                "description": updated.description,
                "target_headcount": updated.target_headcount,
            },
            change_summary=f"Updated Job Opening '{updated.title}'",
        )

        return updated

    async def get_job_opening(self, job_opening_id: UUID) -> JobOpening:
        """Get a Job Opening by ID.

        Args:
            job_opening_id: UUID of the Job Opening.

        Returns:
            The JobOpening entity.

        Raises:
            JobOpeningNotFoundError: If the Job Opening does not exist.
        """
        job_opening = await self.job_opening_repo.get_by_id(job_opening_id)
        if job_opening is None:
            raise JobOpeningNotFoundError(f"Job Opening not found: {job_opening_id}")
        return job_opening

    async def list_job_openings(
        self,
        status: list[str] | None = None,
        position_id: UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[JobOpening], int]:
        """List Job Openings with optional filters.

        Args:
            status: Optional list of status values to filter by.
            position_id: Optional position UUID to filter by.
            search: Optional text to search in title.
            page: Page number (1-indexed).
            page_size: Number of items per page.

        Returns:
            A tuple of (list of JobOpening entities, total count).
        """
        return await self.job_opening_repo.list_job_openings(
            status=status,
            position_id=position_id,
            search=search,
            page=page,
            page_size=page_size,
        )

    async def _transition_status(
        self,
        job_opening_id: UUID,
        target_status: str,
        action_name: str,
    ) -> JobOpening:
        """Transition a Job Opening to a new status with validation.

        Args:
            job_opening_id: UUID of the Job Opening.
            target_status: The target status to transition to.
            action_name: Name of the action for audit logging.

        Returns:
            The updated JobOpening entity.

        Raises:
            JobOpeningNotFoundError: If the Job Opening does not exist.
            JobOpeningInvalidStatusTransitionError: If the transition is not allowed.
        """
        job_opening = await self.job_opening_repo.get_by_id(job_opening_id)
        if job_opening is None:
            raise JobOpeningNotFoundError(f"Job Opening not found: {job_opening_id}")

        current_status = job_opening.status
        allowed_transitions = _JOB_OPENING_TRANSITIONS.get(current_status, [])

        if target_status not in allowed_transitions:
            raise JobOpeningInvalidStatusTransitionError(current_status, action_name)

        previous_status = current_status
        job_opening.status = target_status

        # Set timestamp fields based on target status
        now = datetime.now(UTC)
        if target_status == JobOpeningStatus.OPEN:
            job_opening.opened_at = now
            # Clear closed/cancelled timestamps when reopening
            job_opening.closed_at = None
            job_opening.cancelled_at = None
        elif target_status == JobOpeningStatus.CLOSED:
            job_opening.closed_at = now
        elif target_status == JobOpeningStatus.CANCELLED:
            job_opening.cancelled_at = now

        updated = await self.job_opening_repo.update(job_opening)

        # Audit log
        await log_audit(
            session=self.session,
            operation_type=f"job_opening_{action_name}",
            entity_type="job_opening",
            entity_id=updated.id,
            user_id=self.user_id,
            previous_value={"status": previous_status},
            new_value={"status": updated.status},
            change_summary=(
                f"Job Opening '{updated.title}' {action_name}: {previous_status} → {updated.status}"
            ),
        )

        return updated

    async def open_job_opening(self, job_opening_id: UUID) -> JobOpening:
        """Open a Job Opening for applications.

        Allowed from: draft, closed (reopen).

        Args:
            job_opening_id: UUID of the Job Opening.

        Returns:
            The updated JobOpening entity.
        """
        return await self._transition_status(job_opening_id, JobOpeningStatus.OPEN, "open")

    async def close_job_opening(self, job_opening_id: UUID) -> JobOpening:
        """Close a Job Opening (filled or no longer hiring).

        Allowed from: open.

        Args:
            job_opening_id: UUID of the Job Opening.

        Returns:
            The updated JobOpening entity.
        """
        return await self._transition_status(job_opening_id, JobOpeningStatus.CLOSED, "close")

    async def cancel_job_opening(self, job_opening_id: UUID) -> JobOpening:
        """Cancel a Job Opening (terminal state).

        Allowed from: draft, open.

        Args:
            job_opening_id: UUID of the Job Opening.

        Returns:
            The updated JobOpening entity.
        """
        return await self._transition_status(job_opening_id, JobOpeningStatus.CANCELLED, "cancel")

    async def get_candidate_counts(
        self,
        job_opening_ids: list[UUID],
    ) -> dict[UUID, dict[str, int]]:
        """Return per-status Candidate counts for a batch of Job Openings."""
        return await self.job_opening_repo.count_candidates_by_status(job_opening_ids)

    async def get_headcount_status(
        self,
        job_opening_id: UUID,
    ) -> dict[str, int | bool]:
        """Compute headcount status for a Job Opening.

        Returns a dict with:
        - accepted_count: number of accepted candidates
        - target_headcount: the Job Opening's target
        - remaining: target - accepted (can be negative for overfill)
        - filled: True if accepted >= target
        - overfilled: True if accepted > target

        Args:
            job_opening_id: UUID of the Job Opening.

        Returns:
            Dict with accepted_count, target_headcount, remaining, filled, overfilled.
        """
        job_opening = await self.job_opening_repo.get_by_id(job_opening_id)
        if job_opening is None:
            raise JobOpeningNotFoundError(f"Job Opening not found: {job_opening_id}")

        accepted_count = await self.job_opening_repo.count_accepted_by_job_opening(job_opening_id)
        target = job_opening.target_headcount
        remaining = target - accepted_count

        return {
            "accepted_count": accepted_count,
            "target_headcount": target,
            "remaining": remaining,
            "filled": accepted_count >= target,
            "overfilled": accepted_count > target,
        }

    async def increment_accepted_count(self, job_opening_id: UUID) -> int:
        """Increment accepted count when a candidate is accepted.

        This is called from the CandidateService when a candidate assigned
        to this Job Opening is accepted. Returns the new accepted count.

        Note: This does NOT need to be a transactional increment since we're
        computing from the Candidate table at query time, not maintaining a
        denormalized counter on the Job Opening entity.

        Args:
            job_opening_id: UUID of the Job Opening.

        Returns:
            The new accepted count after the candidate acceptance.
        """
        # Just return the current count - it's computed from Candidate table
        return await self.job_opening_repo.count_accepted_by_job_opening(job_opening_id)

    async def get_summary_metrics(
        self,
    ) -> dict[str, int]:
        """Return summary metrics for Job Opening lifecycle states.

        Delegates to the repository for counts by status, using the
        JobOpeningStatus enum for consistency. Returns counts grouped
        by draft, open, closed, and cancelled.

        Returns:
            Dict with total_job_openings, draft_count, open_count,
            closed_count, cancelled_count.
        """
        status_counts = await self.job_opening_repo.count_job_openings_by_status()
        total = sum(status_counts.values())
        return {
            "total_job_openings": total,
            "draft_count": status_counts.get("draft", 0),
            "open_count": status_counts.get("open", 0),
            "closed_count": status_counts.get("closed", 0),
            "cancelled_count": status_counts.get("cancelled", 0),
        }
