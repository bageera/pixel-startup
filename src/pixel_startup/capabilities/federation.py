"""G23 federation milestone: capability manifests + harness event ingestion.

Architectural rule: ONE capability, ONE execution authority, many organizational
consumers. Pixel Startup is the organization (roster, tasks, approvals,
observability); OpenClaw and Hermes own execution (email, tools, comms). A
capability like `email.send` resolves to its designated owner instead of
triggering a second implementation.

Ingestion rides the EXISTING delegation adapter — the deterministic task id
(correlation = event id) gives idempotency: retried events replay, never
re-dispatch. The manifest's approval flag drives the G23-3a CHO plan gate.
Events carry sanitized summaries only; the full message stays in the owning
harness.
"""

from typing import Any

from pixel_startup.capabilities.delegation import DelegationFailure
from pixel_startup.persistence.store import EmployeeStore

MANIFEST: dict[str, dict[str, Any]] = {
    "email.send": {
        "owner": "openclaw",
        "scopes": ["mail.send"],
        "approval": "cho",
        "reporting": "task.result via openclaw_gateway",
        "note": "Harness owns retrieval/parse/draft/delivery; Pixel gets events+approvals.",
    },
    "email.classify": {
        "owner": "openclaw",
        "scopes": ["mail.read"],
        "approval": None,
        "reporting": None,
        "note": "Runs in the harness; Pixel receives only the proposed organizational action.",
    },
    "messaging.post": {
        "owner": "openclaw",
        "scopes": ["comms.post"],
        "approval": None,
        "reporting": "delivery status via gateway audit",
        "note": "Harnesses use established channels; Pixel coordinates and observes.",
    },
    "memory.share": {
        "owner": "pixel",
        "scopes": ["chroma.write"],
        "approval": None,
        "reporting": "chromadb heartbeat",
        "note": "Explicit shared org knowledge only; never auto-duplicate private agent memory.",
    },
}


class FederatedCapabilityUnknown(DelegationFailure):
    """Not in the manifest: Pixel has no authority to invent one at request time."""


def resolve(capability_id: str) -> dict[str, Any]:
    row = MANIFEST.get(capability_id)
    if row is None:
        raise FederatedCapabilityUnknown(f"capability not in manifest: {capability_id}")
    return row


def make_federation_ingest(db, registry, audit):
    """Ingest a sanitized harness event as an organizational task."""
    from pixel_startup.capabilities.delegation import (
        ACTIVE_LIFECYCLE,
        make_delegation_adapter,
    )

    adapter = make_delegation_adapter(db, registry, audit)

    def ingest(event: dict, requested_by: str = "chief.human") -> dict:
        if not isinstance(event, dict):
            raise DelegationFailure("event must be an object")
        event_id = str(event.get("event_id") or "").strip()
        capability_id = str(event.get("capability") or "").strip()
        if not event_id or not capability_id:
            raise DelegationFailure("event requires event_id and capability")
        cap = resolve(capability_id)
        if cap["owner"] == "pixel":
            return {
                "event_id": event_id,
                "capability": capability_id,
                "owner": "pixel",
                "dispatched": None,
                "note": "pixel-owned capability executed internally",
            }
        target = next(
            (
                e["employee_id"]
                for e in EmployeeStore(db).all()
                if e.get("harness") == cap["owner"] and e.get("lifecycle") in ACTIVE_LIFECYCLE
            ),
            None,
        )
        if target is None:
            raise DelegationFailure(
                f"no active employee bound to harness '{cap['owner']}' — "
                "an onboarding gap, not an integration bug to code around"
            )
        return adapter(
            {
                "target_employee_id": target,
                "objective": str(event.get("summary") or capability_id),
                "scope": "company",
                "correlation_id": event_id,  # idempotency: one event id, one task
                "plan_required": cap.get("approval") == "cho",
                "capability": capability_id,
            },
            requested_by,
            None,
        )

    return ingest
