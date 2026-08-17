"""Repositories for Recruitment module entities.

Provides async database access for Candidate and CVDocument entities
using SQLAlchemy async sessions with SQLModel. Follows the same
patterns established in the employee module.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Text, desc, func, insert, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Load, load_only
from sqlmodel import select

from src.modules.recruitment.domain.entities import (
    CalendarConflict,
    Candidate,
    CorrectionRecord,
    CVDocument,
    EvaluationSample,
    EvaluationSet,
    Interview,
    InterviewParticipant,
    JobApplication,
    JobApplicationLinkProposal,
    JobOpening,
    RecruitmentInboxItem,
)
from src.modules.recruitment.domain.enums import (
    CalendarConflictStatus,
    CandidateStatus,
    JobOpeningStatus,
    ProcessingStatus,
)


def _candidate_read_projection() -> Load:
    """Load only columns still stored on ``candidates`` after migration 054."""
    return load_only(
        Candidate.id,
        Candidate.name,
        Candidate.email,
        Candidate.phone,
        Candidate.skills,
        Candidate.experience,
        Candidate.education,
        Candidate.summary,
        Candidate.parsed_cv_json,
        Candidate.status,
        Candidate.confidence_score,
        Candidate.source_email_message_id,
        Candidate.rejection_reason,
        Candidate.rejected_at,
        Candidate.accepted_at,
        Candidate.archived_at,
        Candidate.created_at,
        Candidate.updated_at,
        Candidate.job_opening_id,
    )


class CandidateRepository:
    """Handles Candidate entity persistence using async SQLAlchemy sessions.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def create(self, candidate: Candidate) -> Candidate:
        """Persist a new candidate entity to the database.

        Args:
            candidate: The Candidate entity to create.

        Returns:
            The persisted Candidate entity with generated fields populated.
        """
        values = candidate.model_dump()
        await self.session.execute(insert(Candidate).values(values))
        return candidate

    async def get_by_id(self, id: UUID) -> Candidate | None:
        """Retrieve a candidate by their unique identifier.

        Args:
            id: The UUID primary key of the candidate.

        Returns:
            The Candidate entity if found, None otherwise.
        """
        statement = (
            select(Candidate).options(_candidate_read_projection()).where(Candidate.id == id)
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_id_for_update(self, id: UUID) -> Candidate | None:
        statement = (
            select(Candidate)
            .options(_candidate_read_projection())
            .where(Candidate.id == id)
            .with_for_update()
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def find_by_email(self, email: str) -> Candidate | None:
        """Retrieve a candidate by email address (case-insensitive).

        Used for deduplication when processing new CVs.

        Args:
            email: The email address to search for.

        Returns:
            The Candidate entity if found, None otherwise.
        """
        statement = (
            select(Candidate)
            .options(_candidate_read_projection())
            .where(func.lower(Candidate.email) == email.lower())
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def update(self, candidate: Candidate) -> Candidate:
        """Update an existing candidate entity.

        Updates the updated_at timestamp automatically.

        Args:
            candidate: The Candidate entity with updated fields.

        Returns:
            The updated Candidate entity.
        """
        candidate.updated_at = datetime.now(UTC)
        self.session.add(candidate)
        await self.session.flush()
        return candidate

    async def delete(self, id: UUID) -> None:
        """Hard-delete a candidate from the database.

        Args:
            id: The UUID of the candidate to delete.
        """
        statement = (
            select(Candidate).options(_candidate_read_projection()).where(Candidate.id == id)
        )
        result = await self.session.execute(statement)
        candidate = result.scalars().first()

        if candidate is not None:
            await self.session.delete(candidate)
            await self.session.flush()

    async def list_candidates(
        self,
        status: list[str] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        min_confidence: float | None = None,
        skills: list[str] | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Candidate], int]:
        """Retrieve a paginated list of candidates with optional filters.

        Archived candidates are excluded by default unless the status
        filter explicitly includes "archived".

        Args:
            status: Optional list of status values to filter by.
            date_from: Optional start date for created_at range filter.
            date_to: Optional end date for created_at range filter.
            min_confidence: Optional minimum confidence score filter.
            skills: Optional list of skills to filter by (OR logic, case-insensitive).
            search: Optional text to search in name, email, phone, skills
                (case-insensitive partial match).
            page: The page number (1-indexed).
            page_size: Number of items per page.

        Returns:
            A tuple of (list of Candidate entities, total count).
        """
        # Candidate retains legacy scheduling attributes in the ORM model for
        # compatibility, while those columns were moved to ``interviews``.
        # Restrict this read projection to columns that exist on candidates.
        statement = select(Candidate).options(
            load_only(
                Candidate.id,
                Candidate.name,
                Candidate.email,
                Candidate.phone,
                Candidate.skills,
                Candidate.status,
                Candidate.confidence_score,
                Candidate.created_at,
                Candidate.job_opening_id,
            )
        )
        count_statement = select(func.count()).select_from(Candidate)

        # Exclude archived candidates unless explicitly requested
        if status is not None:
            statement = statement.where(Candidate.status.in_(status))  # type: ignore[attr-defined]
            count_statement = count_statement.where(Candidate.status.in_(status))  # type: ignore[attr-defined]
        else:
            statement = statement.where(Candidate.status != CandidateStatus.ARCHIVED)
            count_statement = count_statement.where(Candidate.status != CandidateStatus.ARCHIVED)

        # Apply date range filter
        if date_from is not None:
            statement = statement.where(Candidate.created_at >= date_from)
            count_statement = count_statement.where(Candidate.created_at >= date_from)

        if date_to is not None:
            statement = statement.where(Candidate.created_at <= date_to)
            count_statement = count_statement.where(Candidate.created_at <= date_to)

        # Apply minimum confidence filter
        if min_confidence is not None:
            statement = statement.where(Candidate.confidence_score >= min_confidence)
            count_statement = count_statement.where(Candidate.confidence_score >= min_confidence)

        # Apply skills filter (OR logic, case-insensitive)
        # Cast JSONB array to text and use ilike for partial matching
        if skills:
            skills_conditions = []
            for skill in skills:
                skill_pattern = f"%{skill.lower()}%"
                skills_conditions.append(
                    func.lower(
                        func.cast(Candidate.skills, Text())  # type: ignore[arg-type]
                    ).ilike(skill_pattern)
                )
            skills_filter = or_(*skills_conditions)
            statement = statement.where(skills_filter)
            count_statement = count_statement.where(skills_filter)

        # Apply text search filter (case-insensitive partial match)
        if search:
            search_term = f"%{search.lower()}%"
            search_conditions = [
                func.lower(Candidate.name).ilike(search_term),
                func.lower(Candidate.email).ilike(search_term),
                func.lower(Candidate.phone).ilike(search_term),
                # Search within skills JSONB array cast to text
                func.lower(func.cast(Candidate.skills, Text())).ilike(  # type: ignore[arg-type]
                    search_term
                ),
            ]
            search_filter = or_(*search_conditions)
            statement = statement.where(search_filter)
            count_statement = count_statement.where(search_filter)

        # Get total count
        count_result = await self.session.execute(count_statement)
        total = count_result.scalar() or 0

        # Apply sorting and pagination
        offset = (page - 1) * page_size
        statement = statement.order_by(desc(Candidate.created_at))  # type: ignore[arg-type]
        statement = statement.offset(offset).limit(page_size)

        # Execute query
        result = await self.session.execute(statement)
        candidates = list(result.scalars().all())

        return candidates, total


class CVDocumentRepository:
    """Handles CVDocument entity persistence using async SQLAlchemy sessions.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def create(self, doc: CVDocument) -> CVDocument:
        """Persist a new CV document entity to the database.

        Args:
            doc: The CVDocument entity to create.

        Returns:
            The persisted CVDocument entity with generated fields populated.
        """
        self.session.add(doc)
        await self.session.flush()
        return doc

    async def get_by_id(self, id: UUID) -> CVDocument | None:
        """Retrieve a CV document by its unique identifier.

        Args:
            id: The UUID primary key of the CV document.

        Returns:
            The CVDocument entity if found, None otherwise.
        """
        statement = select(CVDocument).where(CVDocument.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def find_by_candidate_id(self, candidate_id: UUID) -> list[CVDocument]:
        """Retrieve all CV documents for a given candidate.

        Results are ordered by created_at descending.

        Args:
            candidate_id: The UUID of the candidate.

        Returns:
            A list of CVDocument entities for the candidate.
        """
        statement = (
            select(CVDocument)
            .where(CVDocument.candidate_id == candidate_id)
            .order_by(desc(CVDocument.created_at))  # type: ignore[arg-type]
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def find_needs_review(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[CVDocument], int]:
        """Retrieve paginated CV documents that need manual review.

        Returns documents with processing_status in ("needs_review", "failed"),
        ordered by created_at descending.

        Args:
            page: The page number (1-indexed).
            page_size: Number of items per page.

        Returns:
            A tuple of (list of CVDocument entities, total count).
        """
        review_statuses = [
            ProcessingStatus.NEEDS_REVIEW,
            ProcessingStatus.FAILED,
            ProcessingStatus.AI_UNAVAILABLE,
        ]

        statement = select(CVDocument).where(
            CVDocument.processing_status.in_(review_statuses)  # type: ignore[attr-defined]
        )
        count_statement = (
            select(func.count())
            .select_from(CVDocument)
            .where(
                CVDocument.processing_status.in_(review_statuses)  # type: ignore[attr-defined]
            )
        )

        # Get total count
        count_result = await self.session.execute(count_statement)
        total = count_result.scalar() or 0

        # Apply sorting and pagination
        offset = (page - 1) * page_size
        statement = statement.order_by(desc(CVDocument.created_at))  # type: ignore[arg-type]
        statement = statement.offset(offset).limit(page_size)

        # Execute query
        result = await self.session.execute(statement)
        documents = list(result.scalars().all())

        return documents, total

    async def find_by_gmail_message_id(self, gmail_message_id: str) -> list[CVDocument]:
        """Retrieve all CV documents associated with a Gmail message ID.

        Args:
            gmail_message_id: The Gmail message identifier string.

        Returns:
            A list of CVDocument entities for the given message.
        """
        statement = (
            select(CVDocument)
            .where(CVDocument.gmail_message_id == gmail_message_id)
            .order_by(desc(CVDocument.created_at))  # type: ignore[arg-type]
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def update(self, doc: CVDocument) -> CVDocument:
        """Update an existing CV document entity.

        Updates the updated_at timestamp automatically.

        Args:
            doc: The CVDocument entity with updated fields.

        Returns:
            The updated CVDocument entity.
        """
        doc.updated_at = datetime.now(UTC)
        self.session.add(doc)
        await self.session.flush()
        return doc

    async def find_by_checksum(self, checksum: str) -> CVDocument | None:
        """Retrieve the first CV document matching a SHA-256 checksum.

        Used for idempotent attachment processing: if a document with the
        same checksum already exists it can be returned without re-processing.

        Args:
            checksum: The hex-encoded SHA-256 digest of the attachment bytes.

        Returns:
            The CVDocument entity if found, None otherwise.
        """
        statement = select(CVDocument).where(CVDocument.checksum == checksum)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def delete(self, id: UUID) -> None:
        """Hard-delete a CV document from the database.

        Args:
            id: The UUID of the CV document to delete.
        """
        statement = select(CVDocument).where(CVDocument.id == id)
        result = await self.session.execute(statement)
        doc = result.scalars().first()

        if doc is not None:
            await self.session.delete(doc)
            await self.session.flush()


class JobOpeningRepository:
    """Handles JobOpening entity persistence using async SQLAlchemy sessions.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def create(self, job_opening: JobOpening) -> JobOpening:
        """Persist a new Job Opening entity to the database.

        Args:
            job_opening: The JobOpening entity to create.

        Returns:
            The persisted JobOpening entity with generated fields populated.
        """
        self.session.add(job_opening)
        await self.session.flush()
        return job_opening

    async def get_by_id(self, id: UUID) -> JobOpening | None:
        """Retrieve a Job Opening by its unique identifier.

        Args:
            id: The UUID primary key of the Job Opening.

        Returns:
            The JobOpening entity if found, None otherwise.
        """
        statement = select(JobOpening).where(JobOpening.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def update(self, job_opening: JobOpening) -> JobOpening:
        """Update an existing Job Opening entity.

        Updates the updated_at timestamp automatically.

        Args:
            job_opening: The JobOpening entity with updated fields.

        Returns:
            The updated JobOpening entity.
        """
        job_opening.updated_at = datetime.now(UTC)
        self.session.add(job_opening)
        await self.session.flush()
        return job_opening

    async def list_job_openings(
        self,
        status: list[str] | None = None,
        position_id: UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[JobOpening], int]:
        """Retrieve a paginated list of Job Openings with optional filters.

        Args:
            status: Optional list of status values to filter by.
            position_id: Optional position UUID to filter by.
            search: Optional text to search in title (case-insensitive partial match).
            page: The page number (1-indexed).
            page_size: Number of items per page.

        Returns:
            A tuple of (list of JobOpening entities, total count).
        """
        statement = select(JobOpening)
        count_statement = select(func.count()).select_from(JobOpening)

        # Apply status filter
        if status is not None:
            statement = statement.where(JobOpening.status.in_(status))  # type: ignore[attr-defined]
            count_statement = count_statement.where(JobOpening.status.in_(status))  # type: ignore[attr-defined]

        # Apply position filter
        if position_id is not None:
            statement = statement.where(JobOpening.position_id == position_id)
            count_statement = count_statement.where(JobOpening.position_id == position_id)

        # Apply text search filter
        if search:
            search_term = f"%{search.lower()}%"
            statement = statement.where(func.lower(JobOpening.title).ilike(search_term))
            count_statement = count_statement.where(func.lower(JobOpening.title).ilike(search_term))

        # Get total count
        count_result = await self.session.execute(count_statement)
        total = count_result.scalar() or 0

        # Apply sorting and pagination
        offset = (page - 1) * page_size
        statement = statement.order_by(desc(JobOpening.created_at))  # type: ignore[arg-type]
        statement = statement.offset(offset).limit(page_size)

        # Execute query
        result = await self.session.execute(statement)
        job_openings = list(result.scalars().all())

        return job_openings, total

    async def count_candidates_by_status(
        self,
        job_opening_ids: list[UUID],
    ) -> dict[UUID, dict[str, int]]:
        """Return candidate counts per status for a batch of Job Opening IDs."""
        if not job_opening_ids:
            return {}

        from collections import defaultdict

        from sqlmodel import select as sqlmodel_select

        stmt = (
            sqlmodel_select(
                Candidate.job_opening_id,
                Candidate.status,
                func.count().label("cnt"),
            )
            .where(Candidate.job_opening_id.in_(job_opening_ids))  # type: ignore[union-attr]
            .group_by(Candidate.job_opening_id, Candidate.status)  # type: ignore[arg-type]
            .order_by(Candidate.job_opening_id)  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        counts: dict[UUID, dict[str, int]] = defaultdict(dict)
        for row in rows:
            jo_id, status, cnt = row
            counts[jo_id][status] = cnt

        for jo_id in job_opening_ids:
            if jo_id not in counts:
                counts[jo_id] = {}

        return dict(counts)

    async def count_accepted_by_job_opening(
        self,
        job_opening_id: UUID,
    ) -> int:
        """Count accepted candidates for a specific Job Opening.

        Args:
            job_opening_id: The Job Opening ID to count accepted candidates for.

        Returns:
            Number of candidates with accepted status assigned to this Job Opening.
        """
        stmt = (
            select(func.count())
            .select_from(Candidate)
            .where(Candidate.job_opening_id == job_opening_id)
            .where(Candidate.status == CandidateStatus.ACCEPTED)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    async def count_job_openings_by_status(
        self,
    ) -> dict[str, int]:
        """Count Job Openings grouped by lifecycle status using JobOpeningStatus enum.

        Returns a dict with all four status keys (draft, open, closed, cancelled)
        mapping to the count of Job Openings in each status.

        Returns:
            Dict with status -> count mapping.
        """
        stmt = (
            select(
                JobOpening.status,
                func.count().label("cnt"),
            ).group_by(JobOpening.status)  # type: ignore[union-attr]
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        counts: dict[str, int] = {
            JobOpeningStatus.DRAFT: 0,
            JobOpeningStatus.OPEN: 0,
            JobOpeningStatus.CLOSED: 0,
            JobOpeningStatus.CANCELLED: 0,
        }
        for row in rows:
            status, cnt = row
            if status in counts:
                counts[status] = cnt

        return counts


class JobApplicationRepository:
    """Handles JobApplication entity persistence using async SQLAlchemy sessions.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def create(self, job_application: JobApplication) -> JobApplication:
        """Persist a Job Application inside an isolated savepoint."""
        async with self.session.begin_nested():
            self.session.add(job_application)
            await self.session.flush()
        return job_application

    async def get_by_id(self, id: UUID) -> JobApplication | None:
        """Retrieve a Job Application by its unique identifier.

        Args:
            id: The UUID primary key of the Job Application.

        Returns:
            The JobApplication entity if found, None otherwise.
        """
        statement = select(JobApplication).where(JobApplication.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_id_for_update(self, id: UUID) -> JobApplication | None:
        """Lock and return a Job Application for an atomic HR decision."""
        statement = select(JobApplication).where(JobApplication.id == id).with_for_update()
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_gmail_message_id(self, gmail_message_id: str) -> JobApplication | None:
        """Retrieve a Job Application by its Gmail message ID.

        Used for idempotent ingestion: returns the existing application
        if one was already created for this message.

        Args:
            gmail_message_id: The Gmail message identifier string.

        Returns:
            The JobApplication entity if found, None otherwise.
        """
        statement = select(JobApplication).where(
            JobApplication.gmail_message_id == gmail_message_id
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def list_by_gmail_thread_id(self, gmail_thread_id: str) -> list[JobApplication]:
        """Return applications already associated with a Gmail thread."""
        statement = select(JobApplication).where(JobApplication.gmail_thread_id == gmail_thread_id)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def update(self, job_application: JobApplication) -> JobApplication:
        """Update an existing Job Application entity.

        Updates the updated_at timestamp automatically.

        Args:
            job_application: The JobApplication entity with updated fields.

        Returns:
            The updated JobApplication entity.
        """
        job_application.updated_at = datetime.now(UTC)
        self.session.add(job_application)
        await self.session.flush()
        return job_application


class JobApplicationLinkProposalRepository:
    """Persist and resolve proposed cross-thread links."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, proposal: JobApplicationLinkProposal) -> JobApplicationLinkProposal:
        self.session.add(proposal)
        await self.session.flush()
        return proposal

    async def get_by_id(self, id: UUID) -> JobApplicationLinkProposal | None:
        statement = select(JobApplicationLinkProposal).where(JobApplicationLinkProposal.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def update(self, proposal: JobApplicationLinkProposal) -> JobApplicationLinkProposal:
        proposal.updated_at = datetime.now(UTC)
        self.session.add(proposal)
        await self.session.flush()
        return proposal


class CorrectionRecordRepository:
    """Handles CorrectionRecord entity persistence.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, record: CorrectionRecord) -> CorrectionRecord:
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_by_id(self, id: UUID) -> CorrectionRecord | None:
        statement = select(CorrectionRecord).where(CorrectionRecord.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_source_id(self, source_id: UUID) -> list[CorrectionRecord]:
        """Return all correction records for a given source.

        Args:
            source_id: UUID of the inbox item or job application.

        Returns:
            List of CorrectionRecord entities, newest first.
        """
        statement = (
            select(CorrectionRecord)
            .where(CorrectionRecord.source_id == source_id)
            .order_by(desc(CorrectionRecord.created_at))
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_by_evaluation_status(self, status: str) -> list[CorrectionRecord]:
        """Return correction records with a specific evaluation status.

        Args:
            status: The evaluation status to filter by.

        Returns:
            List of CorrectionRecord entities.
        """
        statement = (
            select(CorrectionRecord)
            .where(CorrectionRecord.evaluation_status == status)
            .order_by(desc(CorrectionRecord.created_at))
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def update(self, record: CorrectionRecord) -> CorrectionRecord:
        record.updated_at = datetime.now(UTC)
        self.session.add(record)
        await self.session.flush()
        return record


class EvaluationSetRepository:
    """Handles EvaluationSet entity persistence.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, evaluation_set: EvaluationSet) -> EvaluationSet:
        self.session.add(evaluation_set)
        await self.session.flush()
        return evaluation_set

    async def get_by_id(self, id: UUID) -> EvaluationSet | None:
        statement = select(EvaluationSet).where(EvaluationSet.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_version(self, version: str) -> EvaluationSet | None:
        statement = select(EvaluationSet).where(EvaluationSet.version == version)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def list_all(self) -> list[EvaluationSet]:
        statement = select(EvaluationSet).order_by(desc(EvaluationSet.created_at))
        result = await self.session.execute(statement)
        return list(result.scalars().all())


class EvaluationSampleRepository:
    """Handles EvaluationSample entity persistence.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, sample: EvaluationSample) -> EvaluationSample:
        self.session.add(sample)
        await self.session.flush()
        return sample

    async def get_by_id(self, id: UUID) -> EvaluationSample | None:
        statement = select(EvaluationSample).where(EvaluationSample.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def list_by_evaluation_set_id(self, evaluation_set_id: UUID) -> list[EvaluationSample]:
        statement = (
            select(EvaluationSample)
            .where(EvaluationSample.evaluation_set_id == evaluation_set_id)
            .order_by(EvaluationSample.created_at)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def list_by_correction_record_id(
        self, correction_record_id: UUID
    ) -> list[EvaluationSample]:
        statement = (
            select(EvaluationSample)
            .where(EvaluationSample.correction_record_id == correction_record_id)
            .order_by(EvaluationSample.created_at)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())


class RecruitmentInboxItemRepository:
    """Handles RecruitmentInboxItem entity persistence.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def create(self, item: RecruitmentInboxItem) -> RecruitmentInboxItem:
        """Persist a new Recruitment Inbox item.

        Args:
            item: The RecruitmentInboxItem entity to create.

        Returns:
            The persisted RecruitmentInboxItem with generated fields populated.
        """
        self.session.add(item)
        await self.session.flush()
        return item

    async def get_by_id(self, id: UUID) -> RecruitmentInboxItem | None:
        """Retrieve a Recruitment Inbox item by its unique identifier.

        Args:
            id: The UUID primary key of the inbox item.

        Returns:
            The RecruitmentInboxItem entity if found, None otherwise.
        """
        statement = select(RecruitmentInboxItem).where(RecruitmentInboxItem.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_gmail_message_id(self, gmail_message_id: str) -> RecruitmentInboxItem | None:
        """Retrieve an inbox item by its Gmail message ID.

        Used for idempotent creation.

        Args:
            gmail_message_id: The Gmail message identifier string.

        Returns:
            The RecruitmentInboxItem entity if found, None otherwise.
        """
        statement = select(RecruitmentInboxItem).where(
            RecruitmentInboxItem.gmail_message_id == gmail_message_id
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def update(self, item: RecruitmentInboxItem) -> RecruitmentInboxItem:
        """Update an existing Recruitment Inbox item.

        Updates the updated_at timestamp automatically.

        Args:
            item: The RecruitmentInboxItem entity with updated fields.

        Returns:
            The updated RecruitmentInboxItem entity.
        """
        item.updated_at = datetime.now(UTC)
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_by_status(
        self,
        inbox_status: str | None = None,
        dismissed: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[RecruitmentInboxItem], int]:
        """Retrieve a paginated list of inbox items with optional filters.

        Args:
            inbox_status: Filter by inbox status.
            dismissed: Filter by dismissed state.
            page: The page number (1-indexed).
            page_size: Number of items per page.

        Returns:
            A tuple of (list of entities, total count).
        """
        statement = select(RecruitmentInboxItem)
        count_statement = select(func.count()).select_from(RecruitmentInboxItem)

        if inbox_status is not None:
            statement = statement.where(RecruitmentInboxItem.inbox_status == inbox_status)
            count_statement = count_statement.where(
                RecruitmentInboxItem.inbox_status == inbox_status
            )

        if dismissed is not None:
            statement = statement.where(RecruitmentInboxItem.dismissed == dismissed)
            count_statement = count_statement.where(RecruitmentInboxItem.dismissed == dismissed)

        # Get total count
        count_result = await self.session.execute(count_statement)
        total = count_result.scalar() or 0

        # Apply sorting and pagination
        offset = (page - 1) * page_size
        statement = statement.order_by(desc(RecruitmentInboxItem.created_at))
        statement = statement.offset(offset).limit(page_size)

        # Execute query
        result = await self.session.execute(statement)
        items = list(result.scalars().all())

        return items, total

    async def find_dismissed_by_gmail_message_id(
        self, gmail_message_id: str
    ) -> RecruitmentInboxItem | None:
        """Check if a dismissed inbox item exists for a Gmail message.

        Used by the classification worker to prevent re-creation of dismissed items.

        Args:
            gmail_message_id: The Gmail message identifier string.

        Returns:
            The dismissed RecruitmentInboxItem if found, None otherwise.
        """
        statement = select(RecruitmentInboxItem).where(
            RecruitmentInboxItem.gmail_message_id == gmail_message_id,
            RecruitmentInboxItem.dismissed,
        )
        result = await self.session.execute(statement)
        return result.scalars().first()


class InterviewRepository:
    """Handles Interview entity persistence using async SQLAlchemy sessions.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def create(self, interview: Interview) -> Interview:
        """Persist a new Interview entity to the database.

        Args:
            interview: The Interview entity to create.

        Returns:
            The persisted Interview entity with generated fields populated.
        """
        self.session.add(interview)
        await self.session.flush()
        return interview

    async def get_by_id(self, id: UUID) -> Interview | None:
        """Retrieve an Interview by its unique identifier.

        Args:
            id: The UUID primary key of the Interview.

        Returns:
            The Interview entity if found, None otherwise.
        """
        statement = select(Interview).where(Interview.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_by_id_for_update(self, id: UUID) -> Interview | None:
        """Retrieve an Interview by UUID with a row-level lock.

        Args:
            id: The UUID primary key of the Interview.

        Returns:
            The Interview entity if found, None otherwise.
        """
        statement = select(Interview).where(Interview.id == id).with_for_update()
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def update(self, interview: Interview) -> Interview:
        """Update an existing Interview entity.

        Updates the updated_at timestamp automatically.

        Args:
            interview: The Interview entity with updated fields.

        Returns:
            The updated Interview entity.
        """
        interview.updated_at = datetime.now(UTC)
        self.session.add(interview)
        await self.session.flush()
        return interview

    async def find_by_candidate_id(self, candidate_id: UUID) -> list[Interview]:
        """Retrieve all interviews for a given candidate.

        Results are ordered by start_at descending.

        Args:
            candidate_id: The UUID of the candidate.

        Returns:
            A list of Interview entities for the candidate.
        """
        statement = (
            select(Interview)
            .where(Interview.candidate_id == candidate_id)
            .order_by(desc(Interview.start_at))  # type: ignore[arg-type]
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def find_by_candidate_and_event_id(
        self, candidate_id: UUID, event_id: str
    ) -> Interview | None:
        """Find an Interview by candidate ID and Calendar event ID.

        Args:
            candidate_id: The UUID of the candidate.
            event_id: The Google Calendar event ID.

        Returns:
            The Interview entity if found, None otherwise.
        """
        statement = select(Interview).where(
            Interview.candidate_id == candidate_id,
            Interview.calendar_event_id == event_id,
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_participants(self, interview_id: UUID) -> list[InterviewParticipant]:
        """Get all participants for an interview.

        Args:
            interview_id: The UUID of the interview.

        Returns:
            A list of InterviewParticipant entities.
        """
        statement = select(InterviewParticipant).where(
            InterviewParticipant.interview_id == interview_id
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def add_participant(self, participant: InterviewParticipant) -> InterviewParticipant:
        """Persist a new InterviewParticipant.

        Args:
            participant: The InterviewParticipant entity to create.

        Returns:
            The persisted InterviewParticipant entity.
        """
        self.session.add(participant)
        await self.session.flush()
        return participant

    async def delete(self, id: UUID) -> None:
        """Hard-delete an Interview from the database.

        Args:
            id: The UUID of the Interview to delete.
        """
        statement = select(Interview).where(Interview.id == id)
        result = await self.session.execute(statement)
        interview = result.scalars().first()

        if interview is not None:
            await self.session.delete(interview)
            await self.session.flush()


class CalendarConflictRepository:
    """Handles CalendarConflict entity persistence using async SQLAlchemy sessions.

    ``add()``/``flush()`` belong here, not in the application layer -- see
    "Luật `add()`/`flush()`" in ``WORKSPACE_PROTOCOL.md``. The application
    layer only calls ``commit()``/``rollback()`` on the entities this
    repository returns.

    Attributes:
        session: The async database session for executing queries.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with an async database session.

        Args:
            session: An SQLAlchemy AsyncSession instance for database operations.
        """
        self.session = session

    async def create(self, conflict: CalendarConflict) -> CalendarConflict:
        """Persist a new CalendarConflict entity to the database.

        Args:
            conflict: The CalendarConflict entity to create.

        Returns:
            The persisted CalendarConflict entity.
        """
        self.session.add(conflict)
        await self.session.flush()
        return conflict

    async def get_by_id(self, id: UUID) -> CalendarConflict | None:
        """Retrieve a calendar conflict by its unique identifier.

        Args:
            id: The UUID primary key of the CalendarConflict.

        Returns:
            The CalendarConflict entity if found, None otherwise.
        """
        statement = select(CalendarConflict).where(CalendarConflict.id == id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def list_conflicts(
        self,
        status: str | None = None,
        candidate_id: UUID | None = None,
    ) -> list[CalendarConflict]:
        """Retrieve calendar conflicts, optionally filtered by status or candidate.

        Args:
            status: Optional status filter. Defaults to "unresolved" when omitted.
            candidate_id: Optional candidate UUID to filter by.

        Returns:
            A list of CalendarConflict entities ordered newest first.
        """
        statement = select(CalendarConflict).order_by(desc(CalendarConflict.created_at))

        if status is not None:
            statement = statement.where(CalendarConflict.status == status)
        else:
            statement = statement.where(
                CalendarConflict.status == CalendarConflictStatus.UNRESOLVED
            )

        if candidate_id is not None:
            statement = statement.where(CalendarConflict.candidate_id == candidate_id)

        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def update(self, conflict: CalendarConflict) -> CalendarConflict:
        """Update an existing CalendarConflict entity.

        Updates the updated_at timestamp automatically.

        Args:
            conflict: The CalendarConflict entity with updated fields.

        Returns:
            The updated CalendarConflict entity.
        """
        conflict.updated_at = datetime.now(UTC)
        self.session.add(conflict)
        await self.session.flush()
        return conflict
