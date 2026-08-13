"""Tests for the completion lockout boundary in OnboardingService.

Once a process is complete, setup edits and task updates must be rejected
with OnboardingProcessAlreadyCompletedError.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.modules.identity.domain.entities import User, UserRole
from src.modules.onboarding.application.onboarding_service import OnboardingService
from src.modules.onboarding.domain.entities import OnboardingProcess
from src.modules.onboarding.domain.enums import OnboardingStatus, OnboardingTaskStatus
from src.modules.onboarding.domain.exceptions import OnboardingProcessAlreadyCompletedError


@pytest.mark.asyncio
async def test_update_setup_lockout_on_complete_process() -> None:
    process_id = uuid4()
    admin_user = User(id=uuid4(), email="admin@vroom.vn", role=UserRole.HR, name="Admin")

    completed_process = OnboardingProcess(
        id=process_id,
        candidate_id=uuid4(),
        employee_id=uuid4(),
        status=OnboardingStatus.COMPLETE.value,
    )

    process_repo = AsyncMock()
    process_repo.get_for_update.return_value = completed_process

    service = OnboardingService(
        process_repo=process_repo,
        task_repo=AsyncMock(),
        audit_repo=AsyncMock(),
        employee_repo=AsyncMock(),
        session=AsyncMock(),
    )

    with pytest.raises(OnboardingProcessAlreadyCompletedError):
        await service.update_employee_setup(
            process_id=process_id,
            actor=admin_user,
            data={"department_id": str(uuid4())},
        )


@pytest.mark.asyncio
async def test_task_completion_lockout_on_complete_process() -> None:
    process_id = uuid4()
    task_id = uuid4()
    admin_user = User(id=uuid4(), email="admin@vroom.vn", role=UserRole.HR, name="Admin")

    completed_process = OnboardingProcess(
        id=process_id,
        candidate_id=uuid4(),
        employee_id=uuid4(),
        status=OnboardingStatus.COMPLETE.value,
    )

    task = AsyncMock()
    task.id = task_id
    task.process_id = process_id
    task.status = OnboardingTaskStatus.PENDING.value

    task_repo = AsyncMock()
    task_repo.get_by_id.return_value = task

    process_repo = AsyncMock()
    process_repo.get_for_update.return_value = completed_process

    service = OnboardingService(
        process_repo=process_repo,
        task_repo=task_repo,
        audit_repo=AsyncMock(),
        employee_repo=AsyncMock(),
        session=AsyncMock(),
    )

    with pytest.raises(OnboardingProcessAlreadyCompletedError):
        await service.complete_task(
            task_id=task_id,
            actor=admin_user,
            status=OnboardingTaskStatus.DONE.value,
        )
