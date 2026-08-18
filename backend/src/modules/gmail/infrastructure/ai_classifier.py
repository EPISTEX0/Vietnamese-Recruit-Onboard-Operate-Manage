"""AI-based email classifier using LLM (Gemma 4) for the Gmail module.

Handles ambiguous emails that the rule-based classifier cannot confidently
categorize. Uses an OpenAI-compatible API endpoint with structured prompts
designed for Vietnamese HR email context.

Features:
- Retry with exponential backoff (1s, 2s, 4s) up to 3 attempts
- 15-second timeout per request
- Token usage tracking for cost monitoring
- Graceful fallback to uncategorized on failure
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from src.modules.gmail.domain.enums import EmailCategory
from src.modules.gmail.infrastructure.config import GmailSettings

logger = logging.getLogger(__name__)

_BACKOFF_DELAYS = [1, 2, 4]

# System prompt for email classification
_SYSTEM_PROMPT = """\
Bạn là hệ thống phân loại email cho phòng Nhân sự (HR) doanh nghiệp Việt Nam.

Phân loại email vào ĐÚNG MỘT category dưới đây:
- recruitment: CV ứng tuyển, giới thiệu ứng viên, headhunter, job board
- interview: Lịch phỏng vấn, xác nhận, feedback phỏng vấn
- offer: Offer letter, thương lượng lương, chấp nhận/từ chối offer
- onboarding: Nhân viên mới, tài liệu onboarding, ngày đầu làm việc
- leave_request: Xin nghỉ phép, nghỉ ốm, nghỉ thai sản, nghỉ không lương
- payroll: Hỏi lương, payslip, thuế TNCN, thưởng, phúc lợi, khấu trừ
- employee_request: Xin xác nhận, đổi thông tin, hỏi chính sách, đăng ký đào tạo
- resignation: Đơn nghỉ việc, offboarding, bàn giao, thông báo nghỉ việc
- complaint: Khiếu nại, phản ánh, conflict nội bộ, quấy rối
- vendor: Nhà cung cấp dịch vụ HR, training, event, teambuilding, báo giá
- insurance: BHXH, BHYT, bảo hiểm tư nhân, quyền lợi bảo hiểm
- internal: Thông báo nội bộ, phê duyệt, báo cáo quản lý, cuộc họp
- compliance: Thanh tra, kiểm toán, pháp lý lao động, quy định nhà nước
- notification: Thông báo hệ thống tự động, alerts, reminders
- uncategorized: Không xác định được loại email

Trả về ĐÚNG MỘT từ category (không giải thích, không dấu chấm, không ngoặc kép).\
"""


@dataclass
class ClassificationResult:
    """Result of AI email classification.

    Attributes:
        category: The assigned email category.
        confidence: Confidence score (0.0 - 1.0).
        source: Classification source ("rules" or "ai").
        matched_signals: Signals that contributed to classification.
        token_usage: Token usage from LLM call.
        source_hints: Structured metadata about the email source
            (e.g. (("sender_role", "candidate"), ("has_cv_attachment", "true"))).
            Populated by the provider classifier; empty for rule-only results.
            Backwards-compatible default is empty tuple.
    """

    category: EmailCategory
    confidence: float
    source: str = "ai"
    matched_signals: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    source_hints: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # Stable Job Application contract. ``category`` remains the legacy Gmail
    # routing value during expand/compatibility; these fields are independent.
    intent: str | None = None
    application_source: str | None = None
    has_cv: bool | None = None

    def __post_init__(self) -> None:
        if self.category == EmailCategory.recruitment:
            self.intent = self.intent or "job_application"

    @property
    def is_job_application(self) -> bool:
        """Return the stable intent without requiring callers to know legacy categories."""
        return self.intent == "job_application" or self.category.value in {
            "recruitment",
            "job_application",
        }

    @property
    def requires_hr_split(self) -> bool:
        """Whether provider evidence says one source contains several applicants."""
        for key, value in self.source_hints:
            normalized_key = key.lower()
            normalized_value = value.lower()
            if normalized_key in {"multiple_applicants", "contains_multiple_applicants"}:
                return normalized_value in {"true", "yes", "1"}
            if normalized_key in {"applicant_count", "number_of_applicants"}:
                try:
                    return int(normalized_value) > 1
                except ValueError:
                    logger.warning(
                        "Provider sent non-numeric %s source_hint: %r; treating as no split",
                        normalized_key,
                        value,
                    )
                    return False
        return False


class AIClassifier:
    """LLM-based email classifier using Gemma 4 via OpenAI-compatible API.

    Sends email metadata (subject, sender, snippet) to the LLM and parses
    the single-word category response. Implements retry with exponential
    backoff for transient failures.

    Args:
        settings: GmailSettings with LLM connection configuration.
    """

    def __init__(
        self,
        settings: GmailSettings,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        """Initialize the AI classifier with LLM client.

        Args:
            settings: Gmail module configuration with LLM settings.
            base_url: Override the LLM base URL from settings.
            api_key: Override the LLM API key from settings.
            model: Override the LLM model name from settings.
        """
        resolved_base_url = base_url or settings.classification_llm_base_url
        resolved_api_key = api_key or settings.classification_llm_api_key
        resolved_model = model or settings.classification_llm_model

        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            timeout=settings.classification_llm_timeout_seconds,
        )
        self._model = resolved_model

    async def classify(
        self,
        subject: str,
        sender_email: str,
        sender_name: str,
        snippet: str,
        has_attachments: bool = False,
    ) -> ClassificationResult:
        """Classify an email using LLM.

        Constructs a prompt with email metadata and asks the LLM to
        classify into one of the valid HR email categories.

        Args:
            subject: Email subject line.
            sender_email: Sender's email address.
            sender_name: Sender's display name.
            snippet: First 200 characters of email body.
            has_attachments: Whether the email has attachments.

        Returns:
            ClassificationResult with the classified category.

        Raises:
            AIClassificationError: If all retry attempts are exhausted.
        """
        user_prompt = self._build_prompt(
            subject, sender_email, sender_name, snippet, has_attachments
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        logger.info(
            "AI classify input: subject=%r sender=%r snippet=%r",
            subject[:60],
            sender_email,
            snippet[:80],
        )

        last_error: Exception | None = None
        max_retries = self._settings.classification_llm_max_retries

        for attempt in range(max_retries):
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,  # type: ignore[arg-type]
                        temperature=0.0,
                        max_tokens=1000,
                    ),
                    timeout=self._settings.classification_llm_timeout_seconds,
                )

                # Handle reasoning models and varied provider response shapes.
                # Some providers return dict-like choices instead of lists,
                # or nest the message differently.
                raw_content = self._extract_content_from_response(response)
                if not raw_content:
                    raise AIClassificationError("LLM returned empty content in response")

                token_usage = self._extract_token_usage(response)

                logger.info(
                    "AI classification result: raw=%r, tokens=%s",
                    raw_content[:50] if raw_content else "(empty)",
                    token_usage,
                )

                category = self._parse_category(raw_content)
                confidence = 0.85 if category != EmailCategory.uncategorized else 0.3

                return ClassificationResult(
                    category=category,
                    confidence=confidence,
                    source="ai",
                    matched_signals=[f"llm_response:{raw_content}"],
                    token_usage=token_usage,
                    has_cv=has_attachments,
                )

            except TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "AI classification timeout on attempt %d/%d",
                    attempt + 1,
                    max_retries,
                )
            except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
                last_error = exc
                logger.warning(
                    "AI classification error on attempt %d/%d: %s",
                    attempt + 1,
                    max_retries,
                    str(exc),
                )

            # Exponential backoff before retry
            if attempt < max_retries - 1:
                delay = _BACKOFF_DELAYS[min(attempt, len(_BACKOFF_DELAYS) - 1)]
                await asyncio.sleep(delay)

        raise AIClassificationError(
            f"AI classification failed after {max_retries} attempts: {last_error}"
        )

    def _extract_category_from_reasoning(self, reasoning: str) -> str:
        """Extract a category keyword from reasoning_content.

        Reasoning models like MiMo may put the final answer inside their
        reasoning chain rather than in the content field. This method
        searches the reasoning text for any valid category keyword.

        Args:
            reasoning: The reasoning_content string from the model.

        Returns:
            The extracted category string (lowercase), or empty string if not found.
        """
        reasoning_lower = reasoning.lower()

        # Look for category keywords in the reasoning, prioritize exact matches
        for category in EmailCategory:
            # Look for patterns like "category: recruitment" or "→ recruitment"
            # or the category appearing as a standalone word near the end
            if category.value in reasoning_lower:
                return category.value

        return ""

    def _build_prompt(
        self,
        subject: str,
        sender_email: str,
        sender_name: str,
        snippet: str,
        has_attachments: bool,
    ) -> str:
        """Build the classification prompt with email metadata.

        Args:
            subject: Email subject line.
            sender_email: Sender's email address.
            sender_name: Sender's display name.
            snippet: Email body preview.
            has_attachments: Whether email has attachments.

        Returns:
            Formatted prompt string.
        """
        attachment_info = "Có" if has_attachments else "Không"
        sender_display = f"{sender_name} <{sender_email}>" if sender_name else sender_email

        return (
            f"Phân loại email sau:\n\n"
            f"Từ: {sender_display}\n"
            f"Tiêu đề: {subject}\n"
            f"Nội dung: {snippet}\n"
            f"Đính kèm: {attachment_info}\n\n"
            f"Category:"
        )

    def _extract_content_from_response(self, response: Any) -> str:
        """Extract text content from an LLM response, handling varied provider shapes.

        Tries multiple paths to get the content string:
        1. Standard OpenAI: response.choices[0].message.content
        2. Reasoning models: response.choices[0].message.reasoning_content
        3. Dict-style choices (some providers): response.choices.message.content
        4. Direct content: response.content or response.text

        Args:
            response: The raw chat completion response object.

        Returns:
            Extracted text content (lowercase, stripped), or empty string.
        """

        # Log response structure for debugging unknown providers
        try:
            if hasattr(response, "model_dump"):
                logger.debug("LLM response model: %s", getattr(response, "model", "unknown"))
            else:
                logger.debug("LLM response type: %s", type(response).__name__)
        except Exception:
            # Intentionally silent: this try only wraps two logger.debug() calls
            # made purely for diagnostics. Logging the failure of a debug log call
            # would be circular, and nothing downstream depends on this block —
            # extraction continues unconditionally right below.
            pass

        content = self._try_extract_message_content(response)
        if content:
            return content.strip().lower()

        # As a last resort, log the raw response for debugging
        try:
            raw = response.model_dump() if hasattr(response, "model_dump") else str(response)[:500]
            logger.warning("Could not extract content from LLM response: %s", raw)
        except Exception:
            logger.warning("Could not extract content from LLM response (unable to serialize)")
        return ""

    def _try_extract_message_content(self, response: Any) -> str:
        """Try all known paths to extract message content from a response."""
        choices = getattr(response, "choices", None)
        logger.debug("_try_extract: top choices=%s type=%s", choices, type(choices).__name__)
        if not choices:
            raw = self._safe_model_dump(response)
            logger.debug(
                "_try_extract: model_dump returned type=%s has_data=%s",
                type(raw).__name__ if raw else None,
                "data" in raw if raw else False,
            )
            if raw:
                data = raw.get("data")
                logger.debug("_try_extract: data type=%s", type(data).__name__)
                if isinstance(data, dict):
                    choices = data.get("choices")
                    logger.debug(
                        "_try_extract: data.choices=%s",
                        type(choices).__name__ if choices is not None else None,
                    )
                elif data is not None and hasattr(data, "choices"):
                    choices = data.choices
                    logger.debug(
                        "_try_extract: data.choices (attr)=%s",
                        type(choices).__name__ if choices is not None else None,
                    )
        if not choices:
            logger.debug("_try_extract: no choices found, returning empty")
            return ""
        logger.debug(
            "_try_extract: choices is truthy, type=%s len=%s",
            type(choices).__name__,
            len(choices) if hasattr(choices, "__len__") else "?",
        )

        # Handle both list and dict-like choices
        if isinstance(choices, (list, tuple)):
            if not choices:
                return ""
            first = choices[0]
        elif isinstance(choices, dict):
            first = choices
        else:
            try:
                first = next(iter(choices))  # type: ignore[arg-type]
            except (TypeError, StopIteration):
                # Intentionally silent: every "" return from this helper —
                # including this one — flows back to
                # _extract_content_from_response(), which already logs a warning
                # with the raw response when content comes back empty (see the
                # "Could not extract content" warning there). Logging here too
                # would duplicate that trace for the same event.
                return ""

        # Normalise first item and message for consistent access.
        # Some providers return plain dicts inside response.data.choices.
        if isinstance(first, dict):
            message = first.get("message", first)
            content = (
                message.get("content")
                if isinstance(message, dict)
                else getattr(message, "content", None)
            )
        else:
            message = getattr(first, "message", None) or first
            content = getattr(message, "content", None)

        if content:
            return str(content)

        # Try reasoning content
        if isinstance(message, dict):
            reasoning = message.get("reasoning_content", "")
        else:
            reasoning = getattr(message, "reasoning_content", None) or ""
        if reasoning:
            return self._extract_category_from_reasoning(str(reasoning))

        # Try other attributes
        for attr in ("text", "response", "output", "answer"):
            val = message.get(attr) if isinstance(message, dict) else getattr(message, attr, None)
            if val:
                return str(val)

        return ""

    @staticmethod
    def _safe_model_dump(response: Any) -> dict | None:
        """Safely dump a pydantic/object response to dict."""
        try:
            if hasattr(response, "model_dump"):
                return response.model_dump()  # type: ignore[union-attr]
        except Exception:
            # Intentionally silent: try-then-fallback chain. model_dump() failing
            # just means the response isn't a pydantic v2 model shape — the
            # .dict() attempt right below is the fallback, and returning None at
            # the end of the chain if neither works is the documented contract.
            pass
        try:
            if hasattr(response, "dict"):
                return response.dict()  # type: ignore[union-attr]
        except Exception:
            # Intentionally silent: same try-then-fallback chain as above; this is
            # the last fallback before the documented `return None`.
            pass
        return None

    def _parse_category(self, raw_response: str) -> EmailCategory:
        """Parse the LLM response into an EmailCategory enum value.

        Handles common LLM response artifacts (quotes, periods, extra text).
        Defaults to uncategorized if response cannot be parsed.

        Args:
            raw_response: Raw lowercase response from the LLM.

        Returns:
            The parsed EmailCategory value.
        """
        # Clean up common LLM artifacts
        cleaned = raw_response.strip().strip('"').strip("'").strip(".").lower()

        # Remove any text after the first word/underscore-word
        # e.g., "recruitment - this is a CV" → "recruitment"
        cleaned = cleaned.split(" ")[0].split("\n")[0]

        try:
            return EmailCategory(cleaned)
        except ValueError:
            # Intentionally silent: try-then-fallback chain. Not a valid enum
            # value as-is just means we fall through to substring matching below,
            # which has its own documented uncategorized default at the end.
            pass

        # Try matching as substring
        for category in EmailCategory:
            if category.value in cleaned:
                return category

        logger.warning(
            "AI returned unparseable category, defaulting to uncategorized: %r",
            raw_response,
        )
        return EmailCategory.uncategorized

    @staticmethod
    def _extract_token_usage(response: Any) -> dict[str, int]:
        """Extract token usage from the API response.

        Args:
            response: The chat completion response object.

        Returns:
            Dictionary with prompt_tokens, completion_tokens, total_tokens.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }


class AIClassificationError(Exception):
    """Raised when AI classification fails after all retries."""

    pass
