"""G16 read model: one sanitized snapshot for office/dashboard status displays.

Public + unauthenticated by design (display data only, matching the JS
baseline's public GET /api/config): internals are stripped here once, so no
caller of this surface can see grants, binding internals or provider endpoints.
Status provenance is explicit: `observed` (live adapter poll) vs `registry`
(SQLite snapshot) — never merged.
"""

import time

from fastapi import APIRouter, Request

from pixel_startup.persistence.db import Database
from pixel_startup.persistence.store import EmployeeStore

router = APIRouter()

_TASK_ACTIVE = ("dispatched", "running", "waiting")


def _observations(container) -> list[dict]:
    out = []
    for reg in sorted(container.registry.all(), key=lambda r: r.provider_id):
        if reg.category.value != "agent_harness":
            continue
        entry = {
            "provider_id": reg.provider_id,
            "registry_state": reg.state.value,
            "source": "registry",
            "observed": None,
        }
        handler = getattr(reg, "handler", None)
        if handler is not None and hasattr(handler, "probe"):
            try:
                probe = handler.probe()
                entry["observed"] = {
                    "online": probe["online"],
                    "platforms": probe.get("platforms") or [],
                    "live_sessions": len(probe.get("sessions") or []),
                }
                entry["source"] = "observed-poll"
            except Exception:
                entry["observed"] = None  # probe failure is honest, never faked
        out.append(entry)
    return out


@router.get("/api/v1/office/state")
async def office_state(request: Request) -> dict:
    container = request.app.state.container
    db: Database = container.providers["db"]

    store = EmployeeStore(db)
    employees = [
        {
            "employee_id": e["employee_id"],
            "display_name": e["display_name"],
            "sprite_id": e["sprite_id"],
            "kind": e["kind"],
            "lifecycle": e["lifecycle"],
            "harness": e["harness"],
            "binding_verified": bool(e["verified_binding"]),
        }
        for e in store.all()
    ]

    task_counts: dict[str, int] = {}
    for row in db.conn.execute(
        "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
    ).fetchall():
        task_counts[row["status"]] = int(row["n"])
    active_tasks = [
        {
            "task_id": r["task_id"],
            "kind": r["kind"],
            "status": r["status"],
            "employee_id": r["assigned_employee_id"],
            "updated_at": r["updated_at"],
        }
        for r in db.conn.execute(
            "SELECT task_id, kind, status, assigned_employee_id, updated_at FROM tasks"
            " WHERE status IN (?, ?, ?) ORDER BY updated_at DESC LIMIT 10",
            _TASK_ACTIVE,
        ).fetchall()
    ]
    pending_approvals = int(
        db.conn.execute("SELECT COUNT(*) AS n FROM approvals WHERE status = 'pending'").fetchone()[
            "n"
        ]
    )
    pending_capability_approvals = int(
        db.conn.execute(
            "SELECT COUNT(*) AS n FROM capability_approvals WHERE status = 'pending'"
        ).fetchone()["n"]
    )
    providers = [
        {"provider_id": r["provider_id"], "category": r["category"], "state": r["state"]}
        for r in db.conn.execute(
            "SELECT provider_id, category, state FROM provider_records ORDER BY provider_id"
        ).fetchall()
    ]
    activity = [
        {
            "occurred_at": r["occurred_at"],
            "actor": r["actor"],
            "action": r["action"],
            "outcome": r["outcome"],
        }
        for r in db.conn.execute(
            "SELECT occurred_at, actor, action, outcome FROM audit_events"
            " ORDER BY occurred_at DESC, event_id DESC LIMIT 20"
        ).fetchall()
    ]

    return {
        "employees": employees,
        "observations": _observations(container),
        "tasks": {"counts": task_counts, "active": active_tasks},
        "approvals": {
            "pending_tasks": pending_approvals,
            "pending_capabilities": pending_capability_approvals,
        },
        "providers": providers,
        "activity": activity,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }
