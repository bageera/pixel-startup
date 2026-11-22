"""SQLite operational persistence (G6) — versioned migrations + backup/restore.

Scope discipline (mandate G6): SQLite manages employee registry, runtime
bindings, task lifecycle, provider configuration, activity metadata, approval
records and audit references ONLY. Customer databases, financial records,
repositories, ChromaDB contents and MinIO objects stay external.

Concurrency: WAL journal + BEGIN IMMEDIATE for multi-statement writes +
transaction-guarded CAS updates. All files live under PIXEL_DATA_DIR.
"""

import os
import sqlite3
import time
from os import PathLike

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS employees (
            employee_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            sprite_id TEXT NOT NULL,
            harness TEXT NOT NULL,
            native_harness_id TEXT NOT NULL DEFAULT '',
            harness_instance_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            current_task_id TEXT NOT NULL DEFAULT '',
            operational_scope TEXT NOT NULL DEFAULT 'shared',
            authorization_context TEXT NOT NULL DEFAULT '[]',
            lifecycle TEXT NOT NULL DEFAULT 'registered',
            asserted_id TEXT,
            verified_binding TEXT,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS runtime_bindings (
            binding_id TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL REFERENCES employees(employee_id),
            harness TEXT NOT NULL,
            harness_instance_id TEXT NOT NULL,
            native_agent_id TEXT NOT NULL,
            profile_label TEXT NOT NULL DEFAULT '',
            bound_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )""",
        """CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            objective TEXT NOT NULL,
            operational_scope TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            assigned_employee_id TEXT,
            status TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload TEXT,
            updated_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_correlation ON tasks(correlation_id)",
        """CREATE TABLE IF NOT EXISTS approvals (
            request_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            requested_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            operational_scope TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS audit_events (
            event_id TEXT PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            outcome TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '{}'
        )""",
        """CREATE TABLE IF NOT EXISTS provider_records (
            provider_id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            implementation TEXT NOT NULL,
            state TEXT NOT NULL,
            endpoint TEXT,
            capabilities TEXT NOT NULL DEFAULT '[]',
            priority INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )""",
    ],
    2: [
        # G9: decorative vs operational classification
        "ALTER TABLE employees ADD COLUMN kind TEXT NOT NULL DEFAULT 'operational'",
        # G9: impersonation guard — one native harness identity may bind to one employee
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_employees_unique_native
           ON employees (harness, native_harness_id)
           WHERE harness != 'none' AND native_harness_id != ''""",
        # G9: binding provenance (which surface/harness evidence produced the binding)
        "ALTER TABLE runtime_bindings ADD COLUMN provenance TEXT NOT NULL DEFAULT ''",
    ],
    3: [
        # G22: environment selection persists through the Python backend
        """CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    ],
    4: [
        # G10: capability execution ledger (replay protection via UNIQUE request_key)
        """CREATE TABLE IF NOT EXISTS executions (
            request_key TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL REFERENCES employees(employee_id),
            capability TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT '{}',
            workload TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_executions_employee ON executions(employee_id)",
        # G10: capability-level approvals (task approvals stay for G14 delegation)
        """CREATE TABLE IF NOT EXISTS capability_approvals (
            approval_id TEXT PRIMARY KEY,
            capability TEXT NOT NULL,
            scope TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            decided_by TEXT,
            created_at TEXT NOT NULL
        )""",
    ],
    5: [
        # G14: delegation results keyed by task (side-effect outcomes never guessed)
        """CREATE TABLE IF NOT EXISTS task_results (
            task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
            status TEXT NOT NULL,
            result TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    ],
    6: [
        # G30-A: organizational graph — typed nodes (organization/goal/idea/
        # project/milestone/task) + explicit edges (dependency/related/evidence).
        # One table per kind would be seven near-identical tables; kind + CHECK
        # keeps the schema minimal and the projection deterministic.
        """CREATE TABLE IF NOT EXISTS graph_nodes (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN
                ('organization','goal','idea','project','milestone','task')),
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','paused','done','archived')),
            parent_id TEXT REFERENCES graph_nodes(id) ON DELETE CASCADE,
            priority INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 5),
            due_at TEXT,
            cadence TEXT,
            verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
            scope TEXT NOT NULL DEFAULT 'company' CHECK (scope IN ('company','household')),
            detail TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS graph_edges (
            id TEXT PRIMARY KEY,
            from_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
            to_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('depends_on','related','evidence')),
            created_at TEXT NOT NULL,
            UNIQUE (from_id, to_id, kind)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_graph_nodes_parent ON graph_nodes(parent_id)",
        "CREATE INDEX IF NOT EXISTS ix_graph_edges_to ON graph_edges(to_id)",
    ],
}

EMPLOYEE_LIFECYCLE_TRANSITIONS: dict[str, set[str]] = {
    "discovered": {"unassigned", "retired"},
    "unassigned": {"registered", "retired"},
    "registered": {"available", "retired"},
    "available": {"working", "waiting", "blocked", "offline", "retired", "on_demand"},
    "working": {"available", "waiting", "blocked", "offline"},
    "waiting": {"available", "working", "blocked", "offline"},
    "blocked": {"available", "working", "offline", "retired"},
    "offline": {"available", "retired"},
    "on_demand": {"available", "working", "waiting", "offline", "retired"},
    "retired": set(),
}

TASK_TRANSITIONS: dict[str, set[str]] = {
    "created": {"authorized", "planning", "cancelled"},
    "planning": {"authorized", "cancelled"},  # G23-3a: plan-then-review gate
    "authorized": {"dispatched", "cancelled"},
    "dispatched": {"running", "cancelled"},
    "running": {"completed", "failed", "cancelled", "waiting"},
    "waiting": {"running", "cancelled"},
    "completed": {"verified"},  # G23-3b: independent verification mark
    "failed": set(),
    "cancelled": set(),
    "verified": set(),
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


class StorageError(Exception):
    """Persistence-layer misuse or stale atomic transition."""


class Database:
    def __init__(self, path: str | PathLike[str]) -> None:
        self.path = str(path)
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def migrate(self) -> int:
        """Apply pending migrations in transactions; returns latest version."""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {
            row["version"] for row in self.conn.execute("SELECT version FROM schema_migrations")
        }
        for version in sorted(MIGRATIONS):
            if version in applied:
                continue
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in MIGRATIONS[version]:
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, _now()),
                )
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return max(MIGRATIONS)

    def backup(self, dest_path: str) -> None:
        with sqlite3.connect(dest_path) as dest:
            self.conn.backup(dest)

    @staticmethod
    def restore_from(backup_path: str, target_path: str) -> None:
        import shutil

        shutil.copyfile(backup_path, target_path)

    def close(self) -> None:
        self.conn.close()
