"""AI Director chat + status — the last legacy /api/ai/* routes migrated.

Node-parity contract, stubbed transport (no real LLM in tests):
missing prompt 400, unauthenticated 401, LiteLLM down 502, happy path returns
{response, model}, status mirrors /v1/models. Renders stay zero-LLM (that is
asserted by the G30/G32 suites; these tests count the ONLY sanctioned calls).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import anyio
import httpx

from pixel_startup.config import Settings
from pixel_startup.main import create_app

AUTH = {"Authorization": "Bearer ai-token"}


@contextlib.asynccontextmanager
async def client(tmp_path: Path, litellm_calls: list | None = None, get_calls: list | None = None):
    from pixel_startup.integrations import infra

    def fake_post(_url, _token, path, body):
        if litellm_calls is not None:
            litellm_calls.append({"path": path, **body})
        return 200, {"choices": [{"message": {"content": "chat ok"}}], "model": "stub-model"}

    def fake_get(_url, _token, path):
        if get_calls is not None:
            get_calls.append(path)
        return 200, {"data": [{"id": "stub-model"}]}

    real_post, real_get = infra._make_post_client, infra._make_get_client
    infra._make_post_client = lambda: fake_post
    infra._POST_CLIENT = fake_post
    infra._make_get_client = lambda: fake_get
    infra._GET_CLIENT = fake_get
    try:
        app = create_app(
            Settings(
                pixel_data_dir=str(tmp_path / "data"),
                dashboard_password="dash",
                pixel_api_token="ai-token",
                litellm_url="http://stub",
                litellm_token="stub-key",
                litellm_model="stub-model",
            )
        )
        async with app.router.lifespan_context(app):  # wires the provider registry
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                yield c
    finally:
        infra._make_post_client = real_post
        infra._POST_CLIENT = real_post
        infra._make_get_client = real_get
        infra._GET_CLIENT = real_get


def test_g32_ai_chat_happy_path_goes_through_registry():
    async def drive():
        calls: list = []
        async with client(Path(__file__).parent / "tmp-ai", litellm_calls=calls) as c:
            r = await c.post("/api/ai/chat", headers=AUTH, json={"prompt": "Hi"})
            assert r.status_code == 200
            data = r.json()
            assert data["response"] == "chat ok" and data["model"] == "stub-model"
            # the ONE sanctioned LLM path: audited provider invocation, system+user
            assert len(calls) == 1 and calls[0]["path"] == "/v1/chat/completions"
            system = calls[0]["messages"][0]["content"]
            assert calls[0]["messages"][0]["role"] == "system"
            # server-owned nav contract: JSON-only actions so the office UI can parse
            assert "navigation system" in system and "Return ONLY a valid JSON" in system

    anyio.run(drive)


def test_g32_ai_chat_requires_owner_session():
    async def drive():
        async with client(Path(__file__).parent / "tmp-ai") as c:
            r = await c.post("/api/ai/chat", json={"prompt": "Hi"})
            assert r.status_code == 401  # no kiosk flag → node requireAuth parity
            missing = await c.post("/api/ai/chat", headers=AUTH, json={})
            assert missing.status_code in (400, 422)  # prompt is required

    anyio.run(drive)


def test_g32_ai_chat_litellm_down_is_502():
    async def drive():
        from pixel_startup.integrations import infra

        async with client(Path(__file__).parent / "tmp-ai") as c:

            def down_post(_url, _token, _path, _body):
                raise ConnectionError("down")

            infra._make_post_client = lambda: down_post
            infra._POST_CLIENT = down_post
            r = await c.post("/api/ai/chat", headers=AUTH, json={"prompt": "Hi"})
            assert r.status_code == 502
            assert "error" in r.json()

    anyio.run(drive)


def test_g32_ai_status_lists_models():
    async def drive():
        gets: list = []
        async with client(Path(__file__).parent / "tmp-ai", get_calls=gets) as c:
            r = await c.get("/api/ai/status")
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is True
            assert body["models"] == ["stub-model"] and body["model"] == "stub-model"
            assert "/v1/models" in gets  # boot probe may also hit the transport
            # the native route wins BEFORE the gateway catch-all: no 501 passthrough
            assert r.json().get("deprecated") is None

    anyio.run(drive)
