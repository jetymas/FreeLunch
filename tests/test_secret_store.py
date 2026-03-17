from __future__ import annotations

import pytest

from src.secret_store import (
    SecretStoreDecryptError,
    SecretStorePasswordError,
    SecretVaultConfig,
    create_gateway_auth_config,
    create_vault_config,
    unlock_vault,
    verify_gateway_auth_token,
)


def test_vault_config_create_and_unlock_round_trip():
    config, store = create_vault_config("test-password")
    encrypted = store.encrypt("managed-secret")

    unlocked = unlock_vault("test-password", config)

    assert unlocked.decrypt(encrypted) == "managed-secret"


def test_unlock_vault_rejects_wrong_password():
    config, _store = create_vault_config("test-password")

    with pytest.raises(SecretStorePasswordError):
        unlock_vault("wrong-password", config)


def test_unlock_vault_rejects_blank_password():
    config, _store = create_vault_config("test-password")

    with pytest.raises(SecretStorePasswordError):
        unlock_vault("   ", config)


def test_unlock_vault_rejects_wrong_verifier_payload():
    config, store = create_vault_config("test-password")
    tampered = SecretVaultConfig(
        salt_b64=config.salt_b64,
        verifier_encrypted=store.encrypt("not-the-expected-verifier"),
    )

    with pytest.raises(SecretStorePasswordError):
        unlock_vault("test-password", tampered)


def test_managed_secret_decrypt_mapping_reports_failures():
    _config, store = create_vault_config("test-password")
    encrypted = store.encrypt("value")

    secrets, failures = store.decrypt_mapping(
        {
            "providers.openai.api_key": encrypted,
            "providers.groq.api_key": "not-valid-ciphertext",
        }
    )

    assert secrets == {"providers.openai.api_key": "value"}
    assert failures == ["providers.groq.api_key"]


def test_create_vault_config_rejects_empty_password():
    with pytest.raises(ValueError):
        create_vault_config("   ")


def test_decrypt_raises_specific_error_for_invalid_ciphertext():
    _config, store = create_vault_config("test-password")

    with pytest.raises(SecretStoreDecryptError):
        store.decrypt("bad-token", secret_key="providers.openai.api_key")


def test_gateway_auth_config_verifies_matching_token():
    config = create_gateway_auth_config("gateway-token")

    assert verify_gateway_auth_token("gateway-token", config) is True
    assert verify_gateway_auth_token("wrong-token", config) is False
    assert verify_gateway_auth_token("   ", config) is False


def test_create_gateway_auth_config_rejects_empty_token():
    with pytest.raises(ValueError):
        create_gateway_auth_config("   ")
