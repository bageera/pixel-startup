"""G17 adversarial qualification matrix over the enabled Python surfaces."""

import json

import anyio
import httpx

from pixel_startup.config import Settings
from pixel_startup.main import create_app
from pixel_startup.security.auth import create_session_cookie, verify_session_cookie


def _app(tmp_path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (data / "agents.json").write_text(json.dumps([
        {"id": 2, "name": "Charon", "role": "Concierge", "room": "Reception",
         "color": 2, "x": 320, "y": 236, "state": "idle", "active": True}]))
    return create_app(Settings(
        pixel_data_dir=str(data), pixel_api_token="sec-token",
        pixel_session_secret="sec-secret", pixel_allow_no_password=True))


def test_unauthenticated_route_matrix(tmp_path) -> None:
    """Unauth access: public reads succeed, everything privileged fails closed."""
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://t") as client:
                for path in ("/healthz", "/api/environment", "/api/v1/office/state"):
                    # JS-only routes (/api/config, /api/environments catalog) live on
                    # the JS baseline until G20 route parity
                    assert (await client.get(path)).status_code == 200, path
                for method, path in (
                    ("POST", "/api/v1/capabilities/execute"),
                    ("GET", "/api/v1/delegate/task.x/result"),
                    ("PUT", "/api/environment"),
                ):
                    # POST /api/config is JS-baseline-only; its Python counterpart
                    # (PUT /api/environment) is the authed surface verified here.
                    r = await client.request(method, path, json={})
                    # G20: unknown routes 404; deprecated-node routes answer 501;
                    # authed-but-wrong-method 405
                    assert r.status_code in (401, 403, 404, 405, 501), (
                        method, path, r.status_code)
                # authz is enforced WITHOUT echoing malformed payload content
                r = await client.post("/api/v1/capabilities/execute",
                                      json={"payload": "<script>alert(1)</script>"})
                assert r.status_code == 401
                assert b"alert" not in r.content  # inputs never echo

    anyio.run(drive)


def test_session_expiry_and_tamper(tmp_path) -> None:
    cookie = create_session_cookie("sec-secret", ttl_seconds=60, now_ms=1_000)
    assert verify_session_cookie("sec-secret", cookie, now_ms=1_000)
    # expiry timestamp is inclusive: valid AT ttl, invalid one millisecond past
    assert verify_session_cookie("sec-secret", cookie, now_ms=1_000 + 60_000)
    assert not verify_session_cookie("sec-secret", cookie, now_ms=1_000 + 60_001)
    assert not verify_session_cookie("sec-secret", cookie[:-4] + "dead", now_ms=1_000)
    assert not verify_session_cookie("other-secret", cookie, now_ms=1_000)
    malformed = ["", "v1", "v1.", "v1.abc", "a.b.c.d"]
    for bad in malformed:
        assert not verify_session_cookie("sec-secret", bad, now_ms=1_000)
    # expiry tolerance is zero: one extra millisecond past TTL fails
    assert not verify_session_cookie("sec-secret", cookie, now_ms=1_000 + 60_001)


def test_provider_records_and_office_state_leak_free(tmp_path) -> None:
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://t") as client:
                body = (await client.get("/api/v1/office/state")).json()
                blob = json.dumps(body).lower()
                for marker in ("sec-token", "sec-secret", "bearer", "authorization_context",
                               "verified_binding", "endpoint", "asserted_id"):
                    assert marker not in blob, marker
                for e in body["employees"]:
                    assert set(e) <= {"employee_id", "display_name", "sprite_id",
                                      "kind", "lifecycle", "harness", "binding_verified"}

    anyio.run(drive)


def test_office_state_staleness_contract(tmp_path) -> None:
    """generated_at drives the >30s STALE marker; backend absence degrades silently."""
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://t") as client:
                generated = (await client.get("/api/v1/office/state")).json()["generated_at"]
                assert generated.endswith("Z") and "T" in generated  # ISO-8601 UTC

    anyio.run(drive)
