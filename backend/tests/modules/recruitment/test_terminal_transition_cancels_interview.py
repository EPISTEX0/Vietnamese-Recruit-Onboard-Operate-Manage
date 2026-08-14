"""Regression guard: a terminal transition must call off the booked interview.

Requirement R8 (reject/archive cancels the Candidate's Calendar event) was
silently dropped once already: the ``1dcb4a7`` refactor split ``CandidateService``
into ``CandidateLifecycleService`` and ``InterviewSchedulerService`` and the
cancellation side-effect did not survive the move. Nothing failed, because no
test forced the transitions to reach the Calendar. Rejecting a candidate left
their interview live on the interviewers' calendars for four months.

The interview-calendar property tests (18/19/20) cover the *behaviour* through
an in-memory harness, but they wire the canceller into the lifecycle service by
hand, so they would stay green if the production container stopped wiring it.
This module closes that last gap with two deterministic checks:

* Both terminal transitions -- reject AND archive -- reach the Calendar port and
  delete the exact booked event. Covering only one is how a half-restored
  version of this would slip through.
* The recruitment container actually hands the lifecycle service an
  ``InterviewCanceller``, so removing that wiring fails here rather than in
  production.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.modules.recruitment.application.candidate_lifecycle_service import (
    CandidateLifecycleService,
    InterviewCanceller,
)
from src.modules.recruitment.application.interview_scheduler_service import (
    InterviewSchedulerService,
)
from src.modules.recruitment.domain.enums import CandidateStatus
from tests.modules.recruitment._interview_support import (
    build_calendar_harness,
    make_candidate,
    make_interview,
)

_BOOKED_EVENT_ID = "evt-booked-for-cancellation"


@pytest.mark.parametrize("action", ["reject", "archive"])
async def test_terminal_transition_deletes_the_booked_calendar_event(action: str) -> None:
    """Reject and archive each delete the exact booked event and audit it.

    Parametrized rather than drawn, so a regression that breaks only one of the
    two transitions is reported as that transition failing.
    """
    candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
    booked = make_interview(candidate_id=candidate.id, calendar_event_id=_BOOKED_EVENT_ID)
    harness = build_calendar_harness(candidates=[candidate], interviews=[booked])

    with harness.audit_patch():
        if action == "reject":
            returned = await harness.lifecycle.reject_candidate(candidate.id, "position filled")
        else:
            returned = await harness.lifecycle.archive_candidate(candidate.id)

    expected_status = CandidateStatus.REJECTED if action == "reject" else CandidateStatus.ARCHIVED
    assert returned.status == expected_status

    # The Calendar port was actually reached, on the exact booked event.
    assert len(harness.calendar.delete_calls) == 1
    assert harness.calendar.delete_calls[0].event_id == _BOOKED_EVENT_ID

    # The interview is no longer scheduled on our side either.
    assert await harness.scheduled_interview(candidate.id) is None

    # And the cancellation is on the record, attributed to this trigger.
    cancelled = harness.audit_sink.entries_for("interview_event_cancelled")
    assert len(cancelled) == 1
    assert cancelled[0].new_value is not None
    assert cancelled[0].new_value["calendar_event_id"] == _BOOKED_EVENT_ID
    assert cancelled[0].new_value["trigger"] == action


async def test_lifecycle_without_a_canceller_still_transitions() -> None:
    """With no canceller wired, the transition still succeeds (no Calendar call).

    The seam is optional so the CV-processing pipeline can build a lifecycle
    service without Calendar dependencies; that path must not break.
    """
    candidate = make_candidate(status=CandidateStatus.INTERVIEW_SCHEDULED)
    harness = build_calendar_harness(candidates=[candidate])
    harness.lifecycle._interview_canceller = None

    with harness.audit_patch():
        returned = await harness.lifecycle.reject_candidate(candidate.id, "no calendar wiring")

    assert returned.status == CandidateStatus.REJECTED
    assert harness.calendar.was_called is False


async def test_container_wires_an_interview_canceller_into_the_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recruitment container hands the lifecycle service a canceller.

    The property tests wire this seam by hand, so without this check the
    container could stop providing it and every test would still pass -- which is
    precisely how R8 was lost the first time.
    """
    from src.modules.identity.container import get_crypto_utils, get_settings
    from src.modules.recruitment.container import get_candidate_lifecycle_service

    # The container reaches identity for the AES key; give it a throwaway one.
    monkeypatch.setenv("AUTH_GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("AUTH_GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("AUTH_JWT_SECRET_KEY", "test-jwt-secret")
    monkeypatch.setenv(
        "AUTH_OAUTH_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(b"\0" * 32).decode("ascii"),
    )
    # These factories are lru_cached; clear them so the env above is read, and
    # again afterwards so a real key cached here does not leak into other tests.
    get_settings.cache_clear()
    get_crypto_utils.cache_clear()

    try:
        service = await get_candidate_lifecycle_service(
            session=AsyncMock(),
            current_user=SimpleNamespace(id=uuid4()),
        )
    finally:
        get_settings.cache_clear()
        get_crypto_utils.cache_clear()

    assert isinstance(service, CandidateLifecycleService)
    canceller = service._interview_canceller
    assert canceller is not None, (
        "CandidateLifecycleService was built without an InterviewCanceller: "
        "rejecting or archiving a candidate would leave their interview on the "
        "interviewers' Google Calendar"
    )
    assert isinstance(canceller, InterviewCanceller)
    assert isinstance(canceller, InterviewSchedulerService)
