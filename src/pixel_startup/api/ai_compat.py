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

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pixel_startup.api.office_compat import office_from_app, office_write_auth
from pixel_startup.capabilities.delegation import HARNESS_PROVIDER
from pixel_startup.integrations.infra import InfraFailure, InfraUnavailable
from pixel_startup.persistence.store import EmployeeStore

router = APIRouter()


class ChatIn(BaseModel):
    prompt: str


def _nav_system_prompt(request: Request) -> str:
    """Server-owned contract the office chat form expects: the client builds the
    same prompt locally but only ever sends {prompt} — node's generic
    'keep responses brief' made every navigation command a JSON-parse failure."""
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


def _chief_context(request: Request, harness: str) -> str:
    """Grounded context from ACTUAL state only: harness probe (the adapters'
    G7/G8 documented surfaces) + the office task table (real rows) + roster.
    Nothing invented; a missing table or probe failure reads as its honest state."""
    db = request.app.state.container.providers["db"]
    parts: list[str] = []
    registry = request.app.state.container.registry
    provider = HARNESS_PROVIDER.get(harness)
    reg = registry.get(provider) if provider else None
    probe: dict = {}
    try:
        resp = reg.handler.invoke("gateway.health") if reg and reg.handler else None
        if resp and resp.get("supported"):
            probe = resp.get("result") if isinstance(resp.get("result"), dict) else {}
    except Exception:  # noqa: BLE001 - probe failure is an honest offline fact
        probe = {}
    probe_summary = (
        f"harness online: {bool(probe.get('online'))}"
        f"; gateway: {json.dumps((probe.get('gateway') or {}))[:160]}"
        f"; platforms: {probe.get('platforms') or []}"
        if probe
        else "harness probe unavailable (honest-offline)"
    )
    parts.append(f"CONNECTED HARNESS ({harness}): {probe_summary}")
    task_rows: list[dict] = []
    try:
        cur = db.conn.execute(
            "SELECT task_id, objective, status, assigned_employee_id FROM tasks"
            " ORDER BY updated_at DESC LIMIT 12"
        )
        task_rows = [dict(r) for r in cur.fetchall()]
        counts = {
            s: c for (s, c) in db.conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")
        }
    except Exception:  # noqa: BLE001
        counts = {}
    parts.append(f"TASK COUNTS BY STATUS: {counts or 'no tasks'}")
    if task_rows:
        rows = "; ".join(
            f"{r['task_id']} [{r['status']}] {r['objective'][:80]}"
            f" -> {r['assigned_employee_id'] or 'unassigned'}"
            for r in task_rows
        )
        parts.append(f"RECENT TASKS (real rows, newest first): {rows}")
    try:
        roster = [
            f"{e['display_name']} ({e['lifecycle']}, harness {e['harness']})"
            for e in EmployeeStore(db).all()
        ]
        parts.append("ROSTER: " + "; ".join(roster))
    except Exception:  # noqa: BLE001
        parts.append("ROSTER: unavailable")
    return "\n".join(parts)


_CHANNELS = ("mattermost", "email")  # selectable extras; "office" chat is inherent


def _comms_observation(request: Request) -> dict:
    """Honest per-channel availability from the G13 adapters themselves."""
    providers = request.app.state.container.providers
    return {
        name: bool(
            (adapter := providers.get("comms_" + name)) is not None
            and getattr(adapter, "configured", False)
        )
        for name in _CHANNELS
    }


def _patch_startup(request: Request, patch: dict) -> dict:
    """Owner mutation of startup keys via the full-payload save (wipe guards
    keep agents/rooms verbatim; never a partial body)."""
    office = office_from_app(request)
    payload = office.config_payload()
    payload["startup"] = {**office.startup, **patch}
    return office.save_config(payload)["startup"]


@router.get("/api/v1/chief/channels")
async def chief_channels_get(request: Request, _auth: str = Depends(office_write_auth)) -> Any:
    office = office_from_app(request)
    return {
        "chief_harness": str(office.startup.get("chief_harness", "") or ""),
        "office": True,  # the AI Director chat itself is always available
        "allowed": [c for c in (office.startup.get("chief_channels") or []) if c in _CHANNELS],
        "configured": _comms_observation(request),
    }


@router.post("/api/v1/chief/channels")
async def chief_channels_set(request: Request, _auth: str = Depends(office_write_auth)) -> Any:
    try:
        body = await request.json()
    except ValueError:
        body = None
    raw = (body.get("allowed") if isinstance(body, dict) else None) or []
    allowed = sorted({c for c in raw if c in _CHANNELS})  # unknowns ignored
    startup = _patch_startup(request, {"chief_channels": allowed})
    return {"success": True, "allowed": startup.get("chief_channels", [])}


def _channels_note(request: Request) -> str:
    configured = _comms_observation(request)
    office = office_from_app(request)
    allowed = [c for c in (office.startup.get("chief_channels") or []) if c in _CHANNELS]
    lines = [
        "COMMUNICATION CHANNELS: the office chat (here) is always available.",
    ]
    name = {
        "mattermost": "mattermost (internal)",
        "email": "email (external, delivered only after owner approval)",
    }
    if allowed:
        for ch in allowed:
            state = (
                "configured/live"
                if configured.get(ch)
                else "selected by the owner but NOT configured (unavailable until live creds exist)"
            )
            lines.append(f"- {name[ch]}: {state}")
    else:
        lines.append("- no extra channels enabled: deliver everything in the office chat.")
    lines.append(
        "If asked to communicate through a channel not listed above, or one"
        " that is unconfigured, say so honestly and keep it in the office chat."
    )
    return "\n".join(lines)


_CHIEF_SYSTEM = (
    "You are the Chief of Staff of this virtual office, answering the owner."
    " Work from ONLY the provided data - never invent capabilities, tasks or"
    " results. When asked to EVALUATE: assess what is feasible given the roster"
    " and harness state. When asked to REPORT: summarize the real task rows."
    " If the owner asks you to HANDLE a task: name which employee/harness would"
    " get it and state that dispatch goes through the office delegation plan"
    " with owner approval - do not claim work has started unless the task rows"
    " show it. Keep answers short plain text, no markdown headings."
)


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
    if (body.get("mode") if isinstance(body, dict) else None) == "chief":
        return await _chief_chat(request, prompt)
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


async def _chief_chat(request: Request, prompt: str) -> JSONResponse:
    office = office_from_app(request)
    harness = str(getattr(office, "startup", {}).get("chief_harness", "") or "").lower()
    if harness not in HARNESS_PROVIDER:
        return JSONResponse(
            {
                "error": "no Chief of Staff harness selected (owner sets it in the chat panel)",
            },
            status_code=400,
        )
    try:
        result = request.app.state.container.registry.invoke_provider(
            "litellm",
            "chat.completions",
            {
                "messages": [
                    {"role": "system", "content": _CHIEF_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"{_chief_context(request, harness)}\n{_channels_note(request)}"
                            f"\n\nOWNER: {prompt}"
                        ),
                    },
                ],
                "max_tokens": 2048,
            },
        )
    except (InfraFailure, InfraUnavailable) as exc:
        return _llm_unavailable(exc)
    except Exception as exc:  # noqa: BLE001 - audited by the registry, re-raised here
        return _llm_unavailable(exc)
    return JSONResponse(
        {
            "response": result.get("content", ""),
            "model": result.get("model"),
            "mode": "chief",
            "harness": harness,
        }
    )


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
