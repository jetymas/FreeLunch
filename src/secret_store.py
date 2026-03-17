from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_VERIFIER_PLAINTEXT = "freelunch-vault-verifier"
_PBKDF2_ITERATIONS = 600_000


class SecretStoreError(Exception):
    """Base error for managed secret operations."""


class SecretStoreUnavailableError(SecretStoreError):
    """Raised when managed-secret actions require an unlocked vault."""


class SecretStoreDecryptError(SecretStoreError):
    """Raised when a stored secret cannot be decrypted."""


class SecretStorePasswordError(SecretStoreError):
    """Raised when a vault password is invalid for the configured vault."""


@dataclass(slots=True, frozen=True)
class SecretVaultConfig:
    salt_b64: str
    verifier_encrypted: str


def _derive_fernet(password: str, salt_b64: str) -> Fernet:
    password_bytes = password.encode("utf-8")
    salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(password_bytes)))


def create_vault_config(password: str) -> tuple[SecretVaultConfig, ManagedSecretStore]:
    cleaned = password.strip()
    if not cleaned:
        raise ValueError("vault password cannot be empty")
    salt_b64 = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii")
    store = ManagedSecretStore(_derive_fernet(cleaned, salt_b64))
    return (
        SecretVaultConfig(
            salt_b64=salt_b64,
            verifier_encrypted=store.encrypt(_VERIFIER_PLAINTEXT),
        ),
        store,
    )


def unlock_vault(password: str, config: SecretVaultConfig) -> ManagedSecretStore:
    cleaned = password.strip()
    if not cleaned:
        raise SecretStorePasswordError("vault password cannot be empty")
    store = ManagedSecretStore(_derive_fernet(cleaned, config.salt_b64))
    try:
        verifier = store.decrypt(config.verifier_encrypted)
    except SecretStoreDecryptError as exc:
        raise SecretStorePasswordError("invalid vault password") from exc
    if verifier != _VERIFIER_PLAINTEXT:
        raise SecretStorePasswordError("invalid vault password")
    return store


class ManagedSecretStore:
    def __init__(self, fernet: Fernet) -> None:
        self._fernet = fernet

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted_value: str, *, secret_key: str | None = None) -> str:
        try:
            plaintext = self._fernet.decrypt(encrypted_value.encode("ascii"))
        except InvalidToken as exc:
            label = f" for {secret_key}" if secret_key else ""
            raise SecretStoreDecryptError(f"unable to decrypt managed secret{label}") from exc
        return plaintext.decode("utf-8")

    def decrypt_mapping(self, encrypted: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
        decrypted: dict[str, str] = {}
        failures: list[str] = []
        for key, value in encrypted.items():
            try:
                decrypted[key] = self.decrypt(value, secret_key=key)
            except SecretStoreDecryptError:
                failures.append(key)
        return decrypted, failures
