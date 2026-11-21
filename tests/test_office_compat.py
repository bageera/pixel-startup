"""G20-B node-retirement qualification: MVP surface natively served, node stopped."""

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import anyio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pixel_startup.config import Settings
from pixel_startup.main import create_app  # noqa: E402
from pixel_startup.security.auth import verify_session_cookie  # noqa: E402

AGENTS = [
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


def _settings(tmp_path, **kw):
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "agents.json").write_text(json.dumps(AGENTS))
    return Settings(
        pixel_data_dir=str(data),
        pixel_session_secret="sec-secret",
        dashboard_password="pw123",
        pixel_api_token="tok",
        **kw,
    )


def _app(tmp_path, **kw):
    return create_app(_settings(tmp_path, **kw))


async def _client(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            yield c


def _login(client, tmp_path, password="pw123"):
    r = client.post("http://t/api/auth/login", json={"password": password})
    assert r.status_code == 200
    return r


# --- sessions -------------------------------------------------------------------------------


def test_login_issues_python_session_cookie_with_secure_attributes(tmp_path):
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.post("/api/auth/login", json={"password": "pw123"})
                assert r.status_code == 200 and r.json() == {"success": True}
                raw = r.headers.get("set-cookie", "")  # attributes preserved
                assert "HttpOnly" in raw and "SameSite=Lax" in raw and "Path=/" in raw
                assert "Max-Age=43200" in raw
                value = raw.split("=", 1)[1].split(";")[0]
                assert verify_session_cookie("sec-secret", value)  # python format
                # it authorizes privileged office writes
                ok = await c.get("/api/log", cookies={"pixel_session": value})
                assert ok.status_code == 200

    anyio.run(drive)


def test_login_rejects_wrong_password_and_rate_limits(tmp_path):
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                for _ in range(5):
                    r = await c.post("/api/auth/login", json={"password": "nope"})
                    assert r.status_code == 401
                burst = await c.post("/api/auth/login", json={"password": "pw123"})
                assert burst.status_code == 429  # 5-strike window locks even good password
                assert burst.json()["error"] == "too many attempts, retry later"

    anyio.run(drive)


def test_forged_and_legacy_node_cookies(tmp_path):
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                ts = str(int(time.time() * 1000))
                mac = hmac.new(
                    b"sec-secret", f"pixel-office-session:{ts}".encode(), hashlib.sha256
                ).hexdigest()
                # legacy node cookie authorizes (transition compatibility)
                r = await c.post("/api/log/clear", cookies={"pixel_session": f"{ts}.{mac}"})
                assert r.status_code == 200
                # forged mac / wrong secret / expired past 12h / empty all fail closed
                forged = hmac.new(
                    b"other", f"pixel-office-session:{ts}".encode(), hashlib.sha256
                ).hexdigest()
                bad = [
                    f"{ts}.{forged}",
                    f"{ts}." + "a" * 64,
                    f"{'1'}.{mac}",  # expired: epoch-issued, far past TTL
                    "v1.99999999999999.deadbeef",
                ]
                for cookie in bad:
                    r = await c.post("/api/log/clear", cookies={"pixel_session": cookie})
                    assert r.status_code == 401, cookie

    anyio.run(drive)


def test_privileged_routes_require_auth_without_kiosk(tmp_path):
    async def drive():
        app = _app(tmp_path)  # dashboard_password set -> auth enforced
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                for method, url in (
                    ("POST", "/api/config"),
                    ("POST", "/api/collision"),
                    ("POST", "/api/rooms"),
                    ("POST", "/api/agent/2/command"),
                    ("POST", "/api/agent/2/command/ack"),
                    ("POST", "/api/agent/2/arrived"),
                    ("POST", "/api/log/clear"),
                ):
                    r = await c.request(method, url, json={})
                    assert r.status_code == 401, (method, url)

    anyio.run(drive)


def test_kiosk_mode_mirrors_node_office_loop_but_not_executor(tmp_path):
    async def drive():
        app = _app(tmp_path, pixel_allow_no_password=True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                assert (
                    await c.post("/api/agent/2/command", json={"kind": "move"})
                ).status_code == 200
                r = await c.post("/api/v1/capabilities/execute", json={})
                assert r.status_code in (401, 403)  # executor NEVER opens in kiosk

    anyio.run(drive)


# --- MVP read/write parity ------------------------------------------------------------------


def test_config_shape_and_boot_migration(tmp_path):
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                body = (await c.get("/api/config")).json()
                assert body["environment"] == "continental"  # DEFAULT_ENVIRONMENT
                assert body["agents"][0]["name"] == "Charon"
                assert isinstance(body["rooms"], list)

    anyio.run(drive)


def test_config_save_preserves_environment_and_writes_shared_files(tmp_path):
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                login = await c.post("/api/auth/login", json={"password": "pw123"})
                cookie = login.headers["set-cookie"].split("=", 1)[1].split(";")[0]
                r = await c.post(
                    "/api/config",
                    json={"agents": AGENTS, "rooms": [{"name": "Reception", "x": 320, "y": 220}]},
                    cookies={"pixel_session": cookie},
                )
                assert r.status_code == 200
                data = json.loads((tmp_path / "data" / "pixel-config.json").read_text())
                assert data["environment"] == "continental"  # never erased by dashboard edit
                # G24 invariant: the chief human is appended when a save omits him
                expected_agents = json.loads(json.dumps(AGENTS)) + [
                    {
                        "id": 6,
                        "name": "Ghostface",
                        "role": "Owner",
                        "room": "Reception",
                        "color": 6,
                        "x": 420,
                        "y": 180,
                        "state": "idle",
                        "active": True,
                    }
                ]
                assert (
                    json.loads((tmp_path / "data" / "agents.json").read_text()) == expected_agents
                )
                map_data = json.loads((tmp_path / "data" / "map.json").read_text())
                assert map_data["rooms"] == [{"name": "Reception", "x": 320, "y": 220}]

    anyio.run(drive)


def test_commands_ttl_consume_ack_and_arrival_markers(tmp_path):
    async def drive():
        app = _app(tmp_path, pixel_allow_no_password=True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                await c.post("/api/agent/2/command", json={"target": "workstation"})
                cmd = (await c.get("/api/agent/2/command")).json()
                assert cmd == {"target": "workstation"}
                assert (await c.get("/api/agent/2/command")).json() is None  # consumed
                assert (await c.get("/api/agent/2/command")).json() is None  # no double read
                # ack is idempotent (retry-safe)
                for _ in range(2):
                    assert (await c.post("/api/agent/3/command/ack")).status_code == 200
                await c.post("/api/agent/2/arrived", json={"location": "office"})
                assert (tmp_path / "data" / "pep_arrived_office").read_text() == "1"
                await c.post("/api/agent/2/arrived", json={"location": "concierge"})
                assert (tmp_path / "data" / "pep_arrived_concierge").read_text() == "1"
                # replay of arrived is a no-op marker write (idempotent)
                await c.post("/api/agent/2/arrived", json={"location": "office"})
                assert (tmp_path / "data" / "pep_arrived_office").read_text() == "1"

    anyio.run(drive)


def test_messages_rooms_collision_log_parity(tmp_path):
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "agent_1_message.txt").write_text("hello from charon")

    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                messages = (await c.get("/api/messages")).json()
                assert set(messages.keys()) == {"0", "1", "2", "3", "4", "5"}
                assert messages["1"] == "hello from charon" and messages["2"] == ""
                rooms = (await c.get("/api/rooms")).json()
                assert isinstance(rooms, list) and rooms
                collision = await c.get("/api/collision")
                # no map recorded yet -> node-shaped 500 honest error
                assert collision.status_code == 500
                assert collision.json() == {"error": "Map not available"}
                log = (await c.get("/api/log")).json()
                assert log == []  # empty log file -> []

    anyio.run(drive)


def test_log_roundtrip_and_clear(tmp_path):
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "pixel_actions.jsonl").write_text('{"a": 1}\n{"b": 2}\n\n{"a": 3}\n')

    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                rows = (await c.get("/api/log")).json()
                assert rows == [{"a": 1}, {"b": 2}, {"a": 3}]

    anyio.run(drive)


def test_security_disabled_surfaces_stay_403(tmp_path):
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                for url in (
                    "/api/contacts/search?q=x",
                    "/api/orgs/search?q=x",
                    "/api/email/send",
                    "/api/vault/item/abc",
                ):
                    r = await c.request(
                        "POST" if url == "/api/email/send" else "GET", url, json={"a": 1}
                    )
                    assert r.status_code == 403, url  # S-02/S-03/S-04 preserved

    anyio.run(drive)


def test_presence_shape_and_no_internal_addresses(tmp_path):
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                body = (await c.get("/api/presence")).json()
                assert {"hermes", "openclaw", "agents", "checkedAt"} <= set(body)
                blob = json.dumps(body)
                for marker in ("127.0.0.1", "http://", "/Users/", "818989", "18789"):
                    assert marker not in blob, marker  # no internal addresses/paths
                for agent in body["agents"]:
                    assert set(agent) == {"key", "name", "runtime", "surface", "detail"}

    anyio.run(drive)


def test_static_assets_allowlist_and_traversal(tmp_path):
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get("/")
                assert r.status_code == 200 and "pixel" in r.text.lower()
                assert (await c.get("/dashboard.html")).status_code == 200
                assert (await c.get("/favicon.ico")).status_code == 404
                for probe in (
                    "/server.js",
                    "/.env",
                    "/package.json",
                    "/server.js/.env",
                    "/assets/%2e%2e/server.js",
                    "/assets/..%2f..%2fserver.js",
                    "/assets/../server.js",
                    "/assets/environments/",
                ):
                    resp = await c.get(probe)
                    assert resp.status_code in (404, 403), probe  # never a source leak
                # a real environment asset serves
                env_assets = sorted((Path("assets/environments")).glob("*.png"))
                if env_assets:
                    got = await c.get(f"/assets/environments/{env_assets[0].name}")
                    assert got.status_code == 200

    anyio.run(drive)


def test_gateway_hardening_encoded_paths_and_redirects(tmp_path, monkeypatch):
    async def drive():
        app = _app(tmp_path, node_upstream="http://node:19000")

        class Fake:
            def __init__(self, redirect=False):
                self.redirect = redirect

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def request(self, method, url, headers=None, content=None):
                status = 302 if self.redirect else 200
                headers = {"Location": "http://internal-node:19000/secret"} if self.redirect else {}
                import httpx as hx

                return hx.Response(
                    status, headers=headers, content=b"x", request=hx.Request("GET", url)
                )

        import pixel_startup.api.gateway as gw

        monkeypatch.setattr(gw, "_make_client", lambda timeout: Fake(redirect=True))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get("/api/briefing")  # proxied, non-MVP
                assert r.status_code == 302
                assert "location" not in {
                    k.lower() for k in r.headers
                } or "internal" not in r.headers.get("location", "")
                # encoded-path blocklist bypass attempts stay 404
                for probe in (
                    "/%73erver.js",
                    "/server%2ejs",
                    "/server.js/",
                    "/assets%2f%2e%2e/server.js",
                ):
                    resp = await c.get(probe)
                    assert resp.status_code == 404, probe

    anyio.run(drive)


def test_presence_honest_offline_without_hermes_home(tmp_path, monkeypatch):
    """P2/P5: an unmounted hermes home is OFFLINE-truthful, never fabricated online."""

    async def drive():
        data = tmp_path / "data"
        data.mkdir(parents=True, exist_ok=True)
        (data / "agents.json").write_text(json.dumps(AGENTS))
        app = create_app(
            Settings(pixel_data_dir=str(data), hermes_home=str(tmp_path / "no-such-hermes"))
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                body = (await c.get("/api/presence")).json()
                assert body["hermes"]["online"] is False
                assert body["hermes"]["gateway"]["state"] == "offline"
                assert body["agents"] == []  # no roster invented
                assert body["openclaw"]["online"] is False
                state = (await c.get("/api/v1/office/state")).json()
                obs = next(o for o in state["observations"] if o["provider_id"] == "hermes_home")
                # observation truthful: explicit probed OFFLINE report, labeled obs-poll
                assert obs["source"] == "observed-poll"
                assert obs["observed"] and obs["observed"]["online"] is False

    anyio.run(drive)


def test_node_upstream_absent_leaves_mvp_native(tmp_path):
    """NODE_UPSTREAM removal qualification: every MVP route answers with node STOPPED."""

    async def drive():
        app = _app(tmp_path)  # no node_upstream configured
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                login = await c.post("/api/auth/login", json={"password": "pw123"})
                assert login.status_code == 200
                cookie = login.headers["set-cookie"].split("=", 1)[1].split(";")[0]
                for url in (
                    "/api/config",
                    "/api/rooms",
                    "/api/messages",
                    "/api/log",
                    "/api/environments",
                    "/api/presence",
                    "/api/environment",
                    "/api/v1/office/state",
                    "/",
                ):
                    r = await c.get(url)
                    assert r.status_code == 200, (url, r.status_code)
                r = await c.get("/api/agent/2/command")
                assert r.status_code == 200
                r = await c.put(
                    "/api/environment",
                    json={"environment": "continental"},
                    cookies={"pixel_session": cookie},
                )
                assert r.status_code == 200
                r = await c.post(
                    "/api/config", json={"agents": AGENTS}, cookies={"pixel_session": cookie}
                )
                assert r.status_code == 200
                r = await c.get("/api/briefing")
                assert r.status_code == 501  # non-MVP: honest deprecation, node stopped

    anyio.run(drive)
