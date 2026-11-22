"""Operational stores over the versioned schema: employees, tasks, approvals, audit.

Every write is an atomic single-transaction upsert/update; task transitions are
CAS-guarded (UPDATE ... WHERE status = expected) so interrupted writes cannot
half-apply. JSON migration utilities are repeatable and non-destructive: the
source files are read-only inputs and upserts key on stable identifiers, so
re-running never duplicates rows.
"""

import hashlib
import json
import sqlite3

from pixel_startup.persistence.db import (
    EMPLOYEE_LIFECYCLE_TRANSITIONS,
    TASK_TRANSITIONS,
    Database,
    StorageError,
    _now,
)

EMPLOYEE_COLUMNS = (
    "employee_id",
    "display_name",
    "sprite_id",
    "harness",
    "native_harness_id",
    "harness_instance_id",
    "session_id",
    "current_task_id",
    "operational_scope",
    "authorization_context",
    "lifecycle",
    "asserted_id",
    "verified_binding",
    "kind",
    "updated_at",
)


class EmployeeStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, employee: dict, when: str | None = None) -> None:
        row = {c: employee.get(c) for c in EMPLOYEE_COLUMNS if c != "updated_at"}
        row["authorization_context"] = json.dumps(row.get("authorization_context") or [])
        row["updated_at"] = when or _now()
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute(
                f"INSERT OR REPLACE INTO employees ({', '.join(EMPLOYEE_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in EMPLOYEE_COLUMNS)})",
                [row[c] for c in EMPLOYEE_COLUMNS],
            )
            self.db.conn.execute("COMMIT")
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise

    def count(self) -> int:
        row = self.db.conn.execute("SELECT COUNT(*) AS n FROM employees").fetchone()
        return int(row["n"])

    def get(self, employee_id: str) -> dict | None:
        cur = self.db.conn.execute("SELECT * FROM employees WHERE employee_id = ?", (employee_id,))
        found = cur.fetchone()
        if not found:
            return None
        row = dict(found)
        row["authorization_context"] = json.loads(row.get("authorization_context") or "[]")
        row["verified_binding"] = (
            json.loads(row["verified_binding"]) if row.get("verified_binding") else None
        )
        return row

    def all(self) -> list[dict]:
        cur = self.db.conn.execute("SELECT * FROM employees ORDER BY employee_id")
        out = []
        for found in cur.fetchall():
            row = dict(found)
            row["authorization_context"] = json.loads(row.get("authorization_context") or "[]")
            row["verified_binding"] = (
                json.loads(row["verified_binding"]) if row.get("verified_binding") else None
            )
            out.append(row)
        return out

    def _write(self, employee_id: str, assignments: dict, expected_lifecycle: str) -> dict:
        """CAS update: assignments applied only while the expected lifecycle holds."""
        sets = ", ".join(f"{c} = ?" for c in assignments)
        values = list(assignments.values()) + [_now(), employee_id, expected_lifecycle]
        cur = self.db.conn.execute(
            f"UPDATE employees SET {sets}, updated_at = ? WHERE employee_id = ? AND lifecycle = ?",
            values,
        )
        if cur.rowcount != 1:
            row = self.get(employee_id)
            current = row["lifecycle"] if row else None
            if current is None:
                raise StorageError(f"unknown employee: {employee_id}")
            raise StorageError(
                f"stale or illegal update for {employee_id} (lifecycle now {current})"
            )
        return self.get(employee_id) or {}

    def set_lifecycle(self, employee_id: str, new_status: str) -> dict:
        current = self.get(employee_id)
        if current is None:
            raise StorageError(f"unknown employee: {employee_id}")
        if new_status not in EMPLOYEE_LIFECYCLE_TRANSITIONS.get(current["lifecycle"], set()):
            raise StorageError(f"illegal lifecycle {current['lifecycle']} -> {new_status}")
        return self._write(employee_id, {"lifecycle": new_status}, current["lifecycle"])

    def bind(
        self,
        employee_id: str,
        harness: str,
        native_agent_id: str,
        harness_instance_id: str,
        profile_label: str = "",
        provenance: str = "",
    ) -> dict:
        """Bind a native harness identity (mandate: instance + native id mandatory).

        Uniqueness is enforced by the partial unique index (migration 2): a
        native identity may belong to exactly one employee — the impersonation
        guard. verified_binding is NOT granted here; only verify() grants it."""
        if harness not in ("openclaw", "hermes"):
            raise StorageError(f"unsupported harness: {harness}")
        if not native_agent_id or not harness_instance_id:
            raise StorageError("native_agent_id and harness_instance_id are required")
        binding_id = f"binding.{employee_id}.{harness}.{native_agent_id}"
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO runtime_bindings (binding_id, employee_id, harness,"
                " harness_instance_id, native_agent_id, profile_label, bound_at, active,"
                " provenance) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    binding_id,
                    employee_id,
                    harness,
                    harness_instance_id,
                    native_agent_id,
                    profile_label,
                    _now(),
                    provenance,
                ),
            )
            self.db.conn.execute(
                "UPDATE employees SET harness = ?, native_harness_id = ?,"
                " harness_instance_id = ?, updated_at = ? WHERE employee_id = ?",
                (harness, native_agent_id, harness_instance_id, _now(), employee_id),
            )
            self.db.conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            self.db.conn.execute("ROLLBACK")
            raise StorageError(
                f"impersonation refusal: {harness}:{native_agent_id} is already bound"
            ) from None
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise
        return {"binding_id": binding_id, **(self.get(employee_id) or {})}

    def revoke_binding(self, employee_id: str, binding_id: str) -> dict:
        """Runtime-binding revocation: lease deactivated + verification withdrawn.

        Identity proof dies here: the executor refuses any revoked binding."""
        cur = self.db.conn.execute(
            "UPDATE runtime_bindings SET active = 0"
            " WHERE binding_id = ? AND employee_id = ? AND active = 1",
            (binding_id, employee_id),
        )
        if cur.rowcount != 1:
            raise StorageError(f"unknown or already-revoked binding: {binding_id}")
        row = self.get(employee_id)
        if row is None:
            raise StorageError(f"unknown employee: {employee_id}")
        return self._write(employee_id, {"verified_binding": None}, row["lifecycle"])

    def verify_binding(self, employee_id: str, binding_id: str) -> dict:
        """Grant verified_binding only for an existing, active binding row."""
        row = self.db.conn.execute(
            "SELECT active FROM runtime_bindings WHERE binding_id = ?", (binding_id,)
        ).fetchone()
        if row is None or not row["active"]:
            raise StorageError(f"unknown or inactive binding: {binding_id}")
        claimed = self.get(employee_id)
        if claimed is None:
            raise StorageError(f"unknown employee: {employee_id}")
        expected = f"binding.{employee_id}.{claimed['harness']}.{claimed['native_harness_id']}"
        if binding_id != expected:
            raise StorageError("binding does not belong to this employee")
        result = self._write(
            employee_id,
            {"verified_binding": json.dumps({"binding_id": binding_id, "verified": True})},
            claimed["lifecycle"],
        )
        return result


class TaskStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, task: dict) -> dict:
        """Idempotent create: same task_id returns the existing row untouched."""
        existing = self.get(task["task_id"])
        if existing is not None:
            return existing
        ts = _now()
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute(
                "INSERT INTO tasks (task_id, kind, objective, operational_scope, requested_by,"
                " assigned_employee_id, status, correlation_id, created_at, payload, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task["task_id"],
                    task["kind"],
                    task["objective"],
                    task.get("operational_scope", "shared"),
                    task.get("requested_by", "owner"),
                    task.get("assigned_employee_id"),
                    task.get("status", "created"),
                    task.get("correlation_id", task["task_id"]),
                    ts,
                    json.dumps(task["payload"]) if task.get("payload") is not None else None,
                    ts,
                ),
            )
            self.db.conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            self.db.conn.execute("ROLLBACK")
            existing = self.get(task["task_id"])
            if existing is None:
                raise
            return existing
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise
        return self.get(task["task_id"]) or {}

    def get(self, task_id: str) -> dict | None:
        cur = self.db.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        found = cur.fetchone()
        return dict(found) if found else None

    def transition(self, task_id: str, new_status: str) -> dict:
        """Atomic CAS transition; raises StorageError on unknown task or illegal move."""
        task = self.get(task_id)
        if task is None:
            raise StorageError(f"unknown task: {task_id}")
        current = task["status"]
        if new_status not in TASK_TRANSITIONS.get(current, set()):
            raise StorageError(f"illegal transition {current} -> {new_status}")
        cur = self.db.conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ? AND status = ?",
            (new_status, _now(), task_id, current),
        )
        if cur.rowcount != 1:
            raise StorageError(f"stale transition raced for {task_id}")
        return self.get(task_id) or {}

    def attach(self, task_id: str, payload_patch: dict, new_status: str | None = None) -> dict:
        """Atomic payload merge (+ optional status move, same CAS guard).

        G23-3a/3b: plans and verification verdicts live in the payload column, so
        the schema never grows for them."""
        task = self.get(task_id)
        if task is None:
            raise StorageError(f"unknown task: {task_id}")
        current = task["status"]
        if new_status is not None and new_status not in TASK_TRANSITIONS.get(current, set()):
            raise StorageError(f"illegal transition {current} -> {new_status}")
        try:
            merged = json.loads(task["payload"]) if task["payload"] else {}
            if not isinstance(merged, dict):
                merged = {}
        except (json.JSONDecodeError, ValueError):
            merged = {}
        merged.update(payload_patch)
        if new_status is not None:
            cur = self.db.conn.execute(
                "UPDATE tasks SET payload = ?, status = ?, updated_at = ?"
                " WHERE task_id = ? AND status = ?",
                (json.dumps(merged), new_status, _now(), task_id, current),
            )
        else:
            cur = self.db.conn.execute(
                "UPDATE tasks SET payload = ?, updated_at = ? WHERE task_id = ? AND status = ?",
                (json.dumps(merged), _now(), task_id, current),
            )
        if cur.rowcount != 1:
            raise StorageError(f"stale attach raced for {task_id}")
        return self.get(task_id) or {}


class ApprovalStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, request: dict) -> dict:
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute(
                "INSERT INTO approvals (request_id, task_id, requested_by, reason,"
                " operational_scope, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (
                    request["request_id"],
                    request["task_id"],
                    request["requested_by"],
                    request["reason"],
                    request.get("operational_scope", "shared"),
                    _now(),
                ),
            )
            self.db.conn.execute("COMMIT")
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise
        return self.get(request["request_id"]) or {}

    def decide(self, request_id: str, decision: str, decided_by: str) -> dict:
        if decision not in ("approved", "denied"):
            raise StorageError(f"invalid decision: {decision}")
        cur = self.db.conn.execute(
            "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?"
            " WHERE request_id = ? AND status = 'pending'",
            (decision, decided_by, _now(), request_id),
        )
        if cur.rowcount != 1:
            raise StorageError(f"approval {request_id} not pending")
        return self.get(request_id) or {}

    def get(self, request_id: str) -> dict | None:
        cur = self.db.conn.execute("SELECT * FROM approvals WHERE request_id = ?", (request_id,))
        found = cur.fetchone()
        return dict(found) if found else None


class SettingsStore:
    """G22: app-level settings (environment selection) in the app_settings table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str) -> str | None:
        row = self.db.conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, _now()),
            )
            self.db.conn.execute("COMMIT")
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise


class AuditStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def append(self, event: dict) -> None:
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO audit_events (event_id, occurred_at, actor,"
                " action, outcome, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event["event_id"],
                    event.get("occurred_at", _now()),
                    event.get("actor", "system"),
                    event.get("action", "event"),
                    event.get("outcome", "allow"),
                    json.dumps(event.get("detail", {})),
                ),
            )
            self.db.conn.execute("COMMIT")
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise

    def count(self) -> int:
        row = self.db.conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()
        return int(row["n"])


class OfficePersistenceProvider:
    """G5 provider handler for the Python operational store (read-only ops for now)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def invoke(self, operation: str, payload: dict) -> dict:
        if operation == "health.counts":
            counts = {}
            for table in ("employees", "runtime_bindings", "tasks", "approvals", "audit_events"):
                row = self.db.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                counts[table] = int(row["n"])
            return {"counts": counts}
        raise StorageError(f"unsupported operation: {operation}")


def sync_provider_records(db: Database, registry) -> int:
    """G6 leftover closed (G10): snapshot live provider registry state into
    provider_records. Upsert-only — existing configuration rows are updated to
    the registry's CURRENT state; no other tables are touched."""
    upserted = 0
    for reg in registry.all():
        db.conn.execute("BEGIN IMMEDIATE")
        try:
            db.conn.execute(
                "INSERT OR REPLACE INTO provider_records (provider_id, category,"
                " implementation, state, endpoint, capabilities, priority, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    reg.provider_id,
                    reg.category.value,
                    reg.implementation,
                    reg.state.value,
                    reg.endpoint,
                    json.dumps(list(reg.capabilities)),
                    reg.priority,
                    _now(),
                ),
            )
            db.conn.execute("COMMIT")
        except Exception:
            db.conn.execute("ROLLBACK")
            raise
        upserted += 1
    return upserted


def migrate_office_json(db: Database, agents_json_path: str) -> dict:
    """Repeatable, non-destructive migration of the JS office roster.

    Reads data/agents.json (visual roster) and upserts employees keyed by
    stable employee_id. Visual sync touches ONLY visual identity fields
    (display_name, sprite_id) — harness bindings, lifecycle and verified
    bindings are never clobbered on re-run. Source file is read-only.
    """
    raw = open(agents_json_path, "rb").read()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    roster = json.loads(raw)
    upserted = 0
    for agent in roster:
        name = str(agent.get("name", f"agent_{agent.get('id')}"))
        employee_id = f"employee.{name.lower()}"
        db.conn.execute("BEGIN IMMEDIATE")
        try:
            db.conn.execute(
                "INSERT INTO employees (employee_id, display_name, sprite_id, harness,"
                " native_harness_id, harness_instance_id, session_id, current_task_id,"
                " operational_scope, authorization_context, lifecycle, asserted_id,"
                " verified_binding, kind, updated_at)"
                " VALUES (?, ?, ?, 'none', '', '', '', '', 'shared', '[]', ?, ?, NULL,"
                " 'operational', ?)"
                " ON CONFLICT(employee_id) DO UPDATE SET"
                " display_name = excluded.display_name, sprite_id = excluded.sprite_id,"
                " updated_at = excluded.updated_at",
                (
                    employee_id,
                    name,
                    f"char_{agent.get('id')}",
                    "registered" if agent.get("active", True) else "offline",
                    employee_id,
                    _now(),
                ),
            )
            db.conn.execute("COMMIT")
        except Exception:
            db.conn.execute("ROLLBACK")
            raise
        upserted += 1
    return {"employees_upserted": upserted, "source_sha256": source_sha256}


def align_employee_roster(
    db: Database,
    agents_json_path: str,
    kind_map: dict[str, str] | None = None,
    harness_intents: dict[str, str] | None = None,
    observed: dict | None = None,
) -> dict:
    """G9 employee/character alignment.

    Visual characters stay the visual layer's data; the registry records
    classification (kind), harness INTENT (never auto-bound — a native id plus
    harness-instance proof is required before verified_binding), and passes
    observed runtime probe results through verbatim WITHOUT deriving employee
    lifecycle from them (no false 'available' from probe output).
    """
    migrated = migrate_office_json(db, agents_json_path)
    kind_map = kind_map or {}
    for employee_id, kind in kind_map.items():
        if kind not in ("operational", "decorative"):
            raise StorageError(f"invalid kind: {kind}")
        db.conn.execute("UPDATE employees SET kind = ? WHERE employee_id = ?", (kind, employee_id))
    harness_intents = harness_intents or {}
    pending_bindings = []
    for employee_id, harness in harness_intents.items():
        # intent never overwrites an existing proof-backed binding
        db.conn.execute(
            "UPDATE employees SET harness = ?, updated_at = ?"
            " WHERE employee_id = ? AND native_harness_id = ''",
            (harness, _now(), employee_id),
        )
        row = db.conn.execute(
            "SELECT native_harness_id FROM employees WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        if row is None or not row["native_harness_id"]:
            pending_bindings.append(
                {
                    "employee_id": employee_id,
                    "harness": harness,
                    "reason": "awaiting native agent id + instance proof",
                }
            )
    store = EmployeeStore(db)
    employees = [
        {
            "employee_id": e["employee_id"],
            "display_name": e["display_name"],
            "sprite_id": e["sprite_id"],
            "kind": e["kind"],
            "lifecycle": e["lifecycle"],
            "harness": e["harness"],
            "native_agent_id": e["native_harness_id"] or None,
            "verified_binding": e["verified_binding"],
        }
        for e in store.all()
    ]
    return {
        **migrated,
        "employees": employees,
        "pending_bindings": pending_bindings,
        "observed_runtime": observed or {},
    }


class GraphStore:
    """G30-A: organizational graph (typed nodes + explicit edges, stable ids).

    Scope isolation lives at the edge: a node never appears in another scope's
    listing, and a child inherits its parent's scope (cross-scope parenting is
    refused). Edges are idempotent — re-adding an existing (from,to,kind)
    returns the current row, never a duplicate.
    """

    KIND_PARENTS = {  # allowed parent kinds per child kind
        "goal": {"organization", "idea"},
        "idea": {"organization", "idea", "goal"},
        "project": {"idea", "goal"},
        "milestone": {"project"},
        "task": {"project", "milestone"},
    }

    def __init__(self, db: Database) -> None:
        self.db = db
        self._ensure_org()

    def _ensure_org(self) -> None:
        row = self.db.conn.execute("SELECT id FROM graph_nodes WHERE id = 'org'").fetchone()
        if row is None:
            ts = _now()
            self.db.conn.execute(
                "INSERT OR IGNORE INTO graph_nodes"
                " (id, kind, title, status, scope, created_at, updated_at)"
                " VALUES ('org', 'organization', 'Pixel Startup',"
                " 'active', 'company', ?, ?)",
                (ts, ts),
            )

    def _get(self, node_id: str):
        return self.db.conn.execute("SELECT * FROM graph_nodes WHERE id = ?", (node_id,)).fetchone()

    def get(self, node_id: str) -> dict | None:
        row = self._get(node_id)
        return dict(row) if row else None

    def create(self, payload: dict) -> dict:
        kind = payload.get("kind") or ""
        title = str(payload.get("title") or "").strip()
        if kind not in self.KIND_PARENTS:
            raise ValueError(f"unsupported node kind: {kind}")
        if not title:
            raise ValueError("title is required")
        parent_id = payload.get("parent_id") or "org"
        scope = payload.get("scope") or "company"
        if parent_id == "org" and scope == "household":
            parent_id = None  # household is its own root; company org row stays single
        elif scope != "company":
            parent = self.get(parent_id)
            if parent is None or parent["scope"] != scope:
                raise ValueError("cross-scope parenting refused (company/household isolation)")
        parent = self.get(parent_id) if parent_id else None
        if parent_id and parent is None:
            raise ValueError(f"parent {parent_id} not found")
        if parent is not None and parent["kind"] not in self.KIND_PARENTS[kind]:
            raise ValueError(f"{kind} cannot live under {parent['kind']}")
        scope = parent["scope"] if parent else scope
        import uuid

        node_id = f"{kind}-{uuid.uuid4().hex[:12]}"
        ts = _now()
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute(
                "INSERT INTO graph_nodes (id, kind, title, status, parent_id, priority,"
                " due_at, cadence, verified, scope, detail, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    node_id,
                    kind,
                    title,
                    "active",
                    parent_id,
                    int(payload.get("priority") or 2),
                    payload.get("due_at"),
                    payload.get("cadence"),
                    0,
                    scope,
                    payload.get("detail"),
                    ts,
                    ts,
                ),
            )
            self.db.conn.execute("COMMIT")
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise
        node = self.get(node_id)
        assert node is not None  # just inserted/updated
        return node

    def patch(self, node_id: str, patch: dict) -> dict:
        row = self._get(node_id)
        if row is None:
            raise ValueError(f"node {node_id} not found")
        row = dict(row)
        allowed = ("title", "status", "priority", "due_at", "cadence", "detail")
        sets: list[str] = []
        args: list[object] = []
        for field in allowed:
            if field in patch:
                value = patch[field]
                if field == "status":
                    if value not in ("active", "paused", "done", "archived"):
                        raise ValueError(f"unsupported status: {value}")
                if field == "priority":
                    if not (1 <= int(value) <= 5):
                        raise ValueError("priority must be 1-5")
                if field == "title" and not str(value or "").strip():
                    raise ValueError("title cannot be empty")
                setattr_row = field
                sets.append(f"{setattr_row} = ?")
                args.append(value)
        if "verified" in patch:
            if row["kind"] not in ("milestone", "task"):
                raise ValueError("verified applies to milestones and tasks")
            if patch["verified"] not in (0, 1, True, False):
                raise ValueError("verified must be 0/1")
            sets.append("verified = ?")
            args.append(int(bool(patch["verified"])))
            if patch["verified"]:
                sets.append("status = ?")
                args.append("done")
            elif row["status"] == "done":
                sets.append("status = ?")
                args.append("active")
        if not sets:
            return row  # nothing to change: return current, honest no-op
        sets.append("updated_at = ?")
        args.append(_now())
        args.append(node_id)
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute(f"UPDATE graph_nodes SET {', '.join(sets)} WHERE id = ?", args)
            self.db.conn.execute("COMMIT")
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise
        node = self.get(node_id)
        assert node is not None  # just inserted/updated
        return node

    def add_edge(self, from_id: str, to_id: str, kind: str) -> dict:
        if kind not in ("depends_on", "related", "evidence"):
            raise ValueError(f"unsupported edge kind: {kind}")
        a, b = self.get(from_id), self.get(to_id)
        if a is None or b is None:
            raise ValueError("edge endpoints not found")
        if a["scope"] != b["scope"]:
            raise ValueError("cross-scope edges refused (company/household isolation)")
        if from_id == to_id:
            raise ValueError("self edges refused")
        existing = self.db.conn.execute(
            "SELECT * FROM graph_edges WHERE from_id = ? AND to_id = ? AND kind = ?",
            (from_id, to_id, kind),
        ).fetchone()
        if existing:
            return dict(existing)  # idempotent: a duplicate is the same record
        if kind == "depends_on":
            # cycle guard: following depends_on from `to` must never reach `from`
            seen: set[str] = set()
            stack = [to_id]
            while stack:
                cur = stack.pop()
                if cur == from_id:
                    raise ValueError("depends_on cycle refused")
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(
                    r["to_id"]
                    for r in self.db.conn.execute(
                        "SELECT to_id FROM graph_edges WHERE from_id = ? AND kind = 'depends_on'",
                        (cur,),
                    ).fetchall()
                )
        import uuid

        edge = {
            "id": f"edge-{uuid.uuid4().hex[:12]}",
            "from_id": from_id,
            "to_id": to_id,
            "kind": kind,
            "created_at": _now(),
        }
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute(
                "INSERT INTO graph_edges (id, from_id, to_id, kind, created_at) VALUES (?,?,?,?,?)",
                (edge["id"], from_id, to_id, kind, edge["created_at"]),
            )
            self.db.conn.execute("COMMIT")
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise
        return edge

    def remove_edge(self, edge_id: str) -> bool:
        cur = self.db.conn.execute("SELECT id FROM graph_edges WHERE id = ?", (edge_id,)).fetchone()
        if cur is None:
            return False
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            self.db.conn.execute("DELETE FROM graph_edges WHERE id = ?", (edge_id,))
            self.db.conn.execute("COMMIT")
        except Exception:
            self.db.conn.execute("ROLLBACK")
            raise
        return True

    def list(self, scope: str = "company") -> tuple[list[dict], list[dict]]:
        nodes = [
            dict(r)
            for r in self.db.conn.execute(
                "SELECT * FROM graph_nodes WHERE scope = ? ORDER BY created_at, id",
                (scope,),
            )
        ]
        edges = [
            dict(r)
            for r in self.db.conn.execute(
                "SELECT e.* FROM graph_edges e JOIN graph_nodes n ON n.id = e.from_id"
                " WHERE n.scope = ? ORDER BY e.created_at, e.id",
                (scope,),
            )
        ]
        return nodes, edges
