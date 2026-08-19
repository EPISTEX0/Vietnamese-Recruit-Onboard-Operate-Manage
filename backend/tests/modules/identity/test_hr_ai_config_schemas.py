"""HR's AI-config response schemas must never carry credential fields (#420).

``GET /organization/ai-config`` (System Admin) returns
``OrganizationAIConfigurationResponse``, which includes ``provider``,
``base_url``, ``model``, ``api_key_masked``, and ``credential_source``. HR's
narrow schemas exist specifically so HR can never read any of that -- see
``hr_ai_config_schemas`` module docstring. This test makes the contract
mechanical: it fails the moment any of those field names reappears on either
HR schema, regardless of who adds it back or why.
"""

from src.modules.identity.api.hr_ai_config_schemas import (
    HRAIConfigurationResponse,
    HRAIProviderStatusResponse,
)

CREDENTIAL_FIELD_NAMES = frozenset(
    {
        "provider",
        "base_url",
        "model",
        "api_key",
        "api_key_masked",
        "api_key_decrypt_failed",
        "credential_source",
        "deployment_key_available",
    }
)


def test_hr_ai_configuration_response_carries_no_credential_field() -> None:
    leaked = CREDENTIAL_FIELD_NAMES & set(HRAIConfigurationResponse.model_fields)
    assert not leaked, f"HRAIConfigurationResponse leaks credential fields: {leaked}"


def test_hr_ai_provider_status_response_carries_no_credential_field() -> None:
    leaked = CREDENTIAL_FIELD_NAMES & set(HRAIProviderStatusResponse.model_fields)
    assert not leaked, f"HRAIProviderStatusResponse leaks credential fields: {leaked}"


def test_hr_ai_provider_status_response_is_a_bare_boolean() -> None:
    """The provider-status route is deliberately just a boolean, nothing else."""
    assert set(HRAIProviderStatusResponse.model_fields) == {"connected"}
