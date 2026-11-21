"""G26 strangler rung: native /api/v1 office-editor surface (no node proxy hop).

Same OfficeState store as the node-parity compat routes (byte-parity files in
PIXEL_DATA_DIR), with the post-G24 semantics: roster/room wipe guards, startup
direction + goals (no-erase). The dashboard speaks this surface first and falls
back to the legacy compat paths only on 4xx-unsupported/5xx origins.
"""

import importlib.metadata
from typing import Any

from fastapi import APIRouter, Depends, Request

from pixel_startup.api.office_compat import (
    get_collision,
    get_rooms,
    office_from_app,
    office_write_auth,
    post_collision,
    post_rooms,
)

router = APIRouter()

_NAME = "pixel-startup"


@router.get("/api/v1/version")
async def version() -> dict:
    try:
        return {"name": _NAME, "version": importlib.metadata.version(_NAME)}
    except importlib.metadata.PackageNotFoundError:  # source checkouts: pyproject only
        return {"name": _NAME, "version": "0.0.0+local"}


@router.get("/api/v1/auth/check")
async def auth_check(_auth: str = Depends(office_write_auth)) -> dict:
    """Same admin session the office owner login creates — dashboard auto-unlock."""
    return {"success": True}


@router.get("/api/v1/config")
async def get_config(request: Request) -> dict:
    return office_from_app(request).config_payload()


@router.post("/api/v1/config")
async def post_config(request: Request, _auth: str = Depends(office_write_auth)) -> dict:
    office = office_from_app(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    config = office.save_config(body if isinstance(body, dict) else {})
    return {"success": True, "config": config}


@router.get("/api/v1/collision")
async def get_collision_v1(request: Request) -> Any:
    # identical payload shape as the compat route (dashboard drops straight in)
    return await get_collision(request)


@router.post("/api/v1/collision")
async def post_collision_v1(request: Request, _auth: str = Depends(office_write_auth)) -> dict:
    return await post_collision(request)


@router.get("/api/v1/rooms")
async def get_rooms_v1(request: Request) -> list:
    # byte-parity with the compat route (prefers map.json rooms, fallback in-memory)
    return await get_rooms(request)


@router.post("/api/v1/rooms")
async def post_rooms_v1(request: Request, _auth: str = Depends(office_write_auth)) -> dict:
    return await post_rooms(request)


@router.get("/api/v1/move-in")
async def move_in_v1(request: Request, _auth: str = Depends(office_write_auth)) -> dict:
    office = office_from_app(request)
    chief = next((a for a in office.agents if a.get("name") == "Ghostface"), None)
    chief_ok = chief is not None and chief.get("active") is not False
    direction = str(office.startup.get("direction") or "").strip()
    goals = [g for g in (office.startup.get("goals") or []) if str(g).strip()]
    return {
        "ready": chief_ok and bool(direction) and bool(goals),
        "checks": {
            "chief": {
                "ok": chief_ok,
                "detail": (
                    f"Ghostface ({chief.get('role', 'Owner')}) "
                    + ("present, on duty" if chief_ok else "off duty")
                    if chief
                    else "Ghostface (Owner) missing from roster"
                ),
            },
            "direction": {
                "ok": bool(direction),
                "detail": direction if direction else "startup direction not set",
            },
            "goals": {"ok": bool(goals), "detail": f"{len(goals)} goal(s) on the board"},
        },
    }
