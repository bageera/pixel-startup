"""AI Director chat + status — Python port of the last legacy /api/ai/* routes.

Parity contract with node server.js:323-392:
- POST /api/ai/chat {prompt} -> {response, model}; owner session (kiosk flag
  mirrors node's fail-open) because the call spends external inference budget.
- GET /api/ai/status -> {available, url, model, models}.

LLM use here is the SANCTIONED Director chat; rendering/health/scene paths
remain zero-LLM. Provider invocation always goes through the audited provider
registry — never a private fetch.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pixel_startup.api.office_compat import office_from_app, office_write_auth
from pixel_startup.integrations.infra import InfraFailure, InfraUnavailable

router = APIRouter()


class ChatIn(BaseModel):
    prompt: str


def _nav_system_prompt(request: Request) -> str:
    """Server-owned contract the office chat form expects: the client builds the
    same prompt locally but only ever sends {prompt} — node's generic
    'keep responses brief' made every navigation command a JSON-parse failure."""
    from pixel_startup.persistence.store import EmployeeStore

    db = request.app.state.container.providers["db"]
    try:
        chars = [e["display_name"] for e in EmployeeStore(db).all()]
    except Exception:  # noqa: BLE001 - fallback keeps the contract usable
        chars = []
    rooms: list[str] = []
    try:
        for room in office_from_app(request).rooms or []:
            name = room.get("name") if isinstance(room, dict) else None
            if name:
                rooms.append(name)
    except Exception:  # noqa: BLE001
        rooms = []
    return (
        "You are a navigation system for a virtual office.\n"
        "Available characters: [" + ", ".join(chars) + "].\n"
        "Available rooms: [" + ", ".join(rooms) + "].\n"
        "Return ONLY a valid JSON array with the actions for the user's instruction.\n"
        'Format: [{"character": "Name", "room": "Room", "wait": false, "sit": false}]\n'
        'If they say "everyone" or similar, include an individual item per character.\n'
        "If they name a room that is not in the list, pick the closest available room.\n"
        'If they say wait/stay, add "wait": true; if they say sit/chair, add "sit": true.\n'
        "Write NOTHING MORE than raw JSON, without code block markdown."
    )


def _llm_unavailable(exc: Exception) -> JSONResponse:
    detail = str(exc) if isinstance(exc, InfraUnavailable | InfraFailure) else type(exc).__name__
    return JSONResponse({"error": f"LiteLLM API error: {detail}"}, status_code=502)


@router.post("/api/ai/chat")
async def ai_chat(request: Request, _auth: str = Depends(office_write_auth)) -> Any:
    try:
        body = await request.json()
    except ValueError:
        body = None
    prompt = (body.get("prompt") if isinstance(body, dict) else None) or ""
    prompt = prompt.strip()
    if not prompt:
        return JSONResponse({"error": "Missing prompt"}, status_code=400)
    try:
        result = request.app.state.container.registry.invoke_provider(
            "litellm",
            "chat.completions",
            {
                "messages": [
                    {"role": "system", "content": _nav_system_prompt(request)},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2048,
            },
        )
    except (InfraFailure, InfraUnavailable) as exc:
        return _llm_unavailable(exc)
    except Exception as exc:  # noqa: BLE001 - audited by the registry, re-raised here
        return _llm_unavailable(exc)
    return {"response": result.get("content", ""), "model": result.get("model")}


@router.get("/api/ai/status")
async def ai_status(request: Request) -> Any:
    adapter = request.app.state.container.providers.get("infra", {}).get("litellm")
    if adapter is None or not getattr(adapter, "configured", False):
        return JSONResponse({"available": False, "error": "LiteLLM not configured"})
    try:
        from pixel_startup.integrations import infra as infra_mod

        get = infra_mod._GET_CLIENT or infra_mod._make_get_client()  # probe transport
        status_code, data = get(adapter._url, adapter._token, "/v1/models")
    except Exception:  # noqa: BLE001 - status endpoint reports honestly
        return JSONResponse({"available": False, "error": "LiteLLM unreachable"})
    if status_code >= 400:
        return JSONResponse({"available": False, "error": f"LiteLLM API returned {status_code}"})
    models = (data.get("data") if isinstance(data, dict) else []) or []
    settings = request.app.state.settings
    return {
        "available": True,
        "url": getattr(settings, "litellm_url", None),
        "model": getattr(settings, "litellm_model", None),
        "models": [m.get("id") for m in models if isinstance(m, dict)],
    }
