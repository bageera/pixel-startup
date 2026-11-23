"""G33 — Office of Requirement API: derive the office from organizational truth.

GET  /api/v1/office/layout  — deterministic layout manifest (ETag = layout key;
no regeneration when structure is unchanged). Read: any authenticated session.
POST /api/v1/office/layout/apply — owner materializes the manifest into the
office config (rooms + environment asset). Zero LLM; generation happens on
structural change, never per frame (G32 economics preserved).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from pixel_startup.api.office_compat import office_from_app, office_write_auth, require_auth
from pixel_startup.capabilities.delegation import HARNESS_PROVIDER
from pixel_startup.graph.office_gen import DEFAULT_THEME, THEMES, build_layout, build_topology
from pixel_startup.persistence.store import EmployeeStore
from pixel_startup.providers.registry import ProviderCategory

router = APIRouter()

_WALKABLE = (0, 2, 3)  # floor, door, chair — walls (1) are never claimed


def _harness_states(request: Request) -> dict[str, bool]:
    states: dict[str, bool] = {}
    for harness, provider in HARNESS_PROVIDER.items():
        reg = request.app.state.container.registry.get(provider)
        try:
            resp = (
                reg.handler.invoke("gateway.health")
                if reg is not None and reg.handler is not None
                else None
            )
            states[harness] = bool(
                (resp or {}).get("supported") and ((resp or {}).get("result") or {}).get("online")
            )
        except Exception:  # noqa: BLE001 - offline is the honest fallback
            states[harness] = False
    return states


def _registered_harnesses(request: Request) -> list[str]:
    harnesses: list[str] = []
    for reg in request.app.state.container.registry.all():
        if reg.category is not ProviderCategory.AGENT_HARNESS:
            continue
        for harness, provider in HARNESS_PROVIDER.items():
            if provider == reg.provider_id and harness not in harnesses:
                harnesses.append(harness)
    return harnesses


def _assigned_display_names(request: Request) -> set[str]:
    db = request.app.state.container.providers["db"]
    try:
        rows = db.conn.execute(
            "SELECT DISTINCT assigned_employee_id FROM tasks WHERE assigned_employee_id IS NOT NULL"
        )
        ids = {r[0] for r in rows}
    except Exception:  # noqa: BLE001 - no task table yet = no assignments
        return set()
    employees = EmployeeStore(db).all()
    return {e["display_name"] for e in employees if e["employee_id"] in ids}


def _walkable(request: Request) -> list[tuple[int, int]]:
    """Walkable cells of the live collision map (walls excluded)."""
    data = office_from_app(request).load_map()
    grid = (data or {}).get("collision") if isinstance(data, dict) else None
    if not isinstance(grid, list):
        return []
    return [(x, y) for y, row in enumerate(grid) for x, cell in enumerate(row) if cell in _WALKABLE]


def _manifest(request: Request) -> dict:
    container = request.app.state.container
    db = container.providers["db"]
    office = office_from_app(request)
    theme = str(office.startup.get("office_theme") or DEFAULT_THEME)
    topology = build_topology(
        EmployeeStore(db).all(),
        harness_states=_harness_states(request),
        registered_harnesses=_registered_harnesses(request),
        assigned_ids=_assigned_display_names(request),
    )
    busy: set[tuple[int, int]] = set()
    for room in office.rooms or []:
        for t in (room or {}).get("tiles") or []:
            busy.add((int(t["x"]), int(t["y"])))
    free = [(x, y) for (x, y) in _walkable(request) if (x, y) not in busy]
    manifest = build_layout(topology, theme, free)
    manifest["auto_office"] = bool(office.startup.get("auto_office"))
    return manifest


def _materialized_rooms(manifest: dict) -> list[dict]:
    rooms = []
    for room in manifest["rooms"]:
        if room["floor"] != 1 or not room["tiles"]:
            continue  # floor 2+ is listed in the manifest, rendered on demand
        rooms.append(
            {
                "room_id": room["room_id"],
                "name": room["name"],
                "procedural": True,
                "layout_key": manifest["layout_key"],
                "tiles": room["tiles"],
                "stations": room["stations"],
            }
        )
    return rooms


@router.get("/api/v1/office/layout")
async def office_layout_get(request: Request, _auth: str = Depends(require_auth)) -> Any:
    manifest = _manifest(request)
    etag = f'"{manifest["layout_key"]}"'
    if request.headers.get("if-none-match") == etag:
        return JSONResponse(content=None, status_code=304, headers={"ETag": etag})
    return JSONResponse(manifest, headers={"ETag": etag})


@router.post("/api/v1/office/layout/apply")
async def office_layout_apply(request: Request, _auth: str = Depends(office_write_auth)) -> Any:
    manifest = _manifest(request)
    office = office_from_app(request)
    payload = office.config_payload()
    generated = _materialized_rooms(manifest)
    payload["rooms"] = generated
    payload["startup"] = {
        **office.startup,
        "office_theme": manifest["theme"],
        "auto_office": True,
    }
    payload["environment"] = manifest["environment"]
    config = office.save_config(payload)
    return {
        "success": True,
        "layout_key": manifest["layout_key"],
        "stage": manifest["stage"],
        "environment": config.get("environment"),
        "rooms": [r["name"] for r in generated],
    }


@router.post("/api/v1/office/layout/theme")
async def office_layout_theme(request: Request, _auth: str = Depends(office_write_auth)) -> Any:
    try:
        body = await request.json()
    except ValueError:
        body = None
    theme = (body.get("theme") if isinstance(body, dict) else None) or ""
    if theme not in THEMES:
        return JSONResponse({"error": f"theme must be one of {sorted(THEMES)}"}, status_code=400)
    office = office_from_app(request)
    payload = office.config_payload()
    payload["startup"] = {**office.startup, "office_theme": theme}
    result = office.save_config(payload)
    return {"success": True, "office_theme": theme, "environment": result.get("environment")}
