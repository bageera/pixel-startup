"""G33 — Office of Requirement: the office materializes from organization truth.

Three layers, as specified:

1. Organizational topology  — CHO, registered/connected harnesses, workers with
   REAL assignments (task rows or working/waiting lifecycle) from the store.
   Existing-but-unassigned staff do NOT mint furniture.
2. Procedural layout        — staged rooms + station slots: deterministic,
   incremental (a new worker appends, never shuffles), capped per room with
   overflow rooms, and floors listed when the visible grid is exhausted.
3. Theme interpretation     — cosmetic naming + environment asset per theme;
   the same topology renders as garage bench rows, basement consoles or
   firehouse equipment bays.

Zero LLM; the output is pure JSON (the browser renders rooms through surfaces
it already has: environment background, movement tiles, scene station chips).
"""

from __future__ import annotations

import hashlib

STATIONS_PER_ROOM = 8
GRID_W = 20
GRID_H = 24

THEMES: dict[str, dict[str, str]] = {
    # ponytail: environment assets reuse the existing G22 catalog; dedicated
    # G33 art (benches vs consoles vs bays) is an art pass, not a layout change.
    "garage": {
        "environment": "garage",
        "cho_room": "Founder's Garage Office",
        "unassigned_room": "Open Bench Row",
        "harness_room": "Harness Workshop",
        "assigned_station": "Assigned workstation",
    },
    "subbasement": {
        "environment": "mechanic-shop",
        "cho_room": "Sub-Basement Command Bench",
        "unassigned_room": "Scrap Console Bank",
        "harness_room": "Experimental Station",
        "assigned_station": "Assigned console",
    },
    "firehouse": {
        "environment": "continental",
        "cho_room": "Firehouse Command Office",
        "unassigned_room": "Recruit Bunk Benches",
        "harness_room": "Equipment Bay",
        "assigned_station": "Investigation desk",
    },
}
DEFAULT_THEME = "garage"

ASSIGNED_LIFECYCLE = ("working", "waiting")  # an actual assignment, not mere existence


def layout_key(topology: dict) -> str:
    """Stable structural identity over the REAL org facts of the topology."""
    facts = {
        "cho": topology.get("cho"),
        "harnesses": sorted((h["harness"], h["online"]) for h in topology.get("harnesses", [])),
        "workers": sorted(
            (w["worker"], w["harness"], w["assigned"], w["online"])
            for w in topology.get("workers", [])
        ),
    }
    return "g33:" + hashlib.md5(repr(facts).encode()).hexdigest()[:16]


def build_topology(
    employees: list[dict],
    harness_states: dict[str, bool],
    registered_harnesses: list[str],
    assigned_ids: set[str] | None = None,
    cho_name: str = "Ghostface",
) -> dict:
    """Layer 1 — org facts only; nothing visual decides membership here."""
    assigned_ids = assigned_ids or set()
    workers: list[dict] = []
    for e in sorted(
        employees, key=lambda r: (r.get("updated_at") or "", r.get("display_name") or "")
    ):
        name = e.get("display_name") or ""
        harness = (e.get("harness") or "none").lower()
        assigned = name in assigned_ids or (e.get("lifecycle") or "") in ASSIGNED_LIFECYCLE
        workers.append(
            {
                "worker": name,
                "harness": harness,
                "assigned": bool(assigned),
                "online": (bool(harness_states.get(harness, False)) if harness != "none" else True),
            }
        )
    harnesses: list[str] = [h for h in registered_harnesses if h and h != "none"]
    for w in workers:
        if w["harness"] != "none" and w["harness"] not in harnesses:
            harnesses.append(w["harness"])
    return {
        "cho": cho_name,
        "harnesses": [
            {"harness": h, "online": bool(harness_states.get(h, False))} for h in harnesses
        ],
        "workers": workers,
    }


def _claim(free: set[tuple[int, int]], w: int, h: int) -> list[tuple[int, int]]:
    """Deterministic row-major packing; claims w*h cells or returns []."""
    for y in range(GRID_H):
        for x in range(GRID_W):
            cells = [
                (cx, cy)
                for cy in range(y, min(y + h, GRID_H))
                for cx in range(x, min(x + w, GRID_W))
            ]
            if len(cells) == w * h and all(c in free for c in cells):
                for c in cells:
                    free.discard(c)
                return cells
    return []


def _tiles_for(free: set[tuple[int, int]], station_count: int) -> list[dict]:
    cells = _claim(free, 3, 2) if station_count > 2 else _claim(free, 2, 2)
    return [{"x": x, "y": y} for (x, y) in cells]


def build_layout(
    topology: dict,
    theme: str,
    free_tiles: list[tuple[int, int]] | None = None,
) -> dict:
    """Layers 2+3. Rooms keep first-seen order across regenerations (stable
    keys, station order stable by (assigned, created) so additions append)."""
    label = THEMES.get(theme, THEMES[DEFAULT_THEME])
    free: set[tuple[int, int]] = set(free_tiles or [])

    # room accumulation (first-seen order), stations appended in stage order
    rooms: list[dict] = []

    def room(key: str) -> dict:
        for r in rooms:
            if r["key"] == key:
                return r
        r = {"key": key, "name": None, "stations": [], "tiles": None}
        rooms.append(r)
        return r

    # --- Stage 1: CHO office + one station per harness (registered ≠ running)
    cho = room("cho-office")
    cho["name"] = label["cho_room"]
    cho["stations"].append(
        {
            "station_id": "station-cho",
            "label": topology["cho"],
            "occupant": topology["cho"],
            "harness": None,
            "state": "online",
            "personal": True,
        }
    )
    for h in topology.get("harnesses", []):
        hr = room(f"harness-{h['harness']}")
        hr["stations"].append(
            {
                "station_id": f"station-harness-{h['harness']}",
                "label": f"harness {h['harness']}",
                "occupant": None,
                "harness": h["harness"],
                "state": "online" if h["online"] else "offline",  # registered ≠ running
                "personal": False,
            }
        )

    # --- Stage 2: assigned workers get real stations in their harness bay
    assigned = [
        w for w in topology.get("workers", []) if w["assigned"] and w["worker"] != topology["cho"]
    ]
    for w in assigned:
        key = f"harness-{w['harness']}" if w["harness"] != "none" else "assigned-open"
        room(key)["stations"].append(
            {
                "station_id": f"station-worker-{w['worker']}",
                "label": f"{label['assigned_station']} · {w['worker']}",
                "occupant": w["worker"],
                "harness": w["harness"],
                "state": "online" if w["online"] else "offline",
                "personal": True,
            }
        )

    # --- Unassigned staff: communal benches (presence ≠ furniture)
    communal = [
        w
        for w in topology.get("workers", [])
        if not w["assigned"] and w["worker"] != topology["cho"]
    ]
    if communal:
        cr = room("unassigned")
        cr["name"] = label["unassigned_room"]
        for w in communal:
            cr["stations"].append(
                {
                    "station_id": f"station-worker-{w['worker']}",
                    "label": w["worker"],
                    "occupant": w["worker"],
                    "harness": w["harness"],
                    "state": "idle",
                    "personal": False,
                }
            )

    # --- Cap + overflow (incremental: overflow rooms append in first-seen order)
    out: list[dict] = []
    for r in rooms:
        chunks = [
            r["stations"][i : i + STATIONS_PER_ROOM]
            for i in range(0, len(r["stations"]), STATIONS_PER_ROOM)
        ]
        for i, chunk in enumerate(chunks):
            tiles = _tiles_for(free, len(chunk))
            out.append(
                {
                    "room_id": r["key"] if i == 0 else f"{r['key']}-{i + 1}",
                    "name": r["name"]
                    or label["harness_room"]
                    + (f" · {r['key'][8:]}" if r["key"].startswith("harness-") else ""),
                    "floor": 1 if tiles else 2,
                    "tiles": tiles,
                    "stations": chunk,
                }
            )
    stage = (
        3 if any(len(r["stations"]) >= STATIONS_PER_ROOM for r in out) else (2 if assigned else 1)
    )
    return {
        "layout_key": layout_key(topology),
        "theme": theme if theme in THEMES else DEFAULT_THEME,
        "environment": label["environment"],
        "stage": stage,
        "floors": max((r["floor"] for r in out), default=1),
        "rooms": out,
    }
