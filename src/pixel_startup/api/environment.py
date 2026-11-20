"""G22 environment configuration API: GET/PUT /api/environment.

Manifest-catalog validation is data-driven (reading assets/environments), so
additional environment packs require NO backend change. Values persist in the
`app_settings` table (schema v3) and write through to data/pixel-config.json so
the deployed JS baseline renders the same selection.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from pixel_startup.config import Settings
from pixel_startup.persistence.db import Database
from pixel_startup.persistence.store import SettingsStore
from pixel_startup.security.auth import require_auth

router = APIRouter()

_ENVIRONMENTS_DIR = Path(__file__).resolve().parents[3] / "assets" / "environments"
DEFAULT_ENVIRONMENT = "continental"
_VALID_IDS: set[str] | None = None  # populated from disk on first request


def catalog_ids() -> set[str]:
    global _VALID_IDS
    if _VALID_IDS is None:
        _VALID_IDS = set()
        if _ENVIRONMENTS_DIR.is_dir():
            for manifest in _ENVIRONMENTS_DIR.glob("*.manifest.json"):
                try:
                    data = json.loads(manifest.read_text())
                    if isinstance(data.get("id"), str):
                        _VALID_IDS.add(data["id"])
                except (OSError, ValueError):
                    continue  # broken pack fails gracefully
    return _VALID_IDS


def settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def store_from_app(request: Request) -> SettingsStore:
    db: Database = request.app.state.container.providers["db"]
    return request.app.state.container.providers.setdefault("settings", SettingsStore(db))


def pixel_config_path(settings: Settings) -> Path:
    return Path(settings.pixel_data_dir) / "pixel-config.json"


def read_environment(store: SettingsStore) -> str:
    value = store.get("environment")
    return value or DEFAULT_ENVIRONMENT


@router.get("/api/environment")
async def get_environment(request: Request) -> dict:
    store = store_from_app(request)
    return {"environment": read_environment(store), "available": sorted(catalog_ids())}


@router.put("/api/environment")
async def put_environment(request: Request, auth_method: str = Depends(require_auth)) -> dict:
    body = await request.json()
    env_id = body.get("environment") if isinstance(body, dict) else None
    if not isinstance(env_id, str) or env_id not in catalog_ids():
        raise HTTPException(status_code=400, detail=f"unknown environment: {env_id!r}")
    store = store_from_app(request)
    settings = settings_from_app(request)
    store.set("environment", env_id)
    config_path = pixel_config_path(settings)
    try:
        data = json.loads(config_path.read_text()) if config_path.exists() else {}
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict) and data.get("environment") != env_id:
        data["environment"] = env_id  # write-through for the deployed JS baseline
        config_path.write_text(json.dumps(data, indent=2))
    return {"environment": env_id, "persisted": True}
