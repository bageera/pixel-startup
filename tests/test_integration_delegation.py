"""G14 cross-harness delegation: synthetic qualification of the full protocol."""

import anyio
import httpx
import pytest

from pixel_startup.capabilities.delegation import (
    reconcile_stale,
)
from pixel_startup.capabilities.executor import CapabilityExecutor
from pixel_startup.config import Settings
from pixel_startup.main import create_app
from pixel_startup.persistence.db import Database
from pixel_startup.persistence.store import AuditStore, EmployeeStore
from pixel_startup.providers.registry import (
    ProviderCategory,
    ProviderRegistration,
    ProviderRegistry,
    ProviderState,
)

WORKLOAD = {"synthetic-workload-token": "workload.qa"}


class FakeHermes:
    """Conforms to the G14 dispatch contract (synthetic stand-in for A2A)."""

    def __init__(self, offline=False):
        self.tasks: dict[str, dict] = {}
        self.offline = offline
        self.submissions = 0

    def invoke(self, operation, payload_in: dict | None = None) -> dict:
        payload: dict = payload_in or {}
        if operation == "task.submit":
            self.submissions += 1
            task_id = payload["task_id"]
            self.tasks[task_id] = {"state": "completed", "output": {"ok": 1}}
            return {"supported": True, "result": {"taskId": task_id}}
        if operation == "task.result":
            key = payload.get("task_id") or ""
            rec = self.tasks.get(key) or {"state": "failed"}
            return {"supported": True, "result": rec}
        raise AssertionError(operation)


class UnsupportedHarness:
    def invoke(self, operation, payload=None):
        return {"supported": False, "reason": f"no programmatic {operation} interface"}


def _registry(hermes=None, openclaw=None):
    registry = ProviderRegistry()
    registry.register(
        ProviderRegistration(
            provider_id="openclaw_gateway",
            category=ProviderCategory.AGENT_HARNESS,
            implementation="external",
            handler=openclaw,
            capabilities=["gateway.health", "agent.discovery"],
            priority=10,
        )
    )
    if openclaw is None:
        registry.transition("openclaw_gateway", ProviderState.CANDIDATE)
    registry.register(
        ProviderRegistration(
            provider_id="hermes_home",
            category=ProviderCategory.AGENT_HARNESS,
            implementation="external",
            handler=hermes,
            capabilities=["health", "discovery", "task.submit", "task.result"],
            priority=10,
        )
    )
    registry.transition("hermes_home", ProviderState.CANDIDATE)
    return registry


def _executor(db, registry, hermes=None):
    from pixel_startup.capabilities.delegation import make_delegation_adapter
    from pixel_startup.capabilities.providers import SyntheticCredentialProvider

    _ = hermes
    executor = CapabilityExecutor(
        db=db,
        audit=AuditStore(db),
        credential_providers={"synthetic": SyntheticCredentialProvider()},
        adapters={},
        workload_tokens=dict(WORKLOAD),
    )
    executor.adapters["delegate_task"] = make_delegation_adapter(db, registry, AuditStore(db))
    return executor


def _provision(db, employee_id, harness, grants=("delegate.hermes.task",)):
    store = EmployeeStore(db)
    store.upsert(
        {
            "employee_id": employee_id,
            "display_name": employee_id.split(".")[-1].title(),
            "sprite_id": "char_2",
            "harness": harness,
            "operational_scope": "company",
            "authorization_context": list(grants),
            "lifecycle": "available",
            "asserted_id": employee_id,
        }
    )
    bound = store.bind(employee_id, harness, f"agent.{employee_id}.001", "gw-i")
    store.verify_binding(employee_id, bound["binding_id"])
    return bound["binding_id"]


def _req(binding_id, target, objective="quarterly summary", correlation="c-1", **kw):
    return {
        "workload_token": "synthetic-workload-token",
        "binding_id": binding_id,
        "capability": "delegate.hermes.task",
        "request_key": f"req.{correlation}",
        "payload": {
            "target_employee_id": target,
            "objective": objective,
            "correlation_id": correlation,
        },
        "scope": "company",
        **kw,
    }


# 1+4+7: dispatch paths
def test_owner_dispatch_and_result_delivery(tmp_path) -> None:
    db = Database(tmp_path / "o.sqlite3")
    db.migrate()
    hermes = FakeHermes()
    binding = _provision(db, "employee.tailor", "hermes")
    result = _executor(db, _registry(hermes=hermes)).execute(_req(binding, "employee.tailor"))
    assert result["dispatched"] is True
    tasks = db.conn.execute(
        "SELECT status, requested_by FROM tasks WHERE task_id = ?", (result["task_id"],)
    ).fetchone()
    assert tasks["status"] == "running" and tasks["requested_by"] == "employee.tailor"
    # result retrieval completes the lifecycle and stores the output
    from pixel_startup.capabilities.delegation import make_result_retriever

    retriever = make_result_retriever(db, _registry(hermes=hermes), AuditStore(db))
    delivered = retriever(result["task_id"])
    assert delivered["status"] == "completed" and delivered["result"] == {"ok": 1}
    row = db.conn.execute(
        "SELECT * FROM task_results WHERE task_id = ?", (result["task_id"],)
    ).fetchone()
    assert row is not None and row["status"] == "completed"
    # office activity display carries the delegation (C: activity display)
    outcome_rows = db.conn.execute(
        "SELECT action FROM audit_events WHERE action = 'delegation'"
    ).fetchall()
    assert outcome_rows


def test_charon_openclaw_honestly_cancelled_b1(tmp_path) -> None:
    db = Database(tmp_path / "o2.sqlite3")
    db.migrate()
    binding = _provision(db, "employee.charon", "openclaw")
    result = _executor(db, _registry()).execute(_req(binding, "employee.charon"))
    assert result["dispatched"] is False and result["blocker"] == "B1"
    status = db.conn.execute(
        "SELECT status FROM tasks WHERE task_id = ?", (result["task_id"],)
    ).fetchone()["status"]
    assert status == "cancelled"  # persisted truth, not a fabricated ack


def test_unsupported_hermes_cancelled_b5(tmp_path) -> None:
    db = Database(tmp_path / "o3.sqlite3")
    db.migrate()
    binding = _provision(db, "employee.tailor", "hermes")
    result = _executor(db, _registry(hermes=UnsupportedHarness())).execute(
        _req(binding, "employee.tailor")
    )
    assert result["dispatched"] is False and result["blocker"] == "B5"


def test_unavailable_harness(tmp_path) -> None:
    db = Database(tmp_path / "o4.sqlite3")
    db.migrate()
    binding = _provision(db, "employee.tailor", "hermes")
    # hermes provider registered WITHOUT a handler: honest cancel, no fabrication
    result = _executor(db, _registry()).execute(_req(binding, "employee.tailor"))
    assert result["dispatched"] is False
    # and unknown employee is refused outright
    hermes_registry = _registry(hermes=FakeHermes())
    with pytest.raises(Exception):
        _executor(db, hermes_registry).execute(
            _req(binding, "employee.ghost", correlation="ghost-probe")
        )


def test_authorization_requires_grant_and_spoof_refused(tmp_path) -> None:
    db = Database(tmp_path / "o5.sqlite3")
    db.migrate()
    binding = _provision(db, "employee.tailor", "hermes", grants=[])
    executor = _executor(db, _registry(hermes=FakeHermes()))
    with pytest.raises(Exception):  # capability not in authorization_context
        executor.execute(_req(binding, "employee.tailor"))
    binding2 = _provision(db, "employee.charon", "openclaw")
    with pytest.raises(Exception):  # caller id spoof over tailor's binding blocked upstream
        executor.execute(_req(binding2, "employee.tailor", employee_id="employee.fixer"))


def test_duplicate_submission_suppressed(tmp_path) -> None:
    db = Database(tmp_path / "o6.sqlite3")
    db.migrate()
    hermes = FakeHermes()
    binding = _provision(db, "employee.tailor", "hermes")
    executor = _executor(db, _registry(hermes=hermes))
    first = executor.execute(_req(binding, "employee.tailor", correlation="dup-1"))
    second = executor.execute(_req(binding, "employee.tailor", correlation="dup-1"))
    assert second.get("replayed") is True or second.get("task_id") == first.get("task_id")
    assert hermes.submissions == 1  # dispatch happened exactly once


def test_restart_reconciliation_never_invents_outcomes(tmp_path) -> None:
    db = Database(tmp_path / "o7.sqlite3")
    db.migrate()
    hermes = FakeHermes()
    binding = _provision(db, "employee.tailor", "hermes")
    executor = _executor(db, _registry(hermes=hermes))
    dispatched = executor.execute(_req(binding, "employee.tailor", correlation="r-1"))
    fresh = Database(db.path)  # restart: new handles, same file
    fresh.migrate()
    row = fresh.conn.execute(
        "SELECT status FROM tasks WHERE task_id = ?", (dispatched["task_id"],)
    ).fetchone()
    assert row["status"] == "running"  # persisted across restart
    # unacknowledged dispatch never silently becomes success
    report = reconcile_stale(fresh)
    assert isinstance(report["reconciled"], list)
    running = fresh.conn.execute(
        "SELECT status FROM tasks WHERE task_id = ?", (dispatched["task_id"],)
    ).fetchone()["status"]
    assert running in ("running", "cancelled")  # uncertain until harness answers
    fresh.close()


def test_revoked_runtime_binding_dies_midflight(tmp_path) -> None:
    db = Database(tmp_path / "o8.sqlite3")
    db.migrate()
    hermes = FakeHermes()
    binding = _provision(db, "employee.tailor", "hermes")
    executor = _executor(db, _registry(hermes=hermes))
    executor.execute(_req(binding, "employee.tailor", correlation="rb-1"))
    EmployeeStore(db).revoke_binding("employee.tailor", binding)
    with pytest.raises(Exception):
        executor.execute(_req(binding, "employee.tailor", correlation="rb-2"))


def test_api_route_end_to_end(tmp_path) -> None:
    """Owner session/bearer -> executor -> delegation -> retrieval endpoint."""

    async def drive():
        app = create_app(
            Settings(
                pixel_data_dir=str(tmp_path / "data"),
                pixel_api_token="g14-token",
                pixel_session_secret="sectest",
                pixel_allow_no_password=True,
            )
        )
        async with app.router.lifespan_context(app):
            db = app.state.container.providers["db"]
            registry = _registry(hermes=FakeHermes())
            # transplant the synthetic registry into the running container
            container = app.state.container
            container.providers["executor"].adapters["delegate_task"] = None
            from pixel_startup.capabilities.delegation import (
                make_delegation_adapter,
                make_result_retriever,
            )

            container.providers["executor"].adapters["delegate_task"] = make_delegation_adapter(
                db, registry, container.providers["audit"]
            )
            container.providers["delegation_results"] = make_result_retriever(
                db, registry, container.providers["audit"]
            )
            binding = _provision(db, "employee.tailor", "hermes")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                r = await client.post(
                    "/api/v1/capabilities/execute",
                    headers={"Authorization": "Bearer g14-token"},
                    json={
                        "binding_id": binding,
                        "capability": "delegate.hermes.task",
                        "request_key": "req.api-14",
                        "scope": "company",
                        "payload": {
                            "target_employee_id": "employee.tailor",
                            "objective": "status rollup",
                            "correlation_id": "api14",
                        },
                    },
                )
                assert r.status_code == 200 and r.json()["dispatched"] is True
                task_id = r.json()["task_id"]
                rr = await client.get(
                    f"/api/v1/delegate/{task_id}/result",
                    headers={"Authorization": "Bearer g14-token"},
                )
                assert rr.status_code == 200 and rr.json()["status"] == "completed"
                anon = await client.get(f"/api/v1/delegate/{task_id}/result")
                assert anon.status_code == 401  # fail closed

    anyio.run(drive)


# --- G23-3a: plan → review → dispatch -------------------------------------------


def _litellm_registry(hermes=None, plan_text="1. draft data\n2. confirm\n3. report"):
    from pixel_startup.integrations.infra import LiteLLMProvider

    registry = _registry(hermes=hermes)
    stub = LiteLLMProvider(
        "http://llm.test",
        "tok",
        model="glm-test",
        client=lambda m, p, b: (
            200,
            {"choices": [{"message": {"content": plan_text}}], "model": "glm-test"},
        ),
    )
    registry.register(
        ProviderRegistration(
            provider_id="litellm",
            category=ProviderCategory.MONITORING,
            implementation="external",
            handler=stub,
            capabilities=["health.read", "models.discover", "chat.completions"],
            priority=10,
        )
    )
    return registry


def test_plan_gate_holds_until_owner_decision(tmp_path) -> None:
    from pixel_startup.capabilities.delegation import (
        make_plan_flow,
    )
    from pixel_startup.persistence.store import ApprovalStore

    db = Database(tmp_path / "p1.sqlite3")
    db.migrate()
    binding = _provision(db, "employee.tailor", "hermes")
    executor = _executor(db, _litellm_registry(hermes=FakeHermes()))
    request = _req(binding, "employee.tailor", correlation="plan-1")
    request["payload"]["plan_required"] = True  # G23-3a gate
    request["payload"]["verify"] = True  # G23-3b requested together with the plan gate
    result = executor.execute(request)
    assert result["planning"] is True and result["dispatched"] is False
    row = db.conn.execute(
        "SELECT status, payload FROM tasks WHERE task_id = ?", (result["task_id"],)
    ).fetchone()
    assert row["status"] == "planning"
    approvals = ApprovalStore(db)
    approval = approvals.get(result["approval_request_id"])
    assert approval is not None and approval["status"] == "pending"
    # plan/result text is inert: authorization only ever comes from the owner record
    approvals.decide(result["approval_request_id"], "denied", "owner.session")
    flow = make_plan_flow(db, _litellm_registry(hermes=FakeHermes()), AuditStore(db))
    # stale review of an already-denied plan: reconciled loudly, never dispatched
    late = flow["review"](result["task_id"], "approved", "owner.session")
    assert late["status"] == "cancelled" and late["dispatched"] is False
    status = db.conn.execute(
        "SELECT status FROM tasks WHERE task_id = ?", (result["task_id"],)
    ).fetchone()["status"]
    assert status == "cancelled"


def test_approved_plan_generates_plan_then_dispatches(tmp_path) -> None:
    from pixel_startup.capabilities.delegation import make_plan_flow

    db = Database(tmp_path / "p2.sqlite3")
    db.migrate()
    hermes = FakeHermes()
    binding = _provision(db, "employee.tailor", "hermes")
    executor = _executor(db, _litellm_registry(hermes=hermes))
    request = _req(binding, "employee.tailor", correlation="plan-2")
    request["payload"]["plan_required"] = True
    result = executor.execute(request)
    flow = make_plan_flow(db, _litellm_registry(hermes=hermes), AuditStore(db))
    plan = flow["plan_for"](result["task_id"])
    assert "1." in plan["plan"] and plan["approval_status"] == "pending"
    dispatched = flow["review"](result["task_id"], "approved", "owner.session")
    assert dispatched["dispatched"] is True and dispatched["plan_review"] == "approved"
    status = db.conn.execute(
        "SELECT status FROM tasks WHERE task_id = ?", (result["task_id"],)
    ).fetchone()["status"]
    assert status == "running" and hermes.submissions == 1


# --- G23-3b: verify-before-done (independent re-poll; NEVER a re-dispatch) -------


def test_verification_marks_verified(tmp_path) -> None:
    from pixel_startup.capabilities.delegation import make_result_retriever

    db = Database(tmp_path / "p3.sqlite3")
    db.migrate()
    hermes = FakeHermes()
    binding = _provision(db, "employee.tailor", "hermes")
    request = _req(binding, "employee.tailor", correlation="v-1")
    request["payload"]["verify"] = True
    result = _executor(db, _litellm_registry(hermes=hermes)).execute(request)
    retriever = make_result_retriever(db, _litellm_registry(hermes=hermes), AuditStore(db))
    out = retriever(result["task_id"])
    assert out["status"] == "verified" and out["verification"]["verdict"] == "verified"
    again = retriever(result["task_id"])
    assert again["status"] == "verified" and again["result"] == {"ok": 1}


def test_verification_divergent_is_honest_not_retried(tmp_path) -> None:
    from pixel_startup.capabilities.delegation import make_result_retriever

    class ShiftingHarness(FakeHermes):
        polls = 0

        def invoke(self, operation, payload_in: dict | None = None) -> dict:
            payload = payload_in or {}
            if operation == "task.result":
                ShiftingHarness.polls += 1
                return {
                    "supported": True,
                    "result": {"state": "completed", "output": {"n": ShiftingHarness.polls}},
                }
            return super().invoke(operation, payload)

    db = Database(tmp_path / "p4.sqlite3")
    db.migrate()
    hermes = ShiftingHarness()
    binding = _provision(db, "employee.tailor", "hermes")
    request = _req(binding, "employee.tailor", correlation="v-2")
    request["payload"]["verify"] = True
    result = _executor(db, _litellm_registry(hermes=hermes)).execute(request)
    retriever = make_result_retriever(db, _litellm_registry(hermes=hermes), AuditStore(db))
    out = retriever(result["task_id"])
    # the side effect happened once; the mismatch is recorded, not re-executed
    assert hermes.submissions == 1
    assert out["status"] == "completed" and out["verification"]["verdict"] == "divergent"
    row = db.conn.execute(
        "SELECT status FROM tasks WHERE task_id = ?", (result["task_id"],)
    ).fetchone()["status"]
    assert row == "completed"  # stays completed with an honest unverified verdict


def test_verification_absent_label_keeps_old_semantics(tmp_path) -> None:
    from pixel_startup.capabilities.delegation import make_result_retriever

    db = Database(tmp_path / "p5.sqlite3")
    db.migrate()
    hermes = FakeHermes()
    binding = _provision(db, "employee.tailor", "hermes")
    result = _executor(db, _litellm_registry(hermes=hermes)).execute(
        _req(binding, "employee.tailor", correlation="v-3")
    )
    out = make_result_retriever(db, _litellm_registry(hermes=hermes), AuditStore(db))(
        result["task_id"]
    )
    assert out["status"] == "completed" and out["verification"] is None


def test_delegation_refusal_is_400_not_500(tmp_path) -> None:
    """G36-4: a malformed delegation is a client error — honest 400, not 500."""
    from fastapi.testclient import TestClient

    from pixel_startup.api.capabilities import router
    from pixel_startup.config import Settings
    from pixel_startup.main import create_app
    from pixel_startup.security.auth import create_session_cookie

    app = create_app(
        Settings(
            pixel_data_dir=str(tmp_path / "d"),
            pixel_api_token="t",
            pixel_session_secret="test-session-secret",
            dashboard_password="p",
        )
    )
    app.include_router(router)
    with TestClient(app) as client:
        cookie = create_session_cookie("test-session-secret", ttl_seconds=9999)
        auth = {"pixel_session": cookie}
        # provision the worker through the HTTP surface (same thread as the app)
        onb = client.post(
            "/api/v1/office/onboard",
            cookies=auth,
            json={
                "employee_id": "employee.tailor",
                "display_name": "Tailor",
                "harness": "openclaw",
                "native_agent_id": "oc-tailor",
                "harness_instance_id": "inst-t",
                "grants": ["delegate.openclaw.task"],
            },
        )
        assert onb.status_code == 200
        binding_id = onb.json()["binding_id"]
        # objective missing -> route maps the refusal to a 400 (was a 500)
        r = client.post(
            "/api/v1/capabilities/execute",
            cookies=auth,
            json={
                "binding_id": binding_id,
                "capability": "delegate.openclaw.task",
                "scope": "company",
                "request_key": "rkey-bad-delegation",
                "payload": {"objective": "no target given"},
            },
        )
        assert r.status_code == 400
        assert "malformed delegation" in r.json()["error"]["message"]
