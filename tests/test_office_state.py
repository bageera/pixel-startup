"""G16 office read model: sanitized snapshot, observed-vs-registry provenance."""

import json

import anyio
import httpx

from pixel_startup.config import Settings
from pixel_startup.main import create_app
from pixel_startup.persistence.store import EmployeeStore


def _app(tmp_path, hermes_home=None):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (data / "agents.json").write_text(
        json.dumps(
            [
                {
                    "id": 2,
                    "name": "Charon",
                    "role": "Concierge",
                    "room": "Reception",
                    "color": 2,
                    "x": 320,
                    "y": 236,
                    "state": "idle",
                    "active": True,
                }
            ]
        )
    )
    return create_app(
        Settings(
            pixel_data_dir=str(data),
            pixel_api_token="g16-token",
            pixel_session_secret="sectest",
            pixel_allow_no_password=True,
            hermes_home=str(hermes_home) if hermes_home else None,
        )
    )


def test_office_state_snapshot_shape_and_sanitization(tmp_path, hermes_home_qualified) -> None:
    async def drive() -> None:
        app = _app(tmp_path, hermes_home=hermes_home_qualified)
        async with app.router.lifespan_context(app):
            db = app.state.container.providers["db"]
            # a verified binding + one task + one pending approval for real shapes
            store = EmployeeStore(db)
            store.upsert(
                {
                    "employee_id": "employee.charon",
                    "display_name": "Charon",
                    "sprite_id": "char_2",
                    "harness": "none",
                    "operational_scope": "company",
                    "authorization_context": ["synthetic.report"],
                    "lifecycle": "available",
                    "asserted_id": "employee.charon",
                }
            )
            bound = store.bind("employee.charon", "openclaw", "agent.x", "gw-i")
            store.verify_binding("employee.charon", bound["binding_id"])
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                r = await client.get("/api/v1/office/state")
                body = r.json()
                assert r.status_code == 200
                assert "generated_at" in body and body["activity"] is not None
                charon = next(e for e in body["employees"] if e["employee_id"] == "employee.charon")
                assert charon["lifecycle"] == "available" and charon["binding_verified"] is True
                # sanitization: grants and binding internals never leave the backend
                for employee in body["employees"]:
                    assert "authorization_context" not in employee
                    assert "verified_binding" not in employee
                    assert "asserted_id" not in employee
                for provider in body["providers"]:
                    assert "endpoint" not in provider and "capabilities" not in provider
                # provenance: harness rows carry registry vs observed verdicts
                harness = {o["provider_id"]: o for o in body["observations"]}
                assert harness["hermes_home"]["registry_state"] == "qualified"
                assert harness["openclaw_gateway"]["registry_state"] == "candidate"
                assert harness["openclaw_gateway"]["observed"] is None  # no live gateway: honest
                for o in body["observations"]:
                    assert o["source"] in ("registry", "observed-poll")

    anyio.run(drive)


def test_office_state_counts_reflect_stores(tmp_path) -> None:
    async def drive() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            db = app.state.container.providers["db"]
            db.conn.execute(
                "INSERT INTO tasks (task_id, kind, objective, operational_scope,"
                " requested_by, assigned_employee_id, status, correlation_id, created_at,"
                " updated_at) VALUES ('t1','brief','—','company','owner','employee.charon',"
                " 'running','t1','2026-01-01T00:00:00.000Z','2026-01-01T00:00:00.000Z')"
            )
            db.conn.execute(
                "INSERT INTO approvals (request_id, task_id, requested_by, reason,"
                " operational_scope, status, created_at) VALUES ('ap1','t1','owner','why',"
                " 'company','pending','2026-01-01T00:00:00.000Z')"
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                body = (await client.get("/api/v1/office/state")).json()
                assert body["tasks"]["counts"].get("running") == 1
                assert body["tasks"]["active"][0]["task_id"] == "t1"
                assert "objective" not in body["tasks"]["active"][0]  # task text stays internal
                assert body["approvals"]["pending_tasks"] == 1
                assert body["activity"][0]["actor"] in ("system", "owner")

    anyio.run(drive)


def test_no_auth_required_public_read_model(tmp_path) -> None:
    async def drive() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                assert (await client.get("/api/v1/office/state")).status_code == 200

    anyio.run(drive)
