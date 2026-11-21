"""G15 infra providers: synthetic read-only qualification + registry wiring."""

import anyio
import httpx
import pytest

from pixel_startup.config import Settings
from pixel_startup.integrations.infra import (
    FreshRSSProvider,
    GrafanaProvider,
    InfraFailure,
    InfraUnavailable,
    OdooProvider,
)
from pixel_startup.main import create_app


def test_read_only_operation_and_normalization() -> None:
    calls: list[str] = []

    def ok(method, path):
        calls.append(path)
        if path == "/health":
            return 200, {"ok": True}
        return 200, [{"name": "ACME", "id": 7}]

    odoo = OdooProvider("http://odoo.test", "key", client=ok)
    assert odoo.configured
    result = odoo.invoke("customer.lookup", {"vat": "X"})
    assert result["rows"][0]["name"] == "ACME"
    assert calls == ["/customer/lookup?vat=X"]  # only the requested read happened
    # write-style operation simply does not exist -> refusal
    with pytest.raises(InfraFailure, match="unsupported"):
        odoo.invoke("customer.create", {})


def test_unconfigured_is_blocked_not_fabricated() -> None:
    provider = OdooProvider(None, None)
    with pytest.raises(InfraUnavailable, match="BLOCKED"):
        provider.health()


def test_auth_and_5xx_normalization() -> None:
    with pytest.raises(InfraFailure):
        GrafanaProvider("http://g.test", "t", client=lambda m, p: (403, {})).health()
    with pytest.raises(InfraUnavailable):
        GrafanaProvider("http://g.test", "t", client=lambda m, p: (503, {})).health()
    with pytest.raises(InfraUnavailable, match="malformed"):
        GrafanaProvider("http://g.test", "t", client=lambda m, p: (200, "not-json{")).health()


def test_unreachable_transport() -> None:
    def boom(method, path):
        raise ConnectionError("nope")

    with pytest.raises(InfraUnavailable, match="unreachable"):
        FreshRSSProvider("http://fr.test", "t", client=boom).health()


def test_boot_registration_and_provider_records_growth(tmp_path) -> None:
    async def drive():
        app = create_app(
            Settings(
                pixel_data_dir=str(tmp_path / "data"),
                pixel_api_token="t",
                pixel_session_secret="s",
                pixel_allow_no_password=True,
            )
        )
        async with app.router.lifespan_context(app):
            container = app.state.container
            ids = {p.provider_id for p in container.registry.all()}
            assert {
                "odoo",
                "homeassistant",
                "rundeck",
                "grafana",
                "github",
                "freshrss",
                "litellm",
                "calendar",
            } <= ids
            db = container.providers["db"]
            n = db.conn.execute("SELECT COUNT(*) AS n FROM provider_records").fetchone()["n"]
            assert n == 16  # 8 core + 8 infra rows synced at boot
            office = (await httpx_get_state(app)).json()
            for provider in office["providers"]:
                assert "endpoint" not in provider  # office read-model never leaks endpoints

    anyio.run(drive)


async def httpx_get_state(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        return await client.get("/api/v1/office/state")
