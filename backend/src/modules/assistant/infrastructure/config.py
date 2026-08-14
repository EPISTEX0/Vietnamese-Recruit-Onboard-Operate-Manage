"""Configuration for the AI Assistant module.

Loads ASSISTANT_LLM_* environment variables.
Shares only configuration with recruitment's LLMAdapter — not code.
"""

from pydantic_settings import BaseSettings


class AssistantSettings(BaseSettings):
    """Settings for the Assistant's own LLM client.

    Environment variables:
        ASSISTANT_LLM_BASE_URL: LLM API endpoint URL.
        ASSISTANT_LLM_API_KEY: API key for authentication.
        ASSISTANT_LLM_MODEL: Model name to use.
        ASSISTANT_LLM_TIMEOUT_SECONDS: Request timeout in seconds.
        ASSISTANT_LLM_MAX_RETRIES: Max retry attempts.
        ASSISTANT_LLM_MAX_HISTORY: Max messages to send in context.
    """

    model_config = {"env_prefix": "ASSISTANT_LLM_"}

    base_url: str = "http://127.0.0.1:20128/v1"
    api_key: str = "not-needed"
    model: str = "NullNyx-Combo"
    timeout_seconds: int = 30
    max_retries: int = 2
    max_history: int = 20
