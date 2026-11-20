"""G22 environment API: GET/PUT /api/environment, SQLite persistence, write-through."""

import json
import os

import anyio
import httpx
import pytest

from pixel_startup.config import Settings
from pixel_startup.main import create_app

TOKEN = {"Authorization": "Bearer env-test-token"}


@pytest.fixture
def app(tmp_path):
    built = create_app(
        Settings(
            pixel_data_dir=str(tmp_path / "data"),
            pixel_api_token="env-test-token",
            pixel_session_secret="sectest",
            pixel_allow_no_password=True,
        )
    )
    built.state._tmp = str(tmp_path)
    return built


def test_get_default_environment_is_continental(app) -> None:
    async def drive() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                r = await client.get("/api/environment")
                body = r.json()
                assert r.status_code == 200
                assert body["environment"] == "continental"
                assert "mechanic" in body["available"] and "pirate" in body["available"]
                assert "treehouse" in body["available"]

    anyio.run(drive)


def test_put_requires_auth_and_rejects_unknown(app) -> None:
    async def drive() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                assert (
                    await client.put("/api/environment", json={"environment": "pirate"})
                ).status_code == 401
                r = await client.put(
                    "/api/environment", json={"environment": "narnia"}, headers=TOKEN
                )
                assert r.status_code == 400
                assert (
                    await client.put("/api/environment", json={}, headers=TOKEN)
                ).status_code == 400
                assert (await client.get("/api/environment")).json()["environment"] == "continental"

    anyio.run(drive)


def test_put_persists_and_writes_through_to_js_config(app) -> None:
    async def drive() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                r = await client.put(
                    "/api/environment", json={"environment": "mechanic"}, headers=TOKEN
                )
                assert r.status_code == 200 and r.json() == {
                    "environment": "mechanic",
                    "persisted": True,
                }
                assert (await client.get("/api/environment")).json()["environment"] == "mechanic"
                # write-through: deployed JS baseline reads the same file
                config_path = os.path.join(app.state._tmp, "data", "pixel-config.json")
                data = json.loads(open(config_path).read())
                assert data["environment"] == "mechanic"

    anyio.run(drive)


def test_selection_survives_backend_restart(app) -> None:
    async def drive() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                await client.put(
                    "/api/environment", json={"environment": "treehouse"}, headers=TOKEN
                )
        # a genuinely fresh backend: new container, fresh DB open, same data dir
        fresh = create_app(
            Settings(
                pixel_data_dir=app.state._tmp + "/data",
                pixel_api_token="env-test-token",
                pixel_session_secret="sectest",
                pixel_allow_no_password=True,
            )
        )
        async with fresh.router.lifespan_context(fresh):
            store = fresh.state.container.providers["settings"]
            assert store.get("environment") == "treehouse"

    anyio.run(drive)
