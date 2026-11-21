"""G27 onboarding route: the CHO registers/binds/activates a harness employee
over HTTP — the org-side half of harness onboarding (presence is the other half)."""

import contextlib

import anyio
import httpx

from pixel_startup.config import Settings
from pixel_startup.main import create_app

AUTH = {"Authorization": "Bearer g27-token"}

ONBOARD = {
    "employee_id": "hermes.default",
    "display_name": "Hermes",
    "harness": "hermes",
    "native_agent_id": "agent.hermes.default.001",
    "harness_instance_id": "gw-local",
}


@contextlib.asynccontextmanager
async def client(tmp_path):
    app = create_app(
        Settings(
            pixel_data_dir=str(tmp_path / "data"),
            dashboard_password="pw-g27",
            pixel_api_token="g27-token",
            pixel_session_secret="secret-g27",
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            yield c


def test_onboard_registers_binds_activates(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as c:
            r = await c.post("/api/v1/office/onboard", headers=AUTH, json=ONBOARD)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["employee"]["lifecycle"] == "available"
            assert body["employee"]["binding_verified"] is True
            assert body["presence"]["online"] is None or isinstance(
                body["presence"]["online"], bool
            )
            # visible on the sanitized office state read model
            roster = (await c.get("/api/v1/office/state")).json()["employees"]
            assert any(e["employee_id"] == "hermes.default" for e in roster)

            # same CHO may re-onboard to refresh the binding (200, rebind);
            # the impersonation guard: a DIFFERENT employee can't claim the
            # same native identity (unique index) → 409
            r2 = await c.post(
                "/api/v1/office/onboard",
                headers=AUTH,
                json={**ONBOARD, "employee_id": "imposter.hermes"},
            )
            assert r2.status_code == 409, r2.text

    anyio.run(drive)


def test_onboard_rejects_unknown_harness_and_anon(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as c:
            r = await c.post(
                "/api/v1/office/onboard", headers=AUTH, json={**ONBOARD, "harness": "gpt"}
            )
            assert r.status_code == 400
            assert (await c.post("/api/v1/office/onboard", json=ONBOARD)).status_code == 401

    anyio.run(drive)
