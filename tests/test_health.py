"""Health/readiness + error envelope via httpx ASGI transport (no network).

ASGITransport is async-only (httpx 0.28): all request tests drive AsyncClient
through anyio.run (anyio ships as a FastAPI/starlette dependency).
"""

import anyio
import httpx

from pixel_startup.config import Settings
from pixel_startup.main import create_app


def _app():
    app = create_app(Settings(pixel_api_token="tok-test"))  # type: ignore[call-arg]
    app.state.ready = True
    return app


def test_healthz_public() -> None:
    async def drive() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="http://t"
        ) as c:
            r = await c.get("/healthz")
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}

    anyio.run(drive)


def test_readyz_ready_and_starting() -> None:
    async def drive() -> None:
        app = _app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            assert (await c.get("/readyz")).json() == {"status": "ready"}
            app.state.ready = False
            r = await c.get("/readyz")
            assert r.status_code == 503
            assert r.json()["error"]["code"] == 503

    anyio.run(drive)


def test_error_envelope_on_unknown_route() -> None:
    async def drive() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="http://t"
        ) as c:
            r = await c.get("/api/v1/definitely-not-here")
            # G20: without a configured node upstream, deprecated routes answer
            # the honest 501 deprecation envelope instead of a generic 404.
            assert r.status_code == 501
            assert r.json()["deprecated"] == "node"

    anyio.run(drive)


def test_ping_unauth_is_401_envelope() -> None:
    async def drive() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="http://t"
        ) as c:
            r = await c.get("/api/v1/ping")
            assert r.status_code == 401
            assert r.json()["error"] == {"code": 401, "message": "authentication required"}

    anyio.run(drive)


def test_health_services_rows_are_registry_truth() -> None:
    async def drive() -> None:
        app = _app()
        async with app.router.lifespan_context(app):  # boot registration happens in lifespan
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                unauth = await c.get("/api/v1/health/services")
                assert unauth.status_code == 401
                r = await c.get(
                    "/api/v1/health/services", headers={"Authorization": "Bearer tok-test"}
                )
                assert r.status_code == 200
                rows = {row["id"]: row for row in r.json()["services"]}
                # every row carries the six connectivity facts
                assert all(
                    {"id", "category", "state", "configured", "connected", "observed"} == set(row)
                    for row in rows.values()
                )
                # comms has no live transport at boot (B3): never marked connected
                mm = rows["mattermost"]
                assert mm["configured"] is False and mm["connected"] is False
                assert mm["state"] == "candidate" and mm["observed"] is None
                # observed stays None (probe-less rows) or an honest dict (probed rows)
                assert any(
                    row.get("observed") is None or isinstance(row.get("observed"), dict)
                    for row in rows.values()
                )

    anyio.run(drive)
