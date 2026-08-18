"""Unit tests for the LLM Adapter service."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.recruitment.domain.enums import EmailIntent
from src.modules.recruitment.domain.exceptions import LLMParseError
from src.modules.recruitment.infrastructure.config import RecruitmentSettings
from src.modules.recruitment.infrastructure.llm_adapter import (
    IntentResult,
    LLMAdapter,
    ParsedCVResult,
)


def _make_classification_json(intent: str, confidence: float = 0.95) -> str:
    """Create a valid classification JSON response string."""
    return json.dumps(
        {
            "version": "1.0",
            "intent": intent,
            "confidence": confidence,
            "evidence": [f"matched_{intent}_pattern"],
            "source_hints": {"sender_role": "candidate", "has_cv_attachment": "false"},
        }
    )


def _make_completion_response(content: str, prompt_tokens: int = 50, completion_tokens: int = 10):
    """Create a mock chat completion response."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage = MagicMock()
    response.usage.prompt_tokens = prompt_tokens
    response.usage.completion_tokens = completion_tokens
    response.usage.total_tokens = prompt_tokens + completion_tokens
    return response


@pytest.fixture
def settings() -> RecruitmentSettings:
    """Create test settings."""
    return RecruitmentSettings(
        llm_base_url="http://localhost:20128/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_intent_timeout_seconds=15,
        llm_parse_timeout_seconds=30,
        llm_max_retries=3,
    )


@pytest.fixture
def adapter(settings: RecruitmentSettings) -> LLMAdapter:
    """Create an LLMAdapter instance for testing."""
    return LLMAdapter(settings)


class TestClassifyIntent:
    """Tests for the classify_intent method."""

    @pytest.mark.asyncio
    async def test_classifies_cv_intent_legacy(self, adapter: LLMAdapter):
        """Should correctly classify a CV email (legacy intent)."""
        mock_response = _make_completion_response(_make_classification_json("job_application"))

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await adapter.classify_intent(
                subject="Ứng tuyển vị trí Backend Developer",
                sender="candidate@gmail.com",
                snippet="Kính gửi phòng nhân sự, tôi xin gửi CV...",
                attachment_filenames=["CV_NguyenVanA.pdf"],
            )

        assert isinstance(result, IntentResult)
        assert result.intent == EmailIntent.JOB_APPLICATION
        assert result.classification is not None
        assert result.classification.version == "1.0"
        assert result.classification.confidence == 0.95
        assert result.token_usage["prompt_tokens"] == 50
        assert result.token_usage["completion_tokens"] == 10
        assert result.token_usage["total_tokens"] == 60

    @pytest.mark.asyncio
    async def test_classifies_job_application_intent(self, adapter: LLMAdapter):
        """Should correctly classify a job_application email."""
        mock_response = _make_completion_response(_make_classification_json("job_application"))

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await adapter.classify_intent(
                subject="Application for Senior Developer",
                sender="applicant@gmail.com",
                snippet="I would like to apply for the position...",
                attachment_filenames=[],
            )

        assert result.intent == EmailIntent.JOB_APPLICATION
        assert result.classification is not None

    @pytest.mark.asyncio
    async def test_classifies_partner_intent(self, adapter: LLMAdapter):
        """Should correctly classify a partner email."""
        mock_response = _make_completion_response(_make_classification_json("partner"))

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await adapter.classify_intent(
                subject="Hợp tác kinh doanh",
                sender="partner@company.com",
                snippet="Chúng tôi muốn đề xuất hợp tác...",
                attachment_filenames=[],
            )

        assert result.intent == EmailIntent.PARTNER

    @pytest.mark.asyncio
    async def test_classifies_event_intent(self, adapter: LLMAdapter):
        """Should correctly classify an event email."""
        mock_response = _make_completion_response(_make_classification_json("event"))

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await adapter.classify_intent(
                subject="Mời tham dự hội thảo",
                sender="events@techconf.vn",
                snippet="Kính mời quý công ty tham dự...",
                attachment_filenames=["invitation.pdf"],
            )

        assert result.intent == EmailIntent.EVENT

    @pytest.mark.asyncio
    async def test_malformed_response_raises_after_retries(self, adapter: LLMAdapter):
        """Malformed response should raise LLMParseError after retries, not default to OTHER."""
        mock_response = _make_completion_response("I think this is a job application")

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(LLMParseError):
                    await adapter.classify_intent(
                        subject="Test",
                        sender="test@test.com",
                        snippet="Test snippet",
                        attachment_filenames=[],
                    )

        # Verify all retries were exhausted
        assert mock_create.call_count == 3

    @pytest.mark.asyncio
    async def test_missing_required_field_raises_after_retries(self, adapter: LLMAdapter):
        """Missing required fields in JSON should raise LLMParseError after retries."""
        # Missing 'confidence' field
        incomplete_json = json.dumps(
            {
                "version": "1.0",
                "intent": "job_application",
                "evidence": ["matched_cv_pattern"],
            }
        )
        mock_response = _make_completion_response(incomplete_json)

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(LLMParseError):
                    await adapter.classify_intent(
                        subject="Test",
                        sender="test@test.com",
                        snippet="Test snippet",
                        attachment_filenames=[],
                    )

    @pytest.mark.asyncio
    async def test_unsupported_intent_raises_after_retries(self, adapter: LLMAdapter):
        """Unsupported intent in JSON should raise LLMParseError after retries."""
        bad_intent = json.dumps(
            {
                "version": "1.0",
                "intent": "spam",
                "confidence": 0.9,
                "evidence": ["matched_spam_pattern"],
            }
        )
        mock_response = _make_completion_response(bad_intent)

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(LLMParseError):
                    await adapter.classify_intent(
                        subject="Test",
                        sender="test@test.com",
                        snippet="Test snippet",
                        attachment_filenames=[],
                    )

    @pytest.mark.asyncio
    async def test_raises_after_max_retries_on_timeout(self, adapter: LLMAdapter):
        """Should raise LLMParseError after all retries are exhausted on timeout."""
        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = TimeoutError()

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(LLMParseError):
                    await adapter.classify_intent(
                        subject="Test",
                        sender="test@test.com",
                        snippet="Test",
                        attachment_filenames=[],
                    )

        assert mock_create.call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_api_error(self, adapter: LLMAdapter):
        """Should retry on API errors and succeed if a retry works."""
        from openai import APIConnectionError

        mock_response = _make_completion_response(_make_classification_json("job_application"))

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = [
                APIConnectionError(request=MagicMock()),
                mock_response,
            ]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await adapter.classify_intent(
                    subject="Apply",
                    sender="candidate@email.com",
                    snippet="CV attached",
                    attachment_filenames=["cv.pdf"],
                )

        assert result.intent == EmailIntent.JOB_APPLICATION
        assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_prompt_includes_all_metadata(self, adapter: LLMAdapter):
        """Should include subject, sender, snippet, and attachments in prompt."""
        mock_response = _make_completion_response(_make_classification_json("job_application"))

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            await adapter.classify_intent(
                subject="Ứng tuyển Backend",
                sender="nguyen@gmail.com",
                snippet="Xin gửi CV ứng tuyển",
                attachment_filenames=["CV.pdf", "Cover_Letter.docx"],
            )

        # Check the user message content
        call_kwargs = mock_create.call_args[1]
        messages = call_kwargs["messages"]
        user_message = messages[1]["content"]

        assert "Ứng tuyển Backend" in user_message
        assert "nguyen@gmail.com" in user_message
        assert "Xin gửi CV ứng tuyển" in user_message
        assert "CV.pdf" in user_message
        assert "Cover_Letter.docx" in user_message
        assert "2 files" in user_message

    @pytest.mark.asyncio
    async def test_prompt_shows_no_attachments(self, adapter: LLMAdapter):
        """Should indicate no attachments when list is empty."""
        mock_response = _make_completion_response(_make_classification_json("other"))

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            await adapter.classify_intent(
                subject="Hello",
                sender="someone@email.com",
                snippet="Just a message",
                attachment_filenames=[],
            )

        call_kwargs = mock_create.call_args[1]
        messages = call_kwargs["messages"]
        user_message = messages[1]["content"]
        assert "none" in user_message.lower()

    @pytest.mark.asyncio
    async def test_prompt_says_untrusted_data(self, adapter: LLMAdapter):
        """Prompt should label email as untrusted data."""
        mock_response = _make_completion_response(_make_classification_json("other"))

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            await adapter.classify_intent(
                subject="Test",
                sender="test@test.com",
                snippet="Test",
                attachment_filenames=[],
            )

        call_kwargs = mock_create.call_args[1]
        messages = call_kwargs["messages"]
        system_message = messages[0]["content"]
        assert "untrusted" in system_message.lower()
        assert "no tools" in system_message.lower()
        assert "no write" in system_message.lower()

    @pytest.mark.asyncio
    async def test_prompt_requests_json(self, adapter: LLMAdapter):
        """Prompt should request JSON output, not single word."""
        mock_response = _make_completion_response(_make_classification_json("other"))

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            await adapter.classify_intent(
                subject="Test",
                sender="test@test.com",
                snippet="Test",
                attachment_filenames=[],
            )

        call_kwargs = mock_create.call_args[1]
        messages = call_kwargs["messages"]
        user_message = messages[1]["content"]
        assert "JSON" in user_message

    @pytest.mark.asyncio
    async def test_handles_markdown_code_block(self, adapter: LLMAdapter):
        """Should handle JSON wrapped in markdown code blocks."""
        cv_json = json.dumps(
            {
                "version": "1.0",
                "intent": "job_application",
                "confidence": 0.9,
                "evidence": ["matched_cv_pattern"],
                "source_hints": {"has_cv_attachment": "true"},
            }
        )
        wrapped = f"```json\n{cv_json}\n```"
        mock_response = _make_completion_response(wrapped)

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await adapter.classify_intent(
                subject="Test",
                sender="test@test.com",
                snippet="Test",
                attachment_filenames=[],
            )

        assert result.intent == EmailIntent.JOB_APPLICATION


class TestParseCV:
    """Tests for the parse_cv method."""

    @pytest.mark.asyncio
    async def test_parses_valid_cv_json(self, adapter: LLMAdapter):
        """Should parse a valid JSON response into ParsedCV."""
        cv_json = json.dumps(
            {
                "name": "Nguyễn Văn A",
                "email": "nguyenvana@gmail.com",
                "phone": "0901234567",
                "skills": ["Python", "FastAPI", "PostgreSQL"],
                "experience": [
                    {
                        "company": "Tech Corp",
                        "title": "Backend Developer",
                        "duration": "2020-2023",
                        "description": "Developed REST APIs",
                    }
                ],
                "education": [
                    {
                        "institution": "HCMUT",
                        "degree": "Bachelor",
                        "field": "Computer Science",
                        "year": "2020",
                    }
                ],
                "summary": "Experienced backend developer",
            }
        )
        mock_response = _make_completion_response(cv_json, prompt_tokens=200, completion_tokens=150)

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await adapter.parse_cv("Some OCR text from a CV...")

        assert isinstance(result, ParsedCVResult)
        assert result.parsed_cv.name == "Nguyễn Văn A"
        assert result.parsed_cv.email == "nguyenvana@gmail.com"
        assert result.parsed_cv.phone == "0901234567"
        assert "Python" in result.parsed_cv.skills
        assert len(result.parsed_cv.experience) == 1
        assert result.parsed_cv.experience[0].company == "Tech Corp"
        assert len(result.parsed_cv.education) == 1
        assert result.parsed_cv.education[0].institution == "HCMUT"
        assert result.token_usage["prompt_tokens"] == 200
        assert result.token_usage["total_tokens"] == 350

    @pytest.mark.asyncio
    async def test_handles_markdown_code_block_response(self, adapter: LLMAdapter):
        """Should handle JSON wrapped in markdown code blocks."""
        cv_json = json.dumps(
            {
                "name": "Trần Thị B",
                "email": "tranthib@email.com",
                "phone": "",
                "skills": ["Java"],
                "experience": [],
                "education": [],
                "summary": "",
            }
        )
        wrapped = f"```json\n{cv_json}\n```"
        mock_response = _make_completion_response(wrapped)

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await adapter.parse_cv("OCR text...")

        assert result.parsed_cv.name == "Trần Thị B"
        assert result.parsed_cv.email == "tranthib@email.com"

    @pytest.mark.asyncio
    async def test_retries_with_simplified_prompt_on_invalid_json(self, adapter: LLMAdapter):
        """Should retry with simplified prompt when initial response is invalid JSON."""
        invalid_response = _make_completion_response("Here is the parsed CV: {invalid json}")
        valid_json = json.dumps(
            {
                "name": "Test User",
                "email": "test@email.com",
                "phone": "",
                "skills": [],
                "experience": [],
                "education": [],
                "summary": "",
            }
        )
        valid_response = _make_completion_response(valid_json)

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            # First call returns invalid JSON, second (simplified) returns valid
            mock_create.side_effect = [invalid_response, valid_response]

            result = await adapter.parse_cv("Some CV text")

        assert result.parsed_cv.name == "Test User"
        assert result.parsed_cv.email == "test@email.com"
        # Should have been called twice: initial + simplified retry
        assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self, adapter: LLMAdapter):
        """Should raise LLMParseError when all retries fail."""
        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = TimeoutError()

            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(LLMParseError):
                    await adapter.parse_cv("Some CV text")

    @pytest.mark.asyncio
    async def test_handles_minimal_valid_cv(self, adapter: LLMAdapter):
        """Should parse a CV with only required fields."""
        cv_json = json.dumps(
            {
                "name": "Minimal User",
                "email": "minimal@email.com",
            }
        )
        mock_response = _make_completion_response(cv_json)

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            result = await adapter.parse_cv("Short CV text")

        assert result.parsed_cv.name == "Minimal User"
        assert result.parsed_cv.email == "minimal@email.com"
        assert result.parsed_cv.phone == ""
        assert result.parsed_cv.skills == []

    @pytest.mark.asyncio
    async def test_handles_no_usage_in_response(self, adapter: LLMAdapter):
        """Should handle response without usage data gracefully."""
        cv_json = json.dumps(
            {
                "name": "Test",
                "email": "test@test.com",
            }
        )
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = cv_json
        response.usage = None

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = response

            result = await adapter.parse_cv("CV text")

        assert result.token_usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @pytest.mark.asyncio
    async def test_timeout_uses_parse_timeout_setting(self, adapter: LLMAdapter):
        """Should use the configured parse timeout (30s)."""
        cv_json = json.dumps({"name": "Test", "email": "t@t.com"})
        mock_response = _make_completion_response(cv_json)

        with patch.object(
            adapter._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
                mock_wait.return_value = mock_response

                await adapter.parse_cv("CV text")

                # Verify timeout parameter
                _, kwargs = mock_wait.call_args
                assert kwargs["timeout"] == 30


class TestParseClassificationJson:
    """Tests for the _parse_classification_json helper."""

    def test_parses_all_valid_intents(self, adapter: LLMAdapter):
        """Should parse all valid EmailIntent values from classification JSON."""
        for intent in EmailIntent:
            content = json.dumps(
                {
                    "version": "1.0",
                    "intent": intent.value,
                    "confidence": 0.9,
                    "evidence": [f"matched_{intent.value}_pattern"],
                    "source_hints": {},
                }
            )
            result = adapter._parse_classification_json(content)
            assert result.intent == intent
            assert result.version == "1.0"
            assert 0.0 <= result.confidence <= 1.0

    def test_parses_job_application(self, adapter: LLMAdapter):
        """Should parse job_application intent."""
        content = json.dumps(
            {
                "version": "1.0",
                "intent": "job_application",
                "confidence": 0.85,
                "evidence": ["subject:application", "body:apply_for_position"],
                "source_hints": {"sender_role": "candidate", "has_cv_attachment": "false"},
            }
        )
        result = adapter._parse_classification_json(content)
        assert result.intent == EmailIntent.JOB_APPLICATION
        assert result.version == "1.0"
        assert result.confidence == 0.85
        assert len(result.evidence) == 2
        assert ("sender_role", "candidate") in result.source_hints

    def test_rejects_legacy_cv_intent(self, adapter: LLMAdapter):
        """Legacy ``cv`` is audit data, not an active routing intent."""
        content = json.dumps(
            {
                "version": "1.0",
                "intent": "cv",
                "confidence": 0.95,
                "evidence": ["attachment:cv.pdf"],
                "source_hints": {"has_cv_attachment": "true"},
            }
        )
        with pytest.raises(LLMParseError, match="unsupported intent"):
            adapter._parse_classification_json(content)

    @pytest.mark.parametrize("confidence", [1.5, -0.5, True])
    def test_rejects_invalid_confidence(self, adapter: LLMAdapter, confidence: float | bool):
        """Confidence outside the contract range should fail validation."""
        content = json.dumps(
            {
                "version": "1.0",
                "intent": "job_application",
                "confidence": confidence,
                "evidence": ["attachment:cv.pdf"],
                "source_hints": {},
            }
        )
        with pytest.raises(LLMParseError, match="confidence"):
            adapter._parse_classification_json(content)

    def test_handles_markdown_code_blocks(self, adapter: LLMAdapter):
        """Should strip markdown code block wrappers."""
        inner = json.dumps(
            {
                "version": "1.0",
                "intent": "job_application",
                "confidence": 0.9,
                "evidence": ["test"],
                "source_hints": {},
            }
        )
        content = f"```json\n{inner}\n```"
        result = adapter._parse_classification_json(content)
        assert result.intent == EmailIntent.JOB_APPLICATION

    def test_handles_plain_code_blocks(self, adapter: LLMAdapter):
        """Should strip plain code block wrappers (no language tag)."""
        inner = json.dumps(
            {
                "version": "1.0",
                "intent": "event",
                "confidence": 0.8,
                "evidence": ["test"],
                "source_hints": {},
            }
        )
        content = f"```\n{inner}\n```"
        result = adapter._parse_classification_json(content)
        assert result.intent == EmailIntent.EVENT

    def test_raises_on_malformed_json(self, adapter: LLMAdapter):
        """Should raise LLMParseError for malformed JSON."""
        with pytest.raises(LLMParseError, match="not valid JSON"):
            adapter._parse_classification_json("not json at all")

    def test_raises_on_empty_string(self, adapter: LLMAdapter):
        """Should raise LLMParseError for empty string."""
        with pytest.raises(LLMParseError, match="not valid JSON"):
            adapter._parse_classification_json("")

    def test_raises_on_non_dict_json(self, adapter: LLMAdapter):
        """Should raise LLMParseError for JSON that is not a dict."""
        with pytest.raises(LLMParseError, match="not a JSON object"):
            adapter._parse_classification_json('"just a string"')

        with pytest.raises(LLMParseError, match="not a JSON object"):
            adapter._parse_classification_json("[1, 2, 3]")

    def test_raises_on_missing_version(self, adapter: LLMAdapter):
        """Should raise LLMParseError when version field is missing."""
        content = json.dumps(
            {
                "intent": "job_application",
                "confidence": 0.9,
                "evidence": [],
            }
        )
        with pytest.raises(LLMParseError, match="version"):
            adapter._parse_classification_json(content)

    def test_raises_on_missing_intent(self, adapter: LLMAdapter):
        """Should raise LLMParseError when intent field is missing."""
        content = json.dumps(
            {
                "version": "1.0",
                "confidence": 0.9,
                "evidence": [],
            }
        )
        with pytest.raises(LLMParseError, match="intent"):
            adapter._parse_classification_json(content)

    def test_raises_on_missing_confidence(self, adapter: LLMAdapter):
        """Should raise LLMParseError when confidence field is missing."""
        content = json.dumps(
            {
                "version": "1.0",
                "intent": "job_application",
                "evidence": [],
            }
        )
        with pytest.raises(LLMParseError, match="confidence"):
            adapter._parse_classification_json(content)

    def test_raises_on_missing_evidence(self, adapter: LLMAdapter):
        """Should raise LLMParseError when evidence field is missing."""
        content = json.dumps(
            {
                "version": "1.0",
                "intent": "job_application",
                "confidence": 0.9,
            }
        )
        with pytest.raises(LLMParseError, match="evidence"):
            adapter._parse_classification_json(content)

    def test_raises_on_unsupported_intent(self, adapter: LLMAdapter):
        """Should raise LLMParseError for unsupported intent value."""
        content = json.dumps(
            {
                "version": "1.0",
                "intent": "spam",
                "confidence": 0.9,
                "evidence": [],
            }
        )
        with pytest.raises(LLMParseError, match="unsupported intent"):
            adapter._parse_classification_json(content)

    def test_raises_on_missing_source_hints(self, adapter: LLMAdapter):
        """Source hints are a required part of the versioned contract."""
        content = json.dumps(
            {
                "version": "1.0",
                "intent": "other",
                "confidence": 0.5,
                "evidence": ["no_recruitment_signal"],
            }
        )
        with pytest.raises(LLMParseError, match="source_hints"):
            adapter._parse_classification_json(content)


class TestParseCVJson:
    """Tests for the _parse_cv_json helper."""

    def test_parses_valid_json(self, adapter: LLMAdapter):
        """Should parse valid JSON into ParsedCV."""
        content = json.dumps({"name": "Test", "email": "test@test.com"})
        result = adapter._parse_cv_json(content)
        assert result is not None
        assert result.name == "Test"

    def test_returns_none_for_invalid_json(self, adapter: LLMAdapter):
        """Should return None for invalid JSON."""
        assert adapter._parse_cv_json("not json at all") is None
        assert adapter._parse_cv_json("{invalid}") is None

    def test_returns_none_for_non_dict_json(self, adapter: LLMAdapter):
        """Should return None for JSON that is not a dict."""
        assert adapter._parse_cv_json("[1, 2, 3]") is None
        assert adapter._parse_cv_json('"just a string"') is None

    def test_strips_markdown_code_blocks(self, adapter: LLMAdapter):
        """Should strip markdown code block wrappers."""
        content = '```json\n{"name": "Test", "email": "t@t.com"}\n```'
        result = adapter._parse_cv_json(content)
        assert result is not None
        assert result.name == "Test"

    def test_strips_plain_code_blocks(self, adapter: LLMAdapter):
        """Should strip plain code block wrappers (no language tag)."""
        content = '```\n{"name": "Test", "email": "t@t.com"}\n```'
        result = adapter._parse_cv_json(content)
        assert result is not None
        assert result.name == "Test"

    def test_logs_warning_for_invalid_json(
        self, adapter: LLMAdapter, caplog: pytest.LogCaptureFixture
    ):
        """Malformed JSON should log a warning naming the JSON failure."""
        with caplog.at_level(
            logging.WARNING, logger="src.modules.recruitment.infrastructure.llm_adapter"
        ):
            result = adapter._parse_cv_json("{invalid}")
        assert result is None
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.WARNING
        assert "not valid JSON" in record.getMessage()

    def test_logs_exception_for_schema_validation_failure(
        self, adapter: LLMAdapter, caplog: pytest.LogCaptureFixture
    ):
        """Well-formed JSON that fails ParsedCV validation logs at exception level.

        Distinct from the JSON-decode failure above: this is the strongest
        available signal of LLM prompt/schema drift, so it gets exc_info too.
        """
        # "name" is a required field on ParsedCV; omitting it fails model_validate.
        content = json.dumps({"email": "test@test.com"})
        with caplog.at_level(
            logging.WARNING, logger="src.modules.recruitment.infrastructure.llm_adapter"
        ):
            result = adapter._parse_cv_json(content)
        assert result is None
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.ERROR
        assert "schema validation" in record.getMessage()
        assert record.exc_info is not None


class TestTokenUsageExtraction:
    """Tests for the _extract_token_usage helper."""

    def test_extracts_usage_from_response(self, adapter: LLMAdapter):
        """Should extract token usage from response."""
        response = MagicMock()
        response.usage = MagicMock()
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 50
        response.usage.total_tokens = 150

        result = LLMAdapter._extract_token_usage(response)
        assert result == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}

    def test_handles_none_usage(self, adapter: LLMAdapter):
        """Should return zeros when usage is None."""
        response = MagicMock()
        response.usage = None

        result = LLMAdapter._extract_token_usage(response)
        assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def test_handles_missing_usage_attribute(self, adapter: LLMAdapter):
        """Should handle response without usage attribute."""
        response = MagicMock(spec=[])  # No attributes

        result = LLMAdapter._extract_token_usage(response)
        assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
