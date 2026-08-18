"""Logging for the one genuine silent handler among AI classifier's source_hints parsing (#386 C3).

``requires_hr_split`` reads a provider-supplied ``applicant_count``/
``number_of_applicants`` source hint and treats a non-numeric value as "no
split needed" (``False``) — indistinguishable from a legitimate single-
applicant email, and nothing upstream logs it: neither
``ClassificationService._apply_classification`` (which only logs when the
*callback* raises, not when the split decision itself is silently wrong) nor
``InboxService`` (which just reads the boolean to pick an ``inbox_status``).
"""

from __future__ import annotations

import logging

import pytest

from src.modules.gmail.domain.enums import EmailCategory
from src.modules.gmail.infrastructure.ai_classifier import ClassificationResult

LOGGER = "src.modules.gmail.infrastructure.ai_classifier"


def test_non_numeric_applicant_count_logs_warning_and_treats_as_no_split(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = ClassificationResult(
        category=EmailCategory.recruitment,
        confidence=0.9,
        source_hints=(("applicant_count", "several"),),
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        split = result.requires_hr_split

    assert split is False, "behaviour unchanged: malformed count still means no split"
    matching = [r for r in caplog.records if r.name == LOGGER]
    assert len(matching) == 1
    record = matching[0]
    assert record.levelno == logging.WARNING
    assert "applicant_count" in record.getMessage()
    assert "several" in record.getMessage()


def test_numeric_applicant_count_does_not_log(caplog: pytest.LogCaptureFixture) -> None:
    result = ClassificationResult(
        category=EmailCategory.recruitment,
        confidence=0.9,
        source_hints=(("applicant_count", "3"),),
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        split = result.requires_hr_split

    assert split is True
    assert [r for r in caplog.records if r.name == LOGGER] == []
