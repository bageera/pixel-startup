"""G23 federation qualification (synthetic, no live harness):

one event processed once, one CHO approval recorded, one dispatch to the
capability owner — and retries never re-dispatch, even after approval.
"""

import anyio
import pytest

from pixel_startup.capabilities.delegation import (
    ApprovalStore,
    DelegationFailure,
    _plan_request_id,
    make_plan_flow,
)
from pixel_startup.capabilities.federation import (
    MANIFEST,
    FederatedCapabilityUnknown,
    make_federation_ingest,
    resolve,
)
from pixel_startup.persistence.db import Database
from pixel_startup.persistence.store import AuditStore, EmployeeStore
from pixel_startup.providers.registry import (
    ProviderCategory,
    ProviderRegistration,
    ProviderRegistry,
    ProviderState,
)


class FakeOpenclaw:
    """Synthetic OpenClaw: owns email/send execution, counts submissions."""

    def __init__(self) -> None:
        self.submissions = 0

    def invoke(self, operation, payload=None) -> dict:
        data = dict(payload or {})
        if operation == "task.submit":
            self.submissions += 1
            return {"supported": True, "result": {"taskId": data["task_id"]}}
        if operation == "gateway.health":
            return {"supported": True, "result": {"ok": True}}
        raise AssertionError(operation)


def _registry(fake: FakeOpenclaw) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        ProviderRegistration(
            provider_id="openclaw_gateway",
            category=ProviderCategory.AGENT_HARNESS,
            implementation="external",
            handler=fake,
            capabilities=["gateway.health", "task.submit", "task.result"],
            priority=10,
        )
    )
    registry.transition("openclaw_gateway", ProviderState.CANDIDATE)
    return registry


def _db(tmp_path) -> Database:
    db = Database(str(tmp_path / "fed.sqlite"))
    db.migrate()
    emp = EmployeeStore(db)
    emp.upsert(
        {
            "employee_id": "charon.openclaw",
            "display_name": "Charon",
            "sprite_id": "char_2",
            "harness": "openclaw",
            "operational_scope": "company",
            "lifecycle": "available",
            "asserted_id": "charon.openclaw",
        }
    )
    return db


EVENT = {
    "event_id": "mail.incident-42",
    "capability": "email.send",
    "summary": "Proposed reply: outage post-mortem to the customer",
}


def _ingest(tmp_path, fake: FakeOpenclaw | None = None):

    fake = fake or FakeOpenclaw()
    db = _db(tmp_path)
    registry = _registry(fake)
    ingest = make_federation_ingest(db, registry, AuditStore(db))
    return db, registry, ingest, fake


def test_manifest_rule_one_capability_one_owner(tmp_path) -> None:
    assert resolve("email.send")["owner"] == "openclaw"
    assert MANIFEST["memory.share"]["owner"] == "pixel"  # chroma write, no second engine
    with pytest.raises(FederatedCapabilityUnknown):
        resolve("email.send.duplicate")  # pixel invents nothing at request time


def test_email_send_once_approval_once(tmp_path) -> None:  # the qualification
    async def drive():
        db, registry, ingest, fake = _ingest(tmp_path)
        plan_flow = make_plan_flow(db, registry, AuditStore(db))
        audit = AuditStore(db)
        _ = audit

        # 1. event arrives -> organizational task held for CHO approval
        first = ingest(dict(EVENT), requested_by="chief.human")
        assert first["dispatched"] is False and first["planning"] is True
        task_id = first["task_id"]
        approval = ApprovalStore(db).get(_plan_request_id(task_id))
        assert approval is not None
        assert approval["status"] == "pending"

        # 2. provider retries the SAME event -> replay, no second approval, no dispatch
        retry = ingest(dict(EVENT), requested_by="chief.human")
        assert retry["replayed"] is True
        approval_after = ApprovalStore(db).get(_plan_request_id(task_id))
        assert approval_after is not None
        assert approval_after["request_id"] == approval["request_id"]
        assert fake.submissions == 0

        # 3. CHO approves exactly once -> ONE dispatch to the owner
        plan_flow["review"](task_id, "approved", "ghostface")
        assert fake.submissions == 1

        # 4. another retry post-approval -> still replayed, still one send
        late = ingest(dict(EVENT), requested_by="chief.human")
        assert late["replayed"] is True
        assert fake.submissions == 1  # one email processed, one reply sent
        audit.count()  # audit trail exists (smoke; rows asserted in delegation tests)

    anyio.run(drive)


def test_ingest_honest_without_bound_employee(tmp_path) -> None:
    db = _db(tmp_path)
    db.conn.execute("DELETE FROM employees")  # nobody on openclaw yet (B1 honest gap)
    ingest = make_federation_ingest(db, _registry(FakeOpenclaw()), AuditStore(db))
    with pytest.raises(DelegationFailure) as excinfo:
        ingest(dict(EVENT))
    assert "onboarding gap" in str(excinfo.value)


def test_routes_over_real_app(tmp_path) -> None:
    import contextlib

    import httpx

    from pixel_startup.config import Settings
    from pixel_startup.main import create_app

    AUTH = {"Authorization": "Bearer fed-route-token"}

    @contextlib.asynccontextmanager
    async def client():
        app = create_app(
            Settings(
                pixel_data_dir=str(tmp_path / "data"),
                dashboard_password="pw-fed",
                pixel_api_token="fed-route-token",
                pixel_session_secret="secret-fed",
            )
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                yield c

    async def drive():
        def msg_of(resp: httpx.Response) -> str:
            body = resp.json()
            return str((body.get("error") or {}).get("message") or body.get("detail") or "")

        async with client() as c:
            manifest = (await c.get("/api/v1/federation/manifest", headers=AUTH)).json()
            assert manifest["manifest"]["email.send"]["owner"] == "openclaw"
            # no employee bound to openclaw in this boot: honest 400, not a fake dispatch
            r = await c.post(
                "/api/v1/federation/events",
                headers=AUTH,
                json={"event_id": "mail.x", "capability": "email.send", "summary": "s"},
            )
            assert r.status_code == 400 and "onboarding gap" in msg_of(r)
            # missing fields → 400
            empty = await c.post("/api/v1/federation/events", headers=AUTH, json={})
            assert empty.status_code == 400
            unknown = await c.post(
                "/api/v1/federation/events",
                headers=AUTH,
                json={"event_id": "e", "capability": "email.send.duplicate", "summary": "s"},
            )
            assert unknown.status_code == 400 and "manifest" in msg_of(unknown)

    anyio.run(drive)
