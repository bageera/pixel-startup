"""G20-B node-parity surface: the MVP routes served natively by Python.

Byte-compatible reimplementations of the deprecated node handlers, backed by
the SAME data files node used (shared data dir = zero data migration):
pixel-config.json, agents.json, map.json, pixel_actions.jsonl,
agent_<i>_message.txt, pep_arrived_<place> markers. Boot load mirrors node's
agents.json-first branch INCLUDING its one-time migration rules (drop "Off
Duty", ensure Ghostface — server.js lines 239-284).

Sessions: python now ISSUES them (`v1.<expiry>.<hmac>`, HttpOnly SameSite=Lax
Path=/ Max-Age=12h) while legacy node cookies stay verifiable (auth.py).
Proxy-only leftovers (briefing/ai/chat/email/sync/contacts) stay with the node
upstream; the hardened 403 surfaces (S-02/S-03/S-04) are native here.
"""

import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from pixel_startup.config import Settings
from pixel_startup.persistence.store import SettingsStore
from pixel_startup.security.auth import require_auth

router = APIRouter()

COMMAND_TTL_MS = 30_000
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_MS = 15 * 60 * 1000
# Byte-parity with server.js built-in defaults (used only when no data files
# exist yet — the shared data dir normally seeds these anyway).
DEFAULT_AGENTS: list[dict] = [
    {
        "id": 0,
        "name": "Curator",
        "role": "Archivist",
        "room": "Archives",
        "color": 0,
        "x": 320,
        "y": 680,
        "state": "idle",
        "active": True,
    },
    {
        "id": 2,
        "name": "Charon",
        "role": "Concierge",
        "room": "Reception",
        "color": 2,
        "x": 320,
        "y": 220,
        "state": "idle",
        "active": True,
    },
    {
        "id": 3,
        "name": "Wardog",
        "role": "Security Chief",
        "room": "Workstations",
        "color": 3,
        "x": 180,
        "y": 440,
        "state": "idle",
        "active": True,
    },
    {
        "id": 4,
        "name": "Fixer",
        "role": "Maintenance",
        "room": "Workstations",
        "color": 4,
        "x": 320,
        "y": 440,
        "state": "idle",
        "active": True,
    },
    {
        "id": 5,
        "name": "Tailor",
        "role": "Attendant",
        "room": "Workstations",
        "color": 5,
        "x": 460,
        "y": 440,
        "state": "idle",
        "active": True,
    },
    {
        "id": 6,
        "name": "Ghostface",
        "role": "Owner",
        "room": "Reception",
        "color": 6,
        "x": 420,
        "y": 180,
        "state": "idle",
        "active": True,
    },
]
DEFAULT_ROOMS: list[dict] = [
    {"name": "Reception", "x": 320, "y": 220},
    {"name": "Workstations", "x": 320, "y": 440},
    {"name": "Archives", "x": 320, "y": 680},
]


def _j(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _wj(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


class OfficeState:
    """Node-equivalent per-process state: agent command queue + login throttles."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data = Path(settings.pixel_data_dir)
        self.commands: dict[int, tuple[dict, int | None]] = {}
        self.login_failures: dict[str, tuple[int, int]] = {}
        self._boot()

    # --- file names (byte-parity with node) -------------------------------------------------
    def _config_path(self) -> Path:
        return self.data / "pixel-config.json"

    def _agents_path(self) -> Path:
        return self.data / "agents.json"

    def _map_path(self) -> Path:
        return self.data / "map.json"

    def _log_path(self) -> Path:
        return self.data / "pixel_actions.jsonl"

    # --- boot load (mirrors server.js 239-284) -----------------------------------------------
    def _boot(self) -> None:
        stored = _j(self._agents_path())
        legacy = _j(self._config_path())
        agents: list[dict] | None = None
        rooms: list[dict] | None = None
        if isinstance(stored, list) and stored:
            migrated = [a for a in stored if a.get("name") != "Off Duty"]
            if not any(a.get("name") == "Ghostface" for a in migrated):
                ghost = next(a for a in DEFAULT_AGENTS if a["name"] == "Ghostface")
                migrated.append(dict(ghost))
            agents = migrated
            _wj(self._agents_path(), agents)
        elif isinstance(legacy, dict):
            if isinstance(legacy.get("agents"), list):
                agents = legacy["agents"]
            if isinstance(legacy.get("rooms"), list):
                rooms = legacy["rooms"]
            if agents is not None:
                _wj(self._agents_path(), agents)
        if agents is None:
            agents = [dict(a) for a in DEFAULT_AGENTS]
        if rooms is None:
            rooms = [dict(r) for r in DEFAULT_ROOMS]
        self.agents: list[dict] = agents
        map_data = self.load_map() or {}
        stored_rooms = map_data.get("rooms") if isinstance(map_data, dict) else None
        if isinstance(stored_rooms, list) and stored_rooms:
            rooms = stored_rooms
        self.rooms: list[dict] = rooms
        env = (legacy or {}).get("environment") if isinstance(legacy, dict) else None
        if isinstance(env, str) and env.strip():
            self.environment = env.strip()
        else:
            self.environment = environment_default()
        if not self._map_path().exists():
            self.persist_map({"rooms": self.rooms, "collision": (map_data or {}).get("collision")})

    # --- map helpers (mirrors loadMapData/persistMapData) ------------------------------------
    def load_map(self) -> dict | None:
        return _j(self._map_path())

    def persist_map(self, updates: dict) -> dict:
        current = self.load_map() or {}
        merged = {**current, **updates, "updatedAt": _now_iso()}
        _wj(self._map_path(), merged)
        return merged

    # --- config shape (mirrors GET/POST /api/config) ------------------------------------------
    def config_payload(self) -> dict:
        return {"agents": self.agents, "rooms": self.rooms, "environment": self.environment}

    def save_config(self, body: dict) -> dict:
        # node parity: a dropped/invalid array RESETS to the built-in defaults
        agents = body.get("agents") if isinstance(body, dict) else None
        rooms = body.get("rooms") if isinstance(body, dict) else None
        self.agents = [dict(a) for a in DEFAULT_AGENTS] if not isinstance(agents, list) else agents
        self.rooms = [dict(r) for r in DEFAULT_ROOMS] if not isinstance(rooms, list) else rooms
        env = body.get("environment") if isinstance(body, dict) else None
        if not isinstance(env, str) or not env.strip():
            env = self.environment
        self.environment = env
        config = {"agents": self.agents, "rooms": self.rooms, "environment": self.environment}
        _wj(self._config_path(), config)
        _wj(self._agents_path(), self.agents)
        self.persist_map({"rooms": self.rooms})
        return config

    # --- commands (mirrors node in-memory queue incl. 30 s consume-on-read) -------------------
    def post_command(self, agent_id: int, cmd: dict) -> None:
        self.commands[agent_id] = (cmd, int(time.time() * 1000))

    def consume_command(self, agent_id: int) -> dict | None:
        entry = self.commands.get(agent_id)
        now = int(time.time() * 1000)
        if entry and entry[1] and now - entry[1] < COMMAND_TTL_MS:
            del self.commands[agent_id]
            return entry[0]
        if entry:
            del self.commands[agent_id]
        return None

    # --- log (mirrors /api/log: last 50 jsonl rows, ANY bad line -> []) -----------------------
    def read_log(self) -> list:
        try:
            rows = [
                json.loads(line)
                for line in self._log_path().read_text().splitlines()
                if line.strip()
            ]
            return rows[-50:]
        except (OSError, ValueError):
            return []

    def clear_log(self) -> None:
        self._log_path().parent.mkdir(parents=True, exist_ok=True)
        self._log_path().write_text("")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def environment_default() -> str:
    from pixel_startup.api.environment import DEFAULT_ENVIRONMENT

    return DEFAULT_ENVIRONMENT


def office_from_app(request: Request) -> OfficeState:
    return request.app.state.office


# --- auth: node-parity kiosk mirror for OFFICE-UI writes only -----------------------------
#
# node's requireAuth fails open when DASHBOARD_PASSWORD is unset; python mirrors
# that for the office command loop via the EXPLICIT pixel_allow_no_password flag.
# Security-critical surfaces (executor, environment PUT, capabilities) keep the
# strict require_auth — kiosk mode never opens them.


async def office_write_auth(request: Request) -> str:
    settings: Settings = request.app.state.settings
    if settings.pixel_allow_no_password:
        return "kiosk"
    return require_auth(request)


def _cookie(value: str, max_age: int) -> str:
    return f"pixel_session={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}"


def _rate_limited(request: Request, office: OfficeState) -> bool:
    ip = request.client.host if request.client else "unknown"
    now = int(time.time() * 1000)
    count, until = office.login_failures.get(ip, (0, 0))
    return count >= LOGIN_RATE_LIMIT and now < until


def _register_failure(request: Request, office: OfficeState) -> None:
    ip = request.client.host if request.client else "unknown"
    count, _ = office.login_failures.get(ip, (0, 0))
    office.login_failures[ip] = (count + 1, int(time.time() * 1000) + LOGIN_RATE_WINDOW_MS)


@router.post("/api/auth/login")
async def auth_login(request: Request) -> Response:
    office = office_from_app(request)
    settings = office.settings
    if not settings.dashboard_password:  # AUTH_ENABLED == !!DASHBOARD_PASSWORD (node)
        return JSONResponse({"success": True})
    if _rate_limited(request, office):
        return JSONResponse(
            {"success": False, "error": "too many attempts, retry later"}, status_code=429
        )
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    password = body.get("password") if isinstance(body, dict) else None
    if (
        password
        and isinstance(settings.dashboard_password, str)
        and password == settings.dashboard_password
    ):
        office.login_failures.pop(request.client.host if request.client else "unknown", None)
        ttl = 12 * 3600
        secret = settings.pixel_session_secret
        if not secret:
            # fail closed: no secret => a cookie could never verify later
            return JSONResponse(
                {"success": False, "error": "session secret not configured"}, status_code=503
            )
        from pixel_startup.security.auth import create_session_cookie

        cookie_value = create_session_cookie(secret, ttl_seconds=ttl)
        return JSONResponse({"success": True}, headers={"Set-Cookie": _cookie(cookie_value, ttl)})
    _register_failure(request, office)
    return JSONResponse({"success": False}, status_code=401)


@router.get("/api/config")
async def get_config(request: Request) -> dict:
    return office_from_app(request).config_payload()


@router.post("/api/config")
async def post_config(request: Request, _auth: str = Depends(office_write_auth)) -> dict:
    office = office_from_app(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    config = office.save_config(body if isinstance(body, dict) else {})
    return {"success": True, "config": config}


@router.get("/api/collision")
async def get_collision(request: Request):
    data = office_from_app(request).load_map()
    collision = data.get("collision") if isinstance(data, dict) else None
    if not collision:
        return JSONResponse({"error": "Map not available"}, status_code=500)
    return collision


@router.post("/api/collision")
async def post_collision(request: Request, _auth: str = Depends(office_write_auth)) -> dict:
    office = office_from_app(request)
    office.persist_map({"collision": await request.json()})
    return {"success": True}


@router.get("/api/rooms")
async def get_rooms(request: Request) -> list:
    office = office_from_app(request)
    data = office.load_map()
    if isinstance(data, dict) and isinstance(data.get("rooms"), list):
        return data["rooms"]
    return [{"name": r["name"], "x": r["x"], "y": r["y"], "tiles": []} for r in office.rooms]


@router.post("/api/rooms")
async def post_rooms(request: Request, _auth: str = Depends(office_write_auth)) -> dict:
    office = office_from_app(request)
    merged = office.persist_map({"rooms": await request.json()})
    if isinstance(merged.get("rooms"), list):
        office.rooms = merged["rooms"]
    return {"success": True}


@router.get("/api/messages")
async def get_messages(request: Request) -> dict:
    office = office_from_app(request)
    out: dict[str, str] = {}
    for i in range(6):
        try:
            out[str(i)] = (office.data / f"agent_{i}_message.txt").read_text()
        except OSError:
            out[str(i)] = ""
    return out


@router.get("/api/log")
async def get_log(request: Request) -> list:
    return office_from_app(request).read_log()


@router.post("/api/log/clear")
async def post_log_clear(request: Request, _auth: str = Depends(office_write_auth)) -> dict:
    office_from_app(request).clear_log()
    return {"success": True}


@router.get("/api/environments")
async def get_environments() -> list:
    from pathlib import Path as _Path

    from pixel_startup.api.environment import DEFAULT_ENVIRONMENT, catalog_ids

    manifests = sorted(
        (_Path(__file__).resolve().parents[3] / "assets" / "environments").glob("*.manifest.json")
    )
    out = []
    for manifest in manifests:
        try:
            m = json.loads(manifest.read_text())
        except (OSError, ValueError):
            continue
        m_id = m.get("id")
        if m_id in catalog_ids():
            out.append(
                {
                    "id": m_id,
                    "name": m.get("name"),
                    "art_style": m.get("art_style"),
                    "default": m_id == DEFAULT_ENVIRONMENT,
                }
            )
    return out


def _agent_id(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="agent id must be an integer")


@router.get("/api/agent/{agent_id}/command")
async def get_command(agent_id: str, request: Request):
    from fastapi.responses import JSONResponse as _J

    cmd = office_from_app(request).consume_command(_agent_id(agent_id))
    return _J(content=cmd if cmd is not None else None)


@router.post("/api/agent/{agent_id}/command")
async def post_command(
    agent_id: str, request: Request, _auth: str = Depends(office_write_auth)
) -> dict:
    body = await request.json()
    office_from_app(request).post_command(
        _agent_id(agent_id), body if isinstance(body, dict) else {}
    )
    return {"success": True}


@router.post("/api/agent/{agent_id}/command/ack")
async def post_command_ack(
    agent_id: str, request: Request, _auth: str = Depends(office_write_auth)
) -> dict:
    office_from_app(request).commands.pop(_agent_id(agent_id), None)  # idempotent
    return {"success": True}


@router.post("/api/agent/{agent_id}/arrived")
async def post_arrived(
    agent_id: str, request: Request, _auth: str = Depends(office_write_auth)
) -> dict:
    office = office_from_app(request)
    body = await request.json()
    location = body.get("location") if isinstance(body, dict) else None
    arrivals = {"office": "pep_arrived_office", "concierge": "pep_arrived_concierge"}
    marker = arrivals.get(str(location)) if isinstance(location, str) else None
    if marker:
        (office.data / marker).write_text("1")
    return {"success": True}


@router.post("/api/agent/{agent_id}/move")
async def post_move(agent_id: str, request: Request, _auth: str = Depends(office_write_auth)):
    office = office_from_app(request)
    aid = _agent_id(agent_id)
    body = await request.json()
    agent = next((a for a in office.agents if a.get("id") == aid), None)
    if agent is None:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    if not isinstance(body, dict):
        body = {}
    if body.get("x") is not None:
        agent["x"] = body["x"]
    if body.get("y") is not None:
        agent["y"] = body["y"]
    if body.get("state"):
        agent["state"] = body["state"]
    return {"success": True, "agent": agent}


# --- G2 findings ported verbatim (surface stays 403 when node retires) ----------------------


@router.get("/api/contacts/search")
@router.get("/api/orgs/search")
async def contacts_search_disabled() -> JSONResponse:
    return JSONResponse(
        {"error": "disabled pending secure rewrite (security finding S-04)"}, status_code=403
    )


@router.post("/api/email/send")
async def email_send_disabled() -> JSONResponse:
    return JSONResponse(
        {"error": "disabled pending authorized delivery flow (security finding S-03)"},
        status_code=403,
    )


@router.get("/api/vault/item/{item_id}")
async def vault_item_disabled(item_id: str) -> JSONResponse:
    return JSONResponse(
        {"error": "raw credential retrieval disabled (security finding S-02)"}, status_code=403
    )


# --- presence: python-native probes (G7/G8 harnesses) shaped like node integrations.js -------


def _roster(hermes: dict, openclaw: dict) -> list[dict]:
    roster: list[dict] = []
    if hermes.get("gateway", {}).get("state") == "running":
        platforms = [str(p) for p in hermes.get("platforms", [])]
        roster.append(
            {
                "key": "hermes:gateway",
                "name": "Hermes Gateway",
                "runtime": "hermes",
                "surface": "gateway",
                "detail": (
                    f"{hermes['gateway'].get('active_agents', 0)} active agent(s), "
                    f"{len(platforms)} channel(s): {', '.join(platforms) or 'none'}"
                ),
            }
        )
    for session in hermes.get("sessions", []):
        surface = str(session.get("surface") or "")
        if surface.startswith("gateway"):
            continue
        roster.append(
            {
                "key": f"hermes:{surface}:{session.get('session_id')}",
                "name": "Hermes CLI",
                "runtime": "hermes",
                "surface": surface,
                "detail": f"session {session.get('session_id')}",
            }
        )
    if openclaw.get("online"):
        # SECURITY: node leaked the internal gateway url here; python never does.
        models = len(openclaw.get("models", []))
        roster.append(
            {
                "key": "openclaw:gateway",
                "name": "OpenClaw Gateway",
                "runtime": "openclaw",
                "surface": "gateway",
                "detail": (
                    f"{models} model(s)"
                    if openclaw.get("auth") == "ok"
                    else "gateway up, auth rejected"
                ),
            }
        )
    return roster


@router.get("/api/presence")
async def get_presence(request: Request) -> dict:
    container = request.app.state.container

    def _handler(provider_id: str):
        try:
            return container.registry.get(provider_id).handler
        except Exception:
            return None

    hermes: dict
    from pixel_startup.harnesses.hermes import HermesHomeAdapter

    hermes_adapter = _handler("hermes_home")
    if hermes_adapter is None:
        hermes_adapter = HermesHomeAdapter()
    try:  # filesystem probe (the same source files node read)
        hermes = {k: v for k, v in hermes_adapter.probe().items() if k != "home"}
    except Exception:  # probe failures are honest liveness data, never fatal
        hermes = {
            "gateway": {"state": "offline", "pid": None, "active_agents": 0},
            "platforms": [],
            "sessions": [],
        }

    openclaw: dict = {"runtime": "openclaw", "online": False, "auth": "n/a", "models": []}
    from pixel_startup.harnesses.openclaw import OpenClawGatewayAdapter

    openclaw_adapter = _handler("openclaw_gateway")
    if openclaw_adapter is None:
        openclaw_adapter = OpenClawGatewayAdapter()
    try:  # /v1/models probe through the G7 adapter
        probe = openclaw_adapter.probe_models()
        openclaw = {
            "runtime": "openclaw",
            "online": probe.get("online", False),
            "auth": probe.get("auth", "n/a"),
            "models": probe.get("models", []),
        }
    except Exception:
        pass
    return {
        "hermes": hermes,
        "openclaw": openclaw,
        "agents": _roster(hermes, openclaw),
        "checkedAt": _now_iso(),
    }


def ensure_settings_store(request: Request) -> SettingsStore:  # pragma: no cover
    """Kept for parity with the environment router's DI seam (unused here)."""
    return request.app.state.container.providers.setdefault(
        "settings", SettingsStore(request.app.state.container.providers["db"])
    )
