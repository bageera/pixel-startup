"""G15 infra providers: synthetic read-only qualification + registry wiring."""

import anyio
import httpx
import pytest

from pixel_startup.config import Settings
from pixel_startup.integrations import infra
from pixel_startup.integrations.infra import (
    FreshRSSProvider,
    GrafanaProvider,
    InfraFailure,
    InfraUnavailable,
    LiteLLMProvider,
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
            assert n == 17  # 8 core + 8 infra + comms_email_read rows synced at boot
            office = (await httpx_get_state(app)).json()
            for provider in office["providers"]:
                assert "endpoint" not in provider  # office read-model never leaks endpoints

    anyio.run(drive)


async def httpx_get_state(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        return await client.get("/api/v1/office/state")


# --- G23: LiteLLM chat.completions (office chat + Chief of Staff rails) ----------


def test_chat_completions_declared_and_normalized() -> None:
    calls = []

    def ok(method, path, body):
        calls.append((method, path, body["model"]))
        return (
            200,
            {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "model": "glm-test"},
        )

    llm = LiteLLMProvider("http://llm.test", "tok", client=ok, model="glm-fallback")
    out = llm.invoke(
        "chat.completions",
        {"model": "glm-test", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert out == {"content": "ok", "model": "glm-test"}  # content only; token never echoed
    assert calls == [("POST", "/v1/chat/completions", "glm-test")]
    # explicit model wins over the settings-provided default
    llm_fallback = LiteLLMProvider(
        "http://llm.test",
        "tok",
        client=lambda m, p, b: (200, {"choices": [{"message": {"content": "x"}}]}),
        model="glm-fallback",
    )
    out2 = llm_fallback.invoke("chat.completions", {"messages": [{"role": "user", "content": "s"}]})
    assert out2["model"] == "glm-fallback"
    # undeclared ops still refused
    with pytest.raises(InfraFailure, match="unsupported"):
        llm.invoke("chat.finetune", {})


def test_chat_requires_messages() -> None:
    llm = LiteLLMProvider("http://llm.test", "tok", client=lambda m, p, b: (200, {}))
    with pytest.raises(InfraFailure, match="messages"):
        llm.invoke("chat.completions", {"messages": []})
    with pytest.raises(InfraFailure, match="messages"):
        llm.invoke("chat.completions", {"messages": [{"role": "user", "content": 7}]})


def test_chat_auth_5xx_malformed_and_live_transport_factory(monkeypatch) -> None:
    llm = LiteLLMProvider("http://llm.test", "secret-tok", client=lambda m, p, b: (403, {}))
    with pytest.raises(InfraFailure) as exc:
        llm.invoke("chat.completions", {"messages": [{"role": "user", "content": "x"}]})
    assert "secret-tok" not in str(exc.value)  # credential never echoed
    with pytest.raises(InfraUnavailable, match="service error"):
        LiteLLMProvider("http://llm.test", "t", client=lambda m, p, b: (503, {})).invoke(
            "chat.completions", {"messages": [{"role": "user", "content": "x"}]}
        )
    with pytest.raises(InfraUnavailable, match="malformed"):
        LiteLLMProvider("http://llm.test", "t", client=lambda m, p, b: (200, "not-json")).invoke(
            "chat.completions", {"messages": [{"role": "user", "content": "x"}]}
        )
    with pytest.raises(InfraUnavailable, match="malformed"):
        broken = LiteLLMProvider(
            "http://llm.test", "t", client=lambda m, p, b: (200, {"no": "choices"})
        )
        broken.invoke("chat.completions", {"messages": [{"role": "user", "content": "x"}]})

    # live transport (no injected client) routes through the module factory
    def fake_post(url, token, path, body):
        assert token == "live-tok" and path == "/v1/chat/completions"
        return 200, {"choices": [{"message": {"content": "live"}}], "model": "glm-live"}

    monkeypatch.setattr(infra, "_make_post_client", lambda: fake_post)
    live = LiteLLMProvider("http://llm.test", "live-tok")
    assert live.invoke("chat.completions", {"messages": [{"role": "user", "content": "y"}]}) == {
        "content": "live",
        "model": "glm-live",
    }


def test_chat_unconfigured_is_blocked_not_fabricated() -> None:
    unconfigured = LiteLLMProvider(None, None)
    with pytest.raises(InfraUnavailable, match="BLOCKED"):
        unconfigured.invoke("chat.completions", {"messages": [{"role": "user", "content": "x"}]})


def test_litellm_probe_liveliness(monkeypatch):
    """Boot promotion evidence: /health/liveliness 200 -> probe online; failure
    stays honest (InfraUnavailable). Body is plain text, never parsed."""
    from pixel_startup.integrations import infra

    calls = []

    def fake_get(url, token, path):
        calls.append((url, path))
        return 200, "I'm alive!"

    monkeypatch.setattr(infra, "_make_get_client", lambda: fake_get)
    infra._GET_CLIENT = None

    live = infra.LiteLLMProvider("http://llm.test", None, model="m")
    assert live.probe() == {"online": True}
    assert calls == [("http://llm.test", "/health/liveliness")]

    def dead_get(url, token, path):
        raise ConnectionError("refused")

    monkeypatch.setattr(infra, "_make_get_client", lambda: dead_get)
    with pytest.raises(InfraUnavailable, match="unreachable"):
        infra.LiteLLMProvider("http://llm.test", None).probe()
