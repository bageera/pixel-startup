"""G29 move-in e2e: boot the real app with a controlled Hermes home and a fake
SigV4 S3 — the office counts as MOVED IN only when the harness and object
storage are live, and the startup wrote its access log to `shared/access-logs/`."""

import contextlib
import json

import anyio
import httpx

from pixel_startup.config import Settings
from pixel_startup.main import create_app

AUTH = {"Authorization": "Bearer g29-token"}


class FakeS3:
    """Minimal S3: HEAD bucket probe, GET/PUT objects; keys stored verbatim."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []

    def request(self, method, path, headers, body=None):
        path = path.split("?")[0]
        if method == "HEAD":
            return (404 if path.count("/") <= 1 else 200), {}, b""

        def _200(payload):
            return 200, {"Content-Type": "application/octet-stream"}, payload

        if method == "GET":
            if path not in self.objects:
                return 404, {}, b"NoSuchKey"
            return _200(self.objects[path])
        if method == "PUT":
            self.objects[path] = body or b""
            self.puts.append(path)
            return 201, {}, b""
        return 400, {}, b""


@contextlib.asynccontextmanager
async def client(fake_s3, tmp_path):
    import pixel_startup.integrations.minio as minio_mod

    real = minio_mod.SigV4Client
    minio_mod.SigV4Client = lambda *a, **k: fake_s3
    try:
        app = create_app(
            Settings(
                pixel_data_dir=str(tmp_path / "data"),
                dashboard_password="pw-g29",
                pixel_api_token="g29-token",
                pixel_session_secret="secret-g29",
                hermes_home=str(tmp_path / ".hermes"),
                minio_endpoint="minio.local:9000",
                minio_bucket="pixel-office",
                minio_access_key="ak",
                minio_secret_key="sk",
            )
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                yield c
    finally:
        minio_mod.SigV4Client = real


def test_moved_in_requires_harness_and_minio(tmp_path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "gateway_state.json").write_text(
        json.dumps({"gateway_state": "running", "pid": 1, "platforms": {}})
    )
    fake = FakeS3()

    async def drive():
        async with client(fake, tmp_path) as c:
            services = (await c.get("/api/v1/health/services", headers=AUTH)).json()["services"]
            state = {r["id"]: r for r in services}
            assert state["minio"]["state"] == "qualified"
            assert state["minio"]["connected"] is True
            assert state["hermes_home"]["connected"] is True  # gateway file + pid liveness

            mv0 = (await c.get("/api/v1/move-in", headers=AUTH)).json()
            assert mv0["checks"]["harness"]["ok"] is True
            assert mv0["checks"]["minio"]["ok"] is True
            # seed the org direction/goals through the editor, then re-read
            cfg = await c.get("/api/v1/config", headers=AUTH)
            payload = cfg.json()
            payload["startup"] = {"direction": "Own the AI office", "goals": ["ship"]}
            save = await c.post("/api/v1/config", headers=AUTH, json=payload)
            assert save.status_code == 200, save.text
            mv = (await c.get("/api/v1/move-in", headers=AUTH)).json()
            assert mv["ready"] is True, mv
            assert mv["checks"]["chief"]["ok"] is True
            # the startup access log landed in the bucket
            assert any("shared/access-logs/" in p for p in fake.puts)

    anyio.run(drive)


def test_move_in_stays_honest_without_minio(tmp_path) -> None:
    async def drive():
        app = create_app(
            Settings(
                pixel_data_dir=str(tmp_path / "data"),
                dashboard_password="pw-g29",
                pixel_api_token="g29-token",
                pixel_session_secret="secret-g29",
            )
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                mv = (await c.get("/api/v1/move-in", headers=AUTH)).json()
                assert mv["checks"]["minio"]["ok"] is False
                assert mv["checks"]["minio"]["detail"].startswith("object storage not connected")
                assert mv["ready"] is False

    anyio.run(drive)
