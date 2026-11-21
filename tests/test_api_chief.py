"""G23 chief-advise tests: context assembly, honest degradation, auth, seams."""

import anyio
import httpx

from pixel_startup.api import chief as chief_api
from pixel_startup.config import Settings
from pixel_startup.integrations.infra import InfraUnavailable
from pixel_startup.main import create_app


def _app(tmp_path):
    return create_app(
        Settings(
            pixel_data_dir=str(tmp_path / "data"),
            pixel_api_token="g23-token",
            pixel_session_secret="sectest",
        )
    )


def _fake_github(monkeypatch, fail=False, status=200):
    def fake_factory():
        def get(url):
            if fail:
                raise ConnectionError("github down")
            if "commits" in url:
                return status, '[{"commit": {"message": "feat: x"}}]'
            if "git/trees" in url:
                return status, '{"tree": [{"path": "src/pixel_startup/main.py"}]}'
            meta = (
                '{"description": "Pixel Startup", "stargazers_count": 3,'
                ' "open_issues_count": 2, "pushed_at": "2026-11-21T00:00:00Z"}'
            )
            return status, meta

        return get

    monkeypatch.setattr(chief_api, "_make_github_client", fake_factory)


def test_advise_requires_auth(tmp_path):
    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                assert (await client.get("/api/v1/chief/advise")).status_code == 401

    anyio.run(drive)


def test_advise_assembles_context_and_perspective(tmp_path, monkeypatch):
    _fake_github(monkeypatch)
    seen = {}

    def fake_llm(container, messages):
        seen["messages"] = messages
        return {"content": "1. Add a budget tool (no finance store exists).", "model": "glm-x"}

    monkeypatch.setattr(chief_api, "_invoke_llm", fake_llm)

    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                r = await client.get(
                    "/api/v1/chief/advise", headers={"Authorization": "Bearer g23-token"}
                )
        body = r.json()
        assert r.status_code == 200
        assert body["perspective"].startswith("1.")
        assert body["model"] == "glm-x"
        assert body["sources"]["github"] is True
        assert body["sources"]["repo_context"]["repo_meta"]["description"] == "Pixel Startup"
        assert body["sources"]["office_state"] == "ok"
        # the LLM saw BOTH contexts
        assert "OFFICE STATE" in seen["messages"][1]["content"]
        assert "REPO CONTEXT" in seen["messages"][1]["content"]

    anyio.run(drive)


def test_advise_degrades_honestly_when_github_down(tmp_path, monkeypatch):
    _fake_github(monkeypatch, fail=True)

    def fake_llm(container, messages):
        return {"content": "Partial view perspective.", "model": "glm-x"}

    monkeypatch.setattr(chief_api, "_invoke_llm", fake_llm)

    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                r = await client.get(
                    "/api/v1/chief/advise", headers={"Authorization": "Bearer g23-token"}
                )
        body = r.json()
        assert r.status_code == 200  # advise survives degraded context
        assert body["sources"]["github"] is False
        assert "honest-unavailable" in body["sources"]["repo_context"]["note"]

    anyio.run(drive)


def test_advise_llm_unconfigured_is_503_not_fabricated(tmp_path, monkeypatch):
    _fake_github(monkeypatch)

    def fail_llm(container, messages):
        raise InfraUnavailable("litellm not configured; live rows BLOCKED")

    monkeypatch.setattr(chief_api, "_invoke_llm", fail_llm)

    async def drive():
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as client:
                r = await client.get(
                    "/api/v1/chief/advise", headers={"Authorization": "Bearer g23-token"}
                )
        assert r.status_code == 503  # honest unavailability, never a fabricated read

    anyio.run(drive)
