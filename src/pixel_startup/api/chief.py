"""G23 step 2: Chief of Staff perspective (GET /api/v1/chief/advise, owner auth).

Assembles context (public GitHub repo read + the G16 office state snapshot),
asks the LiteLLM provider for a perspective on what to add to the office, and
returns it verbatim. Unavailable sources degrade HONESTLY (sources map names
what was actually read — never fabricated). Request submission is step 3.
"""

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from pixel_startup.api.office_state import office_state
from pixel_startup.integrations.infra import InfraUnavailable
from pixel_startup.security.auth import require_auth

router = APIRouter()

_GITHUB_REPO = "bageera/pixel-startup"
_MAX_TREE_PATHS = 200

_GITHUB_CLIENT: Any = None  # cached live GET transport; tests monkeypatch the factory


def _make_github_client():
    """Module-level factory so tests monkeypatch THIS (never the httpx module)."""
    global _GITHUB_CLIENT
    if _GITHUB_CLIENT is None:
        import httpx

        def _get(url: str) -> tuple[int, str]:
            try:
                resp = httpx.get(url, timeout=15.0, follow_redirects=True)
            except httpx.TimeoutException as exc:
                raise TimeoutError(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise ConnectionError(str(exc)) from exc
            return resp.status_code, resp.text

        _GITHUB_CLIENT = _get
    return _GITHUB_CLIENT


def _github_context() -> dict:
    """Public, token-free repo read (the repo is public). Errors degrade honestly."""
    get = _make_github_client()

    def _parse(url):
        try:
            status, body = get(url)
        except (ConnectionError, TimeoutError, OSError):
            return None
        if status != 200 or not isinstance(body, str):
            return None
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return None

    meta = _parse(f"https://api.github.com/repos/{_GITHUB_REPO}")
    if not isinstance(meta, dict):
        return {"available": False, "note": "github meta unavailable (honest-unavailable)"}
    commits = _parse(f"https://api.github.com/repos/{_GITHUB_REPO}/commits?per_page=10")
    tree = _parse(f"https://api.github.com/repos/{_GITHUB_REPO}/git/trees/main?recursive=1")
    return {
        "available": True,
        "repo_meta": {
            k: meta.get(k)
            for k in ("description", "stargazers_count", "open_issues_count", "pushed_at")
        },
        "recent_commits": [c.get("commit", {}).get("message", "")[:120] for c in commits][:10]
        if isinstance(commits, list)
        else None,
        "paths": [n.get("path") for n in tree["tree"]][:_MAX_TREE_PATHS]
        if isinstance(tree, dict) and isinstance(tree.get("tree"), list)
        else None,
    }


def _invoke_llm(container, messages: list[dict]) -> dict:
    """Thin seam over the G15 provider registry (audited there)."""
    return container.registry.invoke_provider("litellm", "chat.completions", {"messages": messages})


_SYSTEM = (
    "You are the Chief of Staff of the Pixel Startup office (a multi-harness agent"
    " operations platform: live pixel office, employee registry, capability executor,"
    " integrations). You get a snapshot of the repo and the live office state. Give a"
    " SHORT perspective on what can be added or improved: concrete suggestions ranked"
    " by value, one line each, grounded in the provided data only — never invent"
    " capabilities. Plain text, no markdown headings."
)


@router.get("/api/v1/chief/advise")
async def chief_advise(request: Request, auth_method: str = Depends(require_auth)) -> dict:
    container = request.app.state.container
    office = await office_state(request)  # reuse the sanitized G16 snapshot
    repo = _github_context()
    try:
        result = _invoke_llm(
            container,
            [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": f"OFFICE STATE: {office}\n\nREPO CONTEXT: {repo}\n\n"
                    "Give the Chief of Staff perspective: what can be added to this office?",
                },
            ],
        )
    except InfraUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="LLM provider not configured/unavailable"
        ) from exc
    except PermissionError as exc:  # registry: authorization failure is terminal
        raise HTTPException(status_code=503, detail="LLM provider denied the request") from exc
    return {
        "perspective": result.get("content", ""),
        "model": result.get("model"),
        "sources": {
            "office_state": "ok",
            "github": repo.get("available", False),
            "repo_context": repo,
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
