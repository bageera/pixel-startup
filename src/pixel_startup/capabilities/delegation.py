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
    ApprovalStore,
    AuditStore,
    EmployeeStore,
    StorageError,
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


def _plan_request_id(task_id: str) -> str:
    return f"request.plan.{digest8(task_id)}"


def _audit(audit: AuditStore, task_id: str, detail: str, outcome: str = "info") -> None:
    audit.append(
        {
            "event_id": f"deleg.{task_id}.{digest8(task_id + detail)}",
            "occurred_at": _now(),
            "actor": "system",
            "action": "delegation",
            "outcome": outcome,
            "detail": detail,
        }
    )


def _dispatch_task(db: Database, registry, audit: AuditStore, tasks: TaskStore, row: dict) -> dict:
    """Shared dispatch of an authorized task (G23-3a: used by both agent and
    owner-approved plan paths). Cancel-and-audit on every non-dispatch result."""
    task_id = row["task_id"]
    harness = _harness_of(db, row)
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
        _audit(audit, task_id, json.dumps(result), "deny")
        return result
    resp = (
        reg.handler.invoke(
            "task.submit",
            {
                "task_id": task_id,
                "objective": row["objective"],
                "scope": row["operational_scope"],
                "correlation_id": row["correlation_id"],
                "requested_by": row["requested_by"],
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
        _audit(audit, task_id, json.dumps(result), "deny")
        return result
    fresh = tasks.get(task_id)
    if fresh is not None and fresh["status"] in ("created", "planning"):
        tasks.transition(task_id, "authorized")  # entry-state aware (fresh / plan paths)
    tasks.transition(task_id, "dispatched")
    remote_raw = resp.get("result") or {}
    remote_id = remote_raw.get("taskId") if isinstance(remote_raw, dict) else None
    tasks.transition(task_id, "running")
    _audit(
        audit, task_id, json.dumps({"task_id": task_id, "dispatched": True, "remote_id": remote_id})
    )
    return {"task_id": task_id, "dispatched": True, "remote_id": remote_id, "blocker": None}


def make_delegation_adapter(db: Database, registry, audit: AuditStore):
    """Factory: executor adapters are plain callables; services are closed over."""

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
                "payload": {
                    "target": target_id,
                    **({"verify": True} if payload.get("verify") else {}),
                },
            }
        )

        # 2. G23-3a plan gate: hold in planning with a pending owner approval
        if payload.get("plan_required"):
            tasks.transition(task_id, "planning")
            request_id = _plan_request_id(task_id)
            approvals = ApprovalStore(db)
            if approvals.get(request_id) is None:
                approvals.create(
                    {
                        "request_id": request_id,
                        "task_id": task_id,
                        "requested_by": employee_id,
                        "reason": f"plan review: {objective[:180]}",
                        "operational_scope": scope,
                    }
                )
            result = {
                "task_id": task_id,
                "dispatched": False,
                "planning": True,
                "approval_request_id": request_id,
            }
            _audit(audit, task_id, json.dumps(result))
            return result

        created = tasks.get(task_id)
        if created is None:  # create() just returned; never None in practice
            raise DelegationFailure(f"task vanished after create: {task_id}")
        return _dispatch_task(db, registry, audit, tasks, created)

    return adapter


def make_plan_flow(db: Database, registry, audit: AuditStore):
    """G23-3a: plan drafting (LLM), owner review, then the shared dispatch.

    The plan TEXT is inert data: the approve decision is an approvals-row record
    made by the owner session only. Plan content can never create authorization."""

    def approval_state(task_id: str) -> dict | None:
        return ApprovalStore(db).get(_plan_request_id(task_id))

    def plan_for(task_id: str) -> dict:
        tasks = TaskStore(db)
        row = tasks.get(task_id)
        if row is None:
            raise DelegationFailure(f"unknown task: {task_id}")
        payload = json.loads(row["payload"]) if row["payload"] else {}
        if payload.get("plan"):
            plan = payload["plan"]
            fresh = payload.get("planned_via")
        else:
            employee = EmployeeStore(db).get(row["assigned_employee_id"])
            prompt = (
                f"Task: {row['objective']}\n"
                f"Assigned to: {row['assigned_employee_id']}"
                f" (harness: {(employee or {}).get('harness') or 'none'})."
                " Produce a SHORT execution plan: 3-6 numbered steps, nothing else."
            )
            result = registry.invoke_provider(
                "litellm",
                "chat.completions",
                {"messages": [{"role": "user", "content": prompt}]},
            )
            plan = result.get("content", "")
            fresh = f"litellm:{result.get('model')}"
            tasks.attach(task_id, {"plan": plan, "planned_via": fresh})
        approval = approval_state(task_id)
        final = tasks.get(task_id) or row
        return {
            "task_id": task_id,
            "plan": plan,
            "status": final["status"],
            "approval_request_id": approval["request_id"] if approval else None,
            "approval_status": approval["status"] if approval else None,
        }

    def review(task_id: str, decision: str, decided_by: str) -> dict:
        if decision not in ("approved", "denied"):
            raise DelegationFailure("decision must be approved or denied")
        approvals = ApprovalStore(db)
        approval = approvals.get(_plan_request_id(task_id))
        tasks = TaskStore(db)
        row = tasks.get(task_id)
        if row is None:
            raise DelegationFailure(f"unknown task: {task_id}")
        existing = approval["status"] if approval else None
        if existing == "denied" or (approval is None and decision == "denied"):
            if row["status"] == "planning":  # stale planning task, reconciled loudly
                tasks.transition(task_id, "cancelled")
            final_row = tasks.get(task_id) or row
            _audit(audit, task_id, json.dumps({"plan_review": "denied", "by": decided_by}), "deny")
            return {"task_id": task_id, "status": final_row["status"], "dispatched": False}
        if existing == "approved" and row["status"] != "planning":
            # decision already recorded; nothing new to authorize or dispatch
            return {**row, "plan_review": "approved", "dispatched": None, "replayed": True}
        try:
            approvals.decide(_plan_request_id(task_id), decision, decided_by)
        except StorageError as exc:  # decided elsewhere / raced
            raise DelegationFailure(f"approval no longer pending: {exc}") from None
        if decision == "denied":
            tasks.transition(task_id, "cancelled")
            _audit(audit, task_id, json.dumps({"plan_review": "denied", "by": decided_by}), "deny")
            return {"task_id": task_id, "status": "cancelled", "dispatched": False}
        dispatched_row = tasks.get(task_id)
        if dispatched_row is None:  # same task read back; never None in practice
            raise DelegationFailure(f"task vanished: {task_id}")
        return {
            **_dispatch_task(db, registry, audit, tasks, dispatched_row),
            "plan_review": "approved",
        }

    return {"plan_for": plan_for, "review": review, "approval_state": approval_state}


def make_result_retriever(db: Database, registry, audit: AuditStore):
    """Fetch results for a dispatched task; uncertain outcomes stay uncertain."""

    def retriever(task_id: str) -> dict:
        tasks = TaskStore(db)
        row = tasks.get(task_id)
        if row is None:
            raise DelegationFailure(f"unknown task: {task_id}")
        if row["status"] in ("completed", "verified"):
            saved = db.conn.execute(
                "SELECT result FROM task_results WHERE task_id = ?", (task_id,)
            ).fetchone()
            saved_result = dict(saved).get("result") if saved else "{}"
            if isinstance(saved_result, str):
                try:
                    saved_result = json.loads(saved_result)
                except (json.JSONDecodeError, ValueError):
                    pass  # verbatim passthrough of a non-JSON stored blob
            payload = json.loads(row["payload"]) if row["payload"] else {}
            return {
                "result": saved_result,
                "task_id": task_id,
                "status": row["status"],
                "verification": payload.get("verification"),
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
            output = remote.get("output")
            tasks.transition(task_id, "completed")
            db.conn.execute(
                "INSERT INTO task_results (task_id, status, result, updated_at)"
                " VALUES (?, 'completed', ?, ?)"
                " ON CONFLICT(task_id) DO UPDATE SET status='completed', result=excluded.result,"
                " updated_at=excluded.updated_at",
                (task_id, json.dumps(output), _now()),
            )
            verification = _verify_result(db, registry, audit, tasks, task_id, output)
            if verification["verdict"] == "verified":
                final = tasks.attach(task_id, {"verification": verification}, new_status="verified")
            elif verification["verdict"] != "skipped":
                final = tasks.attach(task_id, {"verification": verification})
            else:
                final = {"status": "completed"}
            return {
                "task_id": task_id,
                "status": final["status"],
                "result": output,
                "verification": verification if verification["verdict"] != "skipped" else None,
            }
        if state == "failed":
            tasks.transition(task_id, "failed")
            return {"task_id": task_id, "status": "failed", "result": remote.get("output")}
        return {"task_id": task_id, "status": row["status"], "uncertain": True}

    return retriever


def _verify_result(
    db: Database, registry, audit: AuditStore, tasks: TaskStore, task_id: str, stored_output
) -> dict:
    """G23-3b: independent verification of a completed delegation — one fresh
    harness re-poll compared against the stored result.

    NEVER a re-dispatch: side effects are never re-run (G14 mandate). Verdicts,
    verbatim: verified (fresh == stored), divergent (mismatch), unavailable
    (harness/re-poll not usable), skipped (task carries no verify flag)."""
    row = tasks.get(task_id)
    payload = json.loads(row["payload"]) if row and row["payload"] else {}
    if not payload.get("verify"):
        return {"verdict": "skipped"}
    provider = HARNESS_PROVIDER.get(_harness_of(db, row)) if row is not None else None
    reg = registry.get(provider) if provider else None
    if reg is None or reg.handler is None:
        verdict, detail = "unavailable", {"reason": "harness unavailable"}
    else:
        resp = reg.handler.invoke("task.result", {"task_id": task_id})
        fresh = (resp.get("result") or {}) if resp.get("supported") else None
        detail: dict = {}
        if isinstance(fresh, dict) and fresh.get("state") == "completed":
            same = json.dumps(fresh.get("output"), sort_keys=True) == json.dumps(
                stored_output, sort_keys=True
            )
            verdict, detail = (
                ("verified", {})
                if same
                else (
                    "divergent",
                    {"detail": "fresh re-poll diverged from the stored result"},
                )
            )
        else:
            verdict, detail = "unavailable", {"reason": "harness re-poll not supported"}
    _audit(
        audit,
        task_id,
        json.dumps({"verification": verdict, **detail}),
        "info" if verdict == "verified" else "error",
    )
    return {"verdict": verdict, **detail, "checked_at": _now()}


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
