"""G26 native /api/v1 editor surface: config (G24 guards + startup), move-in,
version, auth/check, rooms/collision roundtrip — on the real lifespan (byte-parity
PIXEL_DATA_DIR files) over httpx ASGI (no network).
"""

import contextlib

import anyio
import httpx

from pixel_startup.config import Settings
from pixel_startup.main import create_app

AUTH = {"Authorization": "Bearer g26-api-token"}


@contextlib.asynccontextmanager
async def client(tmp_path):
    app = create_app(
        Settings(
            pixel_data_dir=str(tmp_path / "data"),
            dashboard_password="pw-g26",
            pixel_api_token="g26-api-token",
            pixel_session_secret="secret-g26",
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            yield c


def test_v1_version_public(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as c:
            r = await c.get("/api/v1/version")
            assert r.status_code == 200
            body = r.json()
            assert body["name"] == "pixel-startup" and body["version"][0].isdigit()

    anyio.run(drive)


def test_v1_authcheck_gates_on_session_or_token(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as c:
            assert (await c.get("/api/v1/auth/check")).status_code == 401
            r = await c.get("/api/v1/auth/check", headers=AUTH)
            assert r.status_code == 200 and r.json() == {"success": True}

    anyio.run(drive)


def test_v1_config_shape_and_startup_roundtrip(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as c:
            r = await c.get("/api/v1/config")
            assert r.status_code == 200
            base = r.json()
            chief = next(a for a in base["agents"] if a["name"] == "Ghostface")
            assert chief["role"] == "Owner"
            assert base["startup"] == {
                "direction": "",
                "goals": [],
                "mood": "focused",
                "chief_harness": "",
            }

            r = await c.post(
                "/api/v1/config",
                headers=AUTH,
                json={
                    "agents": [],
                    "rooms": [],
                    "startup": {"direction": "Own AI-ops", "goals": ["chat live"]},
                },
            )
            assert r.status_code == 200
            cfg = r.json()["config"]
            assert cfg["startup"]["direction"] == "Own AI-ops"
            assert cfg["startup"]["goals"] == ["chat live"]
            # wipe guard fired instead: roster restored with the owner
            assert any(a["name"] == "Ghostface" for a in cfg["agents"])

            mv = (await c.get("/api/v1/move-in", headers=AUTH)).json()
            # G29: ready additionally requires the harness/minio gates; this test
            # pins the organization half only.
            assert mv["checks"]["direction"]["ok"] is True
            assert mv["checks"]["goals"]["ok"] is True
            assert "Ghostface" in mv["checks"]["chief"]["detail"]

    anyio.run(drive)


def test_v1_config_invalid_arrays_reset_and_startup_survives_partial_saves(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as c:
            await c.post(
                "/api/v1/config",
                headers=AUTH,
                json={
                    "agents": [],
                    "rooms": [],
                    "startup": {"direction": "Own AI-ops", "goals": ["chat live"]},
                },
            )
            # partial save, arrays dropped: defaults return, startup no-erase
            r = await c.post("/api/v1/config", headers=AUTH, json={})
            cfg = r.json()["config"]
            assert len(cfg["agents"]) == 6 and any(a["name"] == "Ghostface" for a in cfg["agents"])
            assert cfg["startup"]["direction"] == "Own AI-ops"
            mv = (await c.get("/api/v1/move-in", headers=AUTH)).json()
            assert mv["checks"]["direction"]["ok"] is True
            # full ready additionally needs harness+minio (G29); pinned in test_move_in_e2e

    anyio.run(drive)


def test_v1_movein_honest_before_setup(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as c:
            mv = (await c.get("/api/v1/move-in", headers=AUTH)).json()
            assert mv["ready"] is False
            assert mv["checks"]["chief"]["ok"] is True
            assert mv["checks"]["direction"]["detail"] == "startup direction not set"

    anyio.run(drive)


def test_v1_rooms_and_collision_fresh_roundtrip(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as c:
            r = await c.get("/api/v1/rooms")
            assert r.status_code == 200 and isinstance(r.json(), list)

            rooms = [{"name": "Lobby", "x": 1, "y": 2, "tiles": []}]
            rr = await c.post("/api/v1/rooms", headers=AUTH, json=rooms)
            assert rr.status_code == 200 and rr.json()["success"] is True
            assert (await c.get("/api/v1/rooms")).json() == rooms

            cr = await c.post("/api/v1/collision", headers=AUTH, json=[[1] * 25] * 20)
            assert cr.status_code == 200
            assert (await c.get("/api/v1/collision")).json() == [[1] * 25] * 20

    anyio.run(drive)
