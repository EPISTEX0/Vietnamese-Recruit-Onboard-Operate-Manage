"""Domain exceptions that carry a context-specific message must keep it.

Commit 8adce16 ("i18n: migrate all pages to next-intl") replaced
``exc.message`` with ``get_message(exc.error_code, lang)`` in all nine module
error handlers. That localised the *class default* messages but also
discarded every dynamically-built message -- the offending CIDR, the payslip
id, the reason a promotion was blocked -- because the catalog is keyed by
error code alone and cannot carry per-instance context.

``resolve_error_message`` restores the guarantee the exception docstrings
already promise ("Optional custom message override") while keeping the
catalog translation for the default path.
"""

from src.modules.attendance.domain.exceptions import (
    AlreadyCheckedInError,
    InvalidCidrError,
)
from src.modules.payslip.domain.exceptions import PayslipNotFoundError
from src.modules.recruitment.domain.exceptions import (
    CandidateNotFoundError,
    JobApplicationPromotionBlockedError,
)
from src.shared.messages import get_message, resolve_error_message


class TestCatalogDefaultPath:
    """Without a caller-supplied message the localised catalog entry wins."""

    def test_uses_vietnamese_catalog_entry_by_default(self) -> None:
        exc = AlreadyCheckedInError()
        assert resolve_error_message(exc, "vi") == get_message("ALREADY_CHECKED_IN", "vi")

    def test_honours_english_language_selection(self) -> None:
        exc = AlreadyCheckedInError()
        assert resolve_error_message(exc, "en") == get_message("ALREADY_CHECKED_IN", "en")

    def test_catalog_entry_is_not_the_bare_error_code(self) -> None:
        """Guards the assertions above against a silent catalog-miss fallback."""
        exc = AlreadyCheckedInError()
        assert resolve_error_message(exc, "vi") != "ALREADY_CHECKED_IN"


class TestDeclaredContextIsInterpolated:
    """Context a raise site declared safe lands inside the translated frame."""

    def test_subclass_declared_value_appears_in_the_message(self) -> None:
        exc = InvalidCidrError("not-a-cidr")
        assert "not-a-cidr" in resolve_error_message(exc, "vi")

    def test_context_survives_in_both_languages(self) -> None:
        exc = PayslipNotFoundError("ps-42")
        assert "ps-42" in resolve_error_message(exc, "en")
        assert "ps-42" in resolve_error_message(exc, "vi")

    def test_frame_around_the_context_is_still_translated(self) -> None:
        """The regression this guards: context used to disable localization."""
        exc = PayslipNotFoundError("ps-42")
        assert resolve_error_message(exc, "vi") != resolve_error_message(exc, "en")
        assert resolve_error_message(exc, "vi").startswith("Không tìm thấy")

    def test_message_ref_context_is_localized_too(self) -> None:
        """A reason from a closed set translates with the sentence it sits in."""
        exc = JobApplicationPromotionBlockedError(
            "applicant_email is required", reason_code="PROMOTION_BLOCKED_EMAIL_REQUIRED"
        )
        assert "thiếu email ứng viên" in resolve_error_message(exc, "vi")
        assert "applicant email is required" in resolve_error_message(exc, "en")


class TestUndeclaredMessageIsWithheld:
    """A free-form instance message is log-only; it never reaches the body."""

    def test_arbitrary_constructor_message_is_not_echoed(self) -> None:
        exc = CandidateNotFoundError("Candidate abc123 not found")
        rendered = resolve_error_message(exc, "vi")
        assert rendered == get_message("CANDIDATE_NOT_FOUND", "vi")
        assert "abc123" not in rendered

    def test_message_is_still_available_for_logging(self) -> None:
        exc = CandidateNotFoundError("Candidate abc123 not found")
        assert exc.message == "Candidate abc123 not found"


class TestUncatalogedErrorCode:
    """A code missing from the catalog must not degrade to the raw code."""

    def test_falls_back_to_the_class_default_not_the_code(self) -> None:
        exc = CandidateNotFoundError()
        exc.error_code = "NOT_IN_CATALOG"
        assert get_message("NOT_IN_CATALOG", "vi") == "NOT_IN_CATALOG"
        assert resolve_error_message(exc, "vi") == CandidateNotFoundError.message

    def test_fallback_uses_the_class_default_even_when_an_instance_message_is_set(
        self,
    ) -> None:
        """The uncataloged path is the one an infra wrapper would take."""
        exc = CandidateNotFoundError("host=internal.svc reqid=42")
        exc.error_code = "NOT_IN_CATALOG"
        assert "internal.svc" not in resolve_error_message(exc, "vi")
