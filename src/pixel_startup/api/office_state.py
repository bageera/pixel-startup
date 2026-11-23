"""G16 read model: one sanitized snapshot for office/dashboard status displays.

Public + unauthenticated by design (display data only, matching the JS
baseline's public GET /api/config): internals are stripped here once, so no
caller of this surface can see grants, binding internals or provider endpoints.
Status provenance is explicit: `observed` (live adapter poll) vs `registry`
(SQLite snapshot) — never merged.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from pixel_startup.persistence.db import Database, StorageError
from pixel_startup.persistence.store import EmployeeStore
from pixel_startup.security.auth import require_auth

router = APIRouter()
_DEFAULT_GRANTS = {
    "hermes": ["delegate.hermes.task"],
    "openclaw": ["delegate.openclaw.task"],
}


class OnboardRequest(BaseModel):
    """CHO-authored harness onboarding (G10 lifecycle): the admin session is
    the trust boundary — register, bind the native identity, verify, activate."""

    employee_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=120)
    harness: str
    native_agent_id: str = Field(min_length=1, max_length=160)
    harness_instance_id: str = Field(min_length=1, max_length=160)
    sprite_id: str = "char_2"
    profile_label: str = ""
    grants: list[str] | None = None


@router.post("/api/v1/office/onboard")
async def onboard_employee(
    body: OnboardRequest, request: Request, _auth: str = Depends(require_auth)
) -> dict:
    if body.harness not in ("hermes", "openclaw"):
        raise HTTPException(status_code=400, detail=f"unsupported harness: {body.harness}")
    container = request.app.state.container
    store: EmployeeStore = container.providers["employees"]
    try:
        store.upsert(
            {
                "employee_id": body.employee_id,
                "display_name": body.display_name,
                "sprite_id": body.sprite_id,
                "harness": body.harness,
                "operational_scope": "company",
                "authorization_context": (
                    body.grants if body.grants is not None else _DEFAULT_GRANTS[body.harness]
                ),
                "lifecycle": "registered",
                "asserted_id": body.employee_id,
            }
        )
        binding = store.bind(
            body.employee_id,
            body.harness,
            body.native_agent_id,
            body.harness_instance_id,
            body.profile_label,
        )
        store.verify_binding(body.employee_id, binding["binding_id"])
        active = store.set_lifecycle(body.employee_id, "available")
    except StorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    presence = {"online": None}
    for reg in container.registry.all():
        handler = getattr(reg, "handler", None)
        if (
            reg.category.value != "agent_harness"
            or handler is None
            or not hasattr(handler, "probe")
        ):
            continue
        try:
            probe = handler.probe()
            if worker_matches(probe, body.harness, body.native_agent_id):
                presence = {"online": bool(probe["online"]), "source": "observed-poll"}
        except Exception:
            presence = {"online": None, "source": "probe-failed"}  # honest
    return {
        "employee": {
            "employee_id": active["employee_id"],
            "display_name": active["display_name"],
            "harness": active["harness"],
            "lifecycle": active["lifecycle"],
            "binding_verified": True,
        },
        "binding_id": binding["binding_id"],
        "presence": presence,
    }


def worker_matches(probe: dict, harness: str, native_agent_id: str) -> bool:
    """Presence match: any live session whose surface/session id carries the
    native agent id — best-effort; None presence stays honest when ambiguous."""
    if harness != "hermes":
        return False
    sessions = probe.get("sessions") or []
    for entry in sessions:
        haystack = f"{entry.get('session_id', '')} {entry.get('surface', '')}"
        if native_agent_id in haystack:
            return True
    return bool(sessions)


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
    employees = []
    for e in store.all():
        row = {
            "employee_id": e["employee_id"],
            "display_name": e["display_name"],
            "sprite_id": e["sprite_id"],
            "kind": e["kind"],
            "lifecycle": e["lifecycle"],
            "harness": e["harness"],
            "binding_verified": bool(e["verified_binding"]),
        }
        # store.all() already parsed verified_binding into a dict
        vb = e.get("verified_binding") or {}
        grant = f"delegate.{e['harness']}.task"
        grants = e.get("authorization_context") or []
        if isinstance(vb, dict) and vb.get("binding_id") and grant in grants:
            row["binding_id"] = vb["binding_id"]
        employees.append(row)

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
