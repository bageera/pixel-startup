"""Capability registry: every executable capability is DECLARED here.

Nothing is executable that is not in this table — unknown capability ids fail
closed before any resolution. Adapter names are an adapter whitelist: the
executor dispatches only through a registered constrained adapter.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    capability_id: str
    adapter: str  # adapter whitelist key
    scopes: tuple[str, ...]
    requires_approval: bool
    requires_credential: bool
    credential_provider: str | None  # which provider resolves the credential ref
    description: str


CAPABILITIES: dict[str, Capability] = {
    "synthetic.report": Capability(
        capability_id="synthetic.report",
        adapter="synthetic_report",
        scopes=("company", "shared"),
        requires_approval=False,
        requires_credential=False,
        credential_provider=None,
        description="Synthetic read-only briefing/report operation (qualification).",
    ),
    "synthetic.restricted.write": Capability(
        capability_id="synthetic.restricted.write",
        adapter="synthetic_restricted_write",
        scopes=("company",),
        requires_approval=True,
        requires_credential=False,
        credential_provider=None,
        description="Synthetic side-effecting write gated behind an approval (qualification).",
    ),
    "vault.credential.op": Capability(
        capability_id="vault.credential.op",
        adapter="synthetic_credential_use",
        scopes=("company",),
        requires_approval=True,
        requires_credential=True,
        credential_provider="synthetic",
        description=(
            "Resolves a scoped credential and uses it inside the executor boundary;"
            " result is redacted before return (qualification)."
        ),
    ),
}


class CapabilityDenied(Exception):
    """Fail-closed authorization denial (maps to 403; message is non-sensitive)."""
