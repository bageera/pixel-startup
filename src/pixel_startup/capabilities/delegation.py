"""G14: cross-harness delegation adapter for the trusted capability executor.

Runs as the `delegate_task` adapter of capability `delegate.hermes.task`. The
G10 executor handles authentication (workload), identity (verified binding),
authorization (grants + scope), replay protection and audit; this module does
the harness dispatch on top of the G6 task lifecycle:

    created -> authorized -> dispatched -> running -> completed/failed

Honesty rules: persist BEFORE dispatch, never invent a submission endpoint,
never re-run a side-effecting task whose outcome is uncertain, cancel loudly
when the target harness has no supported submission path (B1/B5).
"""

import hashlib
import json

from pixel_startup.persistence.db import Database
from pixel_startup.persistence.store import (
    AuditStore,
    EmployeeStore,
    TaskStore,
    _now,
)

HARNESS_PROVIDER = {"openclaw": "openclaw_gateway", "hermes": "hermes_home"}
ACTIVE_LIFECYCLE = ("available", "working", "waiting", "on_demand")


class DelegationFailure(Exception):
    """Raised to the executor; converted to a denial by the capability path."""


def digest8(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _make_task_id(correlation_id: str, target: str, objective: str) -> str:
    digest = hashlib.sha256(f"{correlation_id}|{target}|{objective}".encode()).hexdigest()[:12]
    return f"task.{digest}"


def make_delegation_adapter(db: Database, registry, audit: AuditStore):
    """Factory: executor adapters are plain callables; services are closed over."""

    def _audit(task_id: str, detail: str) -> None:
        audit.append(
            {
                "event_id": f"deleg.{task_id}.{digest8(task_id + detail)}",
                "occurred_at": _now(),
                "actor": "system",
                "action": "delegation",
                "outcome": "info" if '"dispatched": false' not in detail.lower() else "deny",
                "detail": detail,
            }
        )

    def adapter(payload: dict, employee_id: str, credential=None) -> dict:
        _ = credential
        target_id = payload.get("target_employee_id") or ""
        objective = payload.get("objective") or ""
        scope = payload.get("scope") or "company"
        correlation_id = payload.get("correlation_id") or payload.get("request_key", "")
        if not target_id or not objective:
            raise DelegationFailure("malformed delegation: target and objective required")

        store = EmployeeStore(db)
        target = store.get(target_id)
        if target is None or target["lifecycle"] not in ACTIVE_LIFECYCLE:
            raise DelegationFailure("target employee not actionable")
        harness = target["harness"] or payload.get("harness") or "none"

        task_id = _make_task_id(correlation_id, target_id, objective)
        tasks = TaskStore(db)
        existing = tasks.get(task_id)  # duplicate suppression by deterministic id
        if existing is not None and existing["status"] not in ("cancelled", "failed"):
            return {**existing, "replayed": True, "dispatched": None}

        # 1. persist BEFORE any dispatch
        _ = tasks.create(
            {
                "task_id": task_id,
                "kind": "delegation",
                "objective": objective,
                "operational_scope": scope,
                "requested_by": employee_id,
                "assigned_employee_id": target_id,
                "status": "created",
                "correlation_id": correlation_id,
                "payload": {"target": target_id},
            }
        )

        # 2. resolve the target harness handler
        provider = HARNESS_PROVIDER.get(harness)
        reg = registry.get(provider) if provider else None
        if reg is None:
            tasks.transition(task_id, "cancelled")
            result = {
                "task_id": task_id,
                "dispatched": False,
                "reason": f"no harness provider for '{harness}'",
                "blocker": "B1",
            }
            _audit(task_id, json.dumps(result))
            return result

        # 3. dispatch through the documented handler contract only
        resp = (
            reg.handler.invoke(
                "task.submit",
                {
                    "task_id": task_id,
                    "objective": objective,
                    "scope": scope,
                    "correlation_id": correlation_id,
                    "requested_by": employee_id,
                },
            )
            if reg.handler is not None
            else {"supported": False, "reason": "no handler"}
        )
        if not resp.get("supported"):
            tasks.transition(task_id, "cancelled")
            result = {
                "task_id": task_id,
                "dispatched": False,
                "reason": resp.get("reason", "harness does not support task.submit"),
                "blocker": "B5" if harness == "hermes" else "B1",
            }
            _audit(task_id, json.dumps(result))
            return result

        # 4. acknowledged: lifecycle moves on; retrieval waits on task.result
        tasks.transition(task_id, "authorized")
        tasks.transition(task_id, "dispatched")
        remote_raw = resp.get("result") or {}
        remote_id = remote_raw.get("taskId") if isinstance(remote_raw, dict) else None
        tasks.transition(task_id, "running")
        _audit(
            task_id, json.dumps({"task_id": task_id, "dispatched": True, "remote_id": remote_id})
        )
        return {"task_id": task_id, "dispatched": True, "remote_id": remote_id, "blocker": None}

    return adapter


def make_result_retriever(db: Database, registry, audit: AuditStore):
    """Fetch results for a dispatched task; uncertain outcomes stay uncertain."""

    def retriever(task_id: str) -> dict:
        tasks = TaskStore(db)
        row = tasks.get(task_id)
        if row is None:
            raise DelegationFailure(f"unknown task: {task_id}")
        if row["status"] == "completed":
            saved = db.conn.execute(
                "SELECT result FROM task_results WHERE task_id = ?", (task_id,)
            ).fetchone()
            return {
                **(dict(saved) if saved else {"result": "{}"}),
                "task_id": task_id,
                "status": "completed",
            }
        if row["status"] != "running":
            return {"task_id": task_id, "status": row["status"], "result": None}
        provider = HARNESS_PROVIDER.get(_harness_of(db, row))
        reg = registry.get(provider) if provider else None
        if reg is None or reg.handler is None:
            return {
                "task_id": task_id,
                "status": "running",
                "uncertain": True,
                "reason": "harness unavailable",
            }
        resp = reg.handler.invoke("task.result", {"task_id": task_id})
        if not resp.get("supported"):
            return {
                "task_id": task_id,
                "status": "running",
                "uncertain": True,
                "reason": resp.get("reason"),
            }
        remote = resp.get("result") or {}
        state = remote.get("state") if isinstance(remote, dict) else None
        if state == "completed":
            tasks.transition(task_id, "completed")
            db.conn.execute(
                "INSERT INTO task_results (task_id, status, result, updated_at)"
                " VALUES (?, 'completed', ?, ?)"
                " ON CONFLICT(task_id) DO UPDATE SET status='completed', result=excluded.result,"
                " updated_at=excluded.updated_at",
                (task_id, json.dumps(remote.get("output")), _now()),
            )
            return {"task_id": task_id, "status": "completed", "result": remote.get("output")}
        if state == "failed":
            tasks.transition(task_id, "failed")
            return {"task_id": task_id, "status": "failed", "result": remote.get("output")}
        return {"task_id": task_id, "status": row["status"], "uncertain": True}

    return retriever


def _harness_of(db: Database, row: dict) -> str:
    employee = EmployeeStore(db).get(row["assigned_employee_id"])
    return (employee or {}).get("harness") or "none"


def reconcile_stale(db: Database) -> dict:
    """Restart/timeout reconciliation: never re-run side-effecting work."""
    tasks = TaskStore(db)
    out = {"reconciled": []}
    for row in db.conn.execute(
        "SELECT task_id, status FROM tasks WHERE status IN ('dispatched', 'running')"
    ).fetchall():
        if row["status"] == "dispatched":  # no execution ack: cancel, outcome unknown
            try:
                tasks.transition(row["task_id"], "cancelled")
                out["reconciled"].append(
                    {"task_id": row["task_id"], "action": "cancelled-unacknowledged"}
                )
            except Exception:
                out["reconciled"].append({"task_id": row["task_id"], "action": "stale-race-retry"})
    return out
