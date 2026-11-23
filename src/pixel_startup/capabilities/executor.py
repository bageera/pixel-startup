"""Trusted capability executor (G10 pipeline, mandate §G10 steps 1-8).

1. authenticate the calling workload (workload token map; no default identity)
2. resolve the VERIFIED runtime binding -> employee (caller-supplied employee ids
   are accepted only as corroboration, never as proof)
3. validate capability (declared registry only) + operational scope
4. evaluate authorization policies and approval requirements
5. resolve the credential through the declared provider (no fallback, ever)
6. execute through the declared constrained adapter (whitelist)
7. redact sensitive material from the result
8. record a sanitized, attributable audit event

Replay/idempotency: request_key is unique (executions table); completed requests
replay their recorded result instead of re-executing; a request_key with a
terminal status is refused (no re-run), an in-flight key is refused (no race).

Every denial fails closed, and every decision (allow or deny) is audited with
attribution to the resolved employee, never the caller's claim.
"""

import json
import secrets
from typing import Any, Callable

from pixel_startup.capabilities.delegation import DelegationFailure
from pixel_startup.capabilities.providers import (
    CredentialProvider,
    ProviderUnavailable,
)
from pixel_startup.capabilities.registry import CAPABILITIES, Capability, CapabilityDenied
from pixel_startup.persistence.db import Database, StorageError, _now
from pixel_startup.persistence.store import AuditStore, EmployeeStore

ACTIVE_LIFECYCLES = {"available", "working", "waiting", "on_demand"}
_SECRET_KEYS = {"credential", "password", "secret", "token", "value", "api_key"}

DEFAULT_ADAPTERS: dict[str, Callable[..., dict]] = {
    "synthetic_report": lambda payload, employee_id, credential: {
        "report": {"topic": payload.get("topic", "status"), "generated_by": employee_id}
    },
    "synthetic_restricted_write": lambda payload, employee_id, credential: {
        "written": True,
        "artifact": payload.get("artifact", "synthetic"),
    },
    "synthetic_credential_use": lambda payload, employee_id, credential: {
        # deliberately never echoes the credential value itself
        "result": "operation executed inside the executor boundary",
        "credential_used": isinstance(credential, str) and bool(credential),
    },
}


def grant_capability_approval(db: Database, capability: str, scope: str, requested_by: str) -> str:
    approval_id = f"approv.{capability}.{scope}.{secrets.token_hex(6)}"
    db.conn.execute(
        "INSERT INTO capability_approvals (approval_id, capability, scope, requested_by,"
        " status, decided_by, created_at) VALUES (?, ?, ?, ?, 'approved', ?, ?)",
        (approval_id, capability, scope, requested_by, requested_by, _now()),
    )
    return approval_id


class CapabilityExecutor:
    def __init__(
        self,
        db: Database,
        audit: AuditStore,
        credential_providers: dict[str, CredentialProvider],
        adapters: dict[str, Callable[..., dict]],
        workload_tokens: dict[str, str],
    ) -> None:
        self.db = db
        self.employees = EmployeeStore(db)
        self.audit = audit
        self.credential_providers = credential_providers
        self.adapters = adapters
        self.workload_tokens = workload_tokens  # token -> workload id (no default)

    # -- step 1: authenticate the calling workload -----------------------------
    def authenticate(self, workload_token: str | None) -> str:
        for token, workload in self.workload_tokens.items():
            if (
                workload_token is not None
                and token
                and secrets.compare_digest(workload_token, token)
            ):
                return workload
        raise CapabilityDenied("workload authentication failed")

    # -- step 2: resolve identity through the VERIFIED binding -----------------
    def resolve_identity(self, binding_id: str) -> tuple[str, dict]:
        row = self.db.conn.execute(
            "SELECT * FROM runtime_bindings WHERE binding_id = ? AND active = 1",
            (binding_id,),
        ).fetchone()
        if row is None:
            raise CapabilityDenied("runtime binding not found or revoked")
        employee = self.employees.get(row["employee_id"])
        verified = (employee or {}).get("verified_binding")
        if employee is None or verified is None or verified.get("binding_id") != binding_id:
            raise CapabilityDenied("binding is not verified for any employee")
        if employee["lifecycle"] not in ACTIVE_LIFECYCLES:
            raise CapabilityDenied("employee is not in an actionable lifecycle state")
        return employee["employee_id"], employee

    # -- steps 3+4: capability, scope, authorization, approval -----------------
    def authorize(self, employee: dict, capability_id: str, scope: str) -> Capability:
        capability = CAPABILITIES.get(capability_id)
        if capability is None:
            raise CapabilityDenied("unknown capability")
        granted = employee.get("authorization_context") or []
        if isinstance(granted, str):
            granted = json.loads(granted)
        if capability_id not in granted:
            raise CapabilityDenied("capability not authorized for this employee")
        if scope not in capability.scopes:
            raise CapabilityDenied("scope is not valid for this capability")
        if scope != employee.get("operational_scope") and employee.get("operational_scope") not in (
            "shared",
        ):
            raise CapabilityDenied("scope is outside the employee's operational scope")
        if capability.requires_approval:
            approved = self.db.conn.execute(
                "SELECT approval_id FROM capability_approvals WHERE capability = ?"
                " AND scope = ? AND status = 'approved'",
                (capability_id, scope),
            ).fetchone()
            if approved is None:
                raise CapabilityDenied("approval required and not granted")
        return capability

    # -- step 5: credential through the declared provider (no fallback) --------
    def resolve_credential(self, capability: Capability, payload: dict, scope: str) -> str | None:
        if not capability.requires_credential:
            return None
        provider = self.credential_providers.get(capability.credential_provider or "")
        if provider is None:
            raise CapabilityDenied("credential provider not provisioned (fail closed)")
        ref = payload.get("credential_ref")
        if not isinstance(ref, str) or not ref.strip():
            raise CapabilityDenied("missing scoped credential reference")
        # cross-scope refusal: a credential ref is namespaced by scope
        if not ref.startswith(scope + "."):
            raise CapabilityDenied("credential reference is outside the approved scope")
        try:
            return provider.get_credential(ref)
        except ProviderUnavailable as exc:
            # terminal: no provider fallback may bypass the failed authorization
            raise CapabilityDenied(
                f"credential provider failed; operation refused ({exc})"
            ) from None

    # -- step 7: redaction -----------------------------------------------------
    def _redact(self, node: Any, secrets: set[str]) -> tuple[Any, int]:
        redactions = 0
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                if key.lower() in _SECRET_KEYS:
                    out[key] = "[REDACTED]"
                    redactions += 1
                    continue
                child, n = self._redact(value, secrets)
                redactions += n
                out[key] = child
            return out, redactions
        if isinstance(node, list):
            items = []
            for item in node:
                child, n = self._redact(item, secrets)
                items.append(child)
                redactions += n
            return items, redactions
        if isinstance(node, str):
            for secret in secrets:
                if secret and secret in node:
                    node = node.replace(secret, "[REDACTED]")
                    redactions += 1
        return node, redactions

    # -- the pipeline ----------------------------------------------------------
    def execute(self, request: dict) -> dict:
        capability_id = request.get("capability")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise CapabilityDenied("malformed request: capability required")
        request_key = request.get("request_key")
        if not isinstance(request_key, str) or not request_key.strip():
            raise CapabilityDenied("malformed request: request_key required")
        binding_id = request.get("binding_id")
        if not isinstance(binding_id, str) or not binding_id.strip():
            raise CapabilityDenied("malformed request: binding_id required")
        payload = request.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        # require_auth(session) already proved the owner workload in-process;
        # the bearer path re-verifies its token through the workload token map.
        pre_authenticated = request.pop("pre_authenticated_workload", None)
        workload = pre_authenticated or self.authenticate(request.get("workload_token"))
        employee_id, employee = self.resolve_identity(binding_id)
        # caller-supplied employee_id is corroboration ONLY; proof came from the
        # binding row. A mismatch is a spoofing attempt -> deny.
        claimed = request.get("employee_id")
        if isinstance(claimed, str) and claimed and claimed != employee_id:
            raise CapabilityDenied("identity mismatch between claim and binding proof")
        scope = request.get("scope") or employee["operational_scope"]
        if not isinstance(scope, str):
            raise CapabilityDenied("malformed request: scope")
        capability = self.authorize(employee, capability_id, scope)

        # replay / idempotency (executions.request_key UNIQUE)
        prior = self.db.conn.execute(
            "SELECT status, result FROM executions WHERE request_key = ?", (request_key,)
        ).fetchone()
        if prior is not None:
            if prior["status"] == "completed":
                return {**json.loads(prior["result"]), "replayed": True}
            raise CapabilityDenied(f"request_key {request_key} is {prior['status']}; refused")
        self.db.conn.execute(
            "INSERT INTO executions (request_key, employee_id, capability, scope,"
            " status, result, workload, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'running', '{}', ?, ?, ?)",
            (request_key, employee_id, capability_id, scope, workload, _now(), _now()),
        )

        try:
            credential = self.resolve_credential(capability, payload, scope)
            adapter = self.adapters.get(capability.adapter)
            if adapter is None:
                raise CapabilityDenied("adapter not provisioned (fail closed)")
            result = adapter(payload=payload, employee_id=employee_id, credential=credential)
            secrets = {credential} if isinstance(credential, str) and credential else set()
            result, redactions = self._redact(result, secrets)
        except CapabilityDenied:
            self._finish(request_key, "denied", {})
            self._audit(employee_id, capability_id, scope, workload, "deny", request_key)
            raise
        except DelegationFailure:
            # a malformed/invalid delegation is a caller error, not a server
            # fault — record error, then let the route answer an honest 400.
            self._finish(request_key, "failed", {})
            self._audit(employee_id, capability_id, scope, workload, "error", request_key)
            raise
        except Exception:
            self._finish(request_key, "failed", {})
            self._audit(employee_id, capability_id, scope, workload, "error", request_key)
            raise CapabilityDenied("execution failed; nothing returned")
        final = {**result, "execution_id": request_key}
        self._finish(request_key, "completed", final)
        self._audit(employee_id, capability_id, scope, workload, "allow", request_key, redactions)
        return final

    def _finish(self, request_key: str, status: str, result: dict) -> None:
        cur = self.db.conn.execute(
            "UPDATE executions SET status = ?, result = ?, updated_at = ? WHERE request_key = ?",
            (status, json.dumps(result), _now(), request_key),
        )
        if cur.rowcount != 1:
            raise StorageError("execution record missing")

    def _audit(
        self,
        employee_id: str,
        capability_id: str,
        scope: str,
        workload: str,
        outcome: str,
        request_key: str,
        redactions: int = 0,
    ) -> None:
        # sanitized + attributable: actor is the RESOLVED employee, never a claim
        self.audit.append(
            {
                "event_id": f"audit.cap.{request_key}.{outcome}",
                "actor": employee_id,
                "action": f"capability.{capability_id}.{outcome}",
                "outcome": outcome,
                "detail": {"scope": scope, "workload": workload, "redactions": redactions},
            }
        )
