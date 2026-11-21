"""Credential providers — resolved INSIDE the executor boundary only.

No provider here ever returns credentials to callers: the executor consumes the
value and redacts anything derived from it. Fallback between providers is
prohibited at this layer; the caller (executor) gets exactly the provider its
capability declares, and a provider error is terminal (fail closed) — a failing
provider can never silently bypass authorization.
"""

import abc


class CredentialProvider(abc.ABC):
    provider_id: str

    @abc.abstractmethod
    def get_credential(self, credential_ref: str) -> str:
        """Return the secret value for a scoped reference, or raise."""


class ProviderUnavailable(RuntimeError):
    """Raised honestly when a credential source cannot be reached/qualified."""


class SyntheticCredentialProvider(CredentialProvider):
    """In-memory synthetic credential store for G10 qualification (no secrets)."""

    provider_id = "synthetic"

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}

    def seed(self, credential_ref: str, value: str) -> None:
        self._credentials[credential_ref] = value

    def revoke(self, credential_ref: str) -> None:
        self._credentials.pop(credential_ref, None)

    def get_credential(self, credential_ref: str) -> str:
        value = self._credentials.get(credential_ref)
        if value is None:
            raise ProviderUnavailable(
                f"credential unavailable: {credential_ref}"
            )  # revoked/unknown
        return value


class BlockedVaultwardenProvider(CredentialProvider):
    """Honest Vaultwarden stub: live integration qualification is BLOCKED
    (no credentials/connectivity on host). It can never fabricate a value."""

    provider_id = "vaultwarden"

    def get_credential(self, credential_ref: str) -> str:
        raise ProviderUnavailable(
            "vaultwarden live integration BLOCKED: no credentials/connectivity "
            "(G10 ledger verdict); no fallback is permitted"
        )
