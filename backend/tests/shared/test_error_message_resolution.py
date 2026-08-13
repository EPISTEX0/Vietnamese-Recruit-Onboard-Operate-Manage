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


class TestCallerSuppliedMessageSurvives:
    """A message built by the domain layer carries context the catalog cannot."""

    def test_explicit_constructor_message_is_preserved(self) -> None:
        exc = CandidateNotFoundError("Candidate abc123 not found")
        assert resolve_error_message(exc, "vi") == "Candidate abc123 not found"

    def test_subclass_built_message_keeps_its_interpolated_value(self) -> None:
        exc = InvalidCidrError("not-a-cidr")
        assert "not-a-cidr" in resolve_error_message(exc, "vi")

    def test_preserved_message_ignores_language(self) -> None:
        """Interpolated context is not translatable, so lang must not drop it."""
        exc = PayslipNotFoundError("ps-42")
        assert "ps-42" in resolve_error_message(exc, "en")
        assert "ps-42" in resolve_error_message(exc, "vi")

    def test_reason_for_blocked_promotion_reaches_the_client(self) -> None:
        exc = JobApplicationPromotionBlockedError("applicant email is missing")
        assert "applicant email is missing" in resolve_error_message(exc, "vi")


class TestUncatalogedErrorCode:
    """A code missing from the catalog must not degrade to the raw code."""

    def test_falls_back_to_the_exception_message_not_the_code(self) -> None:
        exc = CandidateNotFoundError()
        exc.error_code = "NOT_IN_CATALOG"
        assert get_message("NOT_IN_CATALOG", "vi") == "NOT_IN_CATALOG"
        assert resolve_error_message(exc, "vi") == exc.message
