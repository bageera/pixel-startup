"""G20 cut-over gateway tests: python front door, deprecated node upstream."""

import sys
from pathlib import Path

import anyio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeUpstream:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def request(self, method, url, headers=None, content=None):
        self.calls.append((method, url, headers, content))
        return self.responses.pop(0)


def _app(monkeypatch, tmp_path, upstream: str | None = "http://node:19000"):
    from pixel_startup.config import Settings
    from pixel_startup.main import create_app

    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "agents.json").write_text(
        '[{"id":2,"name":"Charon","role":"C","room":"Reception","color":2,'
        '"x":320,"y":236,"state":"idle","active":true}]'
    )
    app = create_app(
        Settings(pixel_data_dir=str(data), pixel_session_secret="sec", node_upstream=upstream)
    )
    return app


def _fake(monkeypatch, responses):
    fake = _FakeUpstream(responses)
    monkeypatch.setattr("pixel_startup.api.gateway._make_client", lambda timeout: fake)
    return fake


def _http_response(status, body=b"ok", headers=None):
    r = httpx.Response(
        status, headers=headers or {}, content=body, request=httpx.Request("GET", "http://node/")
    )
    return r


def test_native_routes_win_over_the_upstream(tmp_path, monkeypatch):
    async def drive():
        app = _app(monkeypatch, tmp_path)
        fake = _fake(monkeypatch, [])  # zero upstream calls if hit natively
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                assert (await c.get("/healthz")).status_code == 200
                state = await c.get("/api/v1/office/state")
                assert state.status_code == 200
                assert (await c.get("/api/config")).status_code == 200  # G20-B native
            assert fake.calls == []  # native routes never proxy

    anyio.run(drive)


def test_proxy_passthrough_and_set_cookie(tmp_path, monkeypatch):
    async def drive():
        app = _app(monkeypatch, tmp_path)
        fake = _fake(
            monkeypatch,
            [
                _http_response(
                    200,
                    b'{"accounts":[]}',
                    {"Set-Cookie": "pixel_session=123.abc", "Content-Type": "application/json"},
                ),
            ],
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get("/api/email/accounts?limit=5")
                assert r.status_code == 200
                assert "pixel_session=123.abc" in r.headers.get("set-cookie", "")
                assert r.headers["content-type"] == "application/json"
        method, url, sent_headers, _ = fake.calls[0]
        assert method == "GET" and url.endswith("/api/email/accounts?limit=5")
        assert "host" not in {k.lower() for k in sent_headers}

    anyio.run(drive)


def test_hardened_node_files_stay_404(tmp_path, monkeypatch):
    async def drive():
        app = _app(monkeypatch, tmp_path)
        fake = _fake(monkeypatch, [])
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                for p in ("/server.js", "/.env", "/package.json", "/server.js/x"):
                    r = await c.get(p)
                    assert r.status_code == 404, p
            assert fake.calls == []

    anyio.run(drive)


def test_no_upstream_honest_501(tmp_path, monkeypatch):
    async def drive():
        app = _app(monkeypatch, tmp_path, upstream=None)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                # G20-B: all MVP routes are native — the honest 501 only covers
                # surfaces that stay node-parity/proxied (non-MVP).
                r = await c.get("/api/briefing")
                assert r.status_code == 501
                assert r.json()["deprecated"] == "node"
                assert (await c.get("/api/presence")).status_code == 200
                assert (await c.get("/api/config")).status_code == 200

    anyio.run(drive)


def test_js_cookie_accepted_by_python_auth(tmp_path, monkeypatch):
    """Node-issued cookie (ts.hmac over 'pixel-office-session:ts') authorizes python."""
    import hashlib
    import hmac as hmac_mod
    import time

    async def drive():
        ts = str(int(time.time() * 1000))  # a REAL fresh node-issued session
        mac = hmac_mod.new(
            b"sec", f"pixel-office-session:{ts}".encode(), hashlib.sha256
        ).hexdigest()
        app = _app(monkeypatch, tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.put(
                    "/api/environment",
                    json={"environment": "continental"},
                    cookies={"pixel_session": f"{ts}.{mac}"},
                )
                assert r.status_code == 200, r.status_code  # node cookie == python session

    anyio.run(drive)


def test_js_cookie_expired_or_forged_rejected(tmp_path, monkeypatch):
    import hashlib
    import hmac as hmac_mod

    from pixel_startup.security.auth import verify_session_cookie

    ts = 1_000
    mac = hmac_mod.new(b"sec", f"pixel-office-session:{ts}".encode(), hashlib.sha256).hexdigest()
    assert verify_session_cookie("sec", f"{ts}.{mac}", now_ms=1_000 + 12 * 3600 * 1000)
    assert not verify_session_cookie(
        "sec", f"{ts}.{mac}", now_ms=1_000 + 12 * 3600 * 1000 + 1
    )  # TTL 12h exact
    forged = hmac_mod.new(b"sec", f"OTHER:{ts}".encode(), hashlib.sha256).hexdigest()
    assert not verify_session_cookie("sec", f"{ts}.{forged}", now_ms=1_000)
    assert not verify_session_cookie("sec", f"{ts}." + "f" * 63, now_ms=1_000)
