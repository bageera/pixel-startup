#!/usr/bin/env python3
"""G22 — deterministic isometric environment pack generator + manifest validator.

Draws four original environments (640x800, tile grid 20x25 @ 32px, pseudo-isometric
staggered tiles with top-lit bevels) over the SAME proven walkable topology as the
deployed office, so every agent coordinate, chair behavior and editor stays valid.

Values: 0 walkable, 1 wall/solid, 2 door, 3 chair.

Run: python3 tools/generate-environments.py   (regenerates assets/environments/*)
"""

import json
import re
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "environments"
COLS, ROWS, TILE = 20, 25, 32

def _shared_collision() -> list[list[int]]:
    """Single source of truth: the deployed office's walkable topology, verbatim
    from index.html (default_COLLISION_MAP) — never a hand transcribed copy."""
    html = (Path(__file__).resolve().parent.parent / "index.html").read_text()
    match = re.search(r"let default_COLLISION_MAP = (\[\[.*?\]\]);", html, re.S)
    assert match, "default_COLLISION_MAP not found in index.html"
    grid = json.loads(match.group(1))
    assert len(grid) == ROWS and all(len(r) == COLS for r in grid)
    return grid

SHARED_COLLISION = _shared_collision()

ANCHORS = {"reception": [10, 7], "workstations": [10, 14], "archives": [10, 21]}

ENVS = {
    "continental": {
        "name": "The Continental",
        "art_style": "Art Deco concierge headquarters (original, non-derivative)",
        "palette": {
            "floor_a": (26, 46, 38), "floor_b": (32, 58, 48), "tile_bevel": (64, 110, 92),
            "wall": (18, 26, 24), "wall_trim": (201, 168, 90), "accent": (201, 168, 90),
            "rug": (58, 24, 24), "furniture": (24, 32, 30), "chair": (90, 32, 32),
            "lamp": (232, 214, 148),
        },
    },
    "mechanic": {
        "name": "Backline Mechanic Shop",
        "art_style": "Industrial garage and engineering office (original, non-derivative)",
        "palette": {
            "floor_a": (58, 58, 62), "floor_b": (66, 66, 70), "tile_bevel": (96, 96, 102),
            "wall": (30, 30, 34), "wall_trim": (255, 179, 71), "accent": (255, 179, 71),
            "rug": (40, 44, 48), "furniture": (48, 50, 54), "chair": (22, 24, 26),
            "lamp": (255, 205, 120),
        },
    },
    "pirate": {
        "name": "Inferno Deck",
        "art_style": "Nautical flagship deck (original, non-derivative)",
        "palette": {
            "floor_a": (122, 84, 48), "floor_b": (104, 70, 40), "tile_bevel": (160, 116, 68),
            "wall": (66, 46, 28), "wall_trim": (198, 148, 82), "accent": (64, 175, 168),
            "rug": (54, 96, 92), "furniture": (58, 40, 22), "chair": (44, 30, 16),
            "lamp": (255, 236, 170),
        },
    },
    "treehouse": {
        "name": "Lost Pines Treehouse",
        "art_style": "Woodland canopy headquarters (original, non-derivative)",
        "palette": {
            "floor_a": (98, 76, 50), "floor_b": (86, 66, 44), "tile_bevel": (150, 122, 82),
            "wall": (64, 52, 36), "wall_trim": (136, 178, 96), "accent": (136, 178, 96),
            "rug": (48, 62, 40), "furniture": (70, 54, 34), "chair": (52, 40, 26),
            "lamp": (255, 220, 140),
        },
    },
    "garage": {
        "name": "Silicon Garage",
        "art_style": "Silicon Valley garage startup office (original, non-derivative)",
        "palette": {
            "floor_a": (186, 182, 176), "floor_b": (170, 166, 160), "tile_bevel": (212, 208, 202),
            "wall": (44, 48, 62), "wall_trim": (232, 234, 228), "accent": (255, 96, 120),
            "rug": (56, 62, 92), "furniture": (72, 66, 62), "chair": (38, 46, 62),
            "lamp": (255, 228, 148),
        },
    },
}


def _iso_floor(d: ImageDraw.ImageDraw, env: dict) -> None:
    p = env["palette"]
    for row in range(ROWS):
        for col in range(COLS):
            x0, y0 = col * TILE, row * TILE
            even = (row + col) % 2 == 0
            d.rectangle([x0, y0, x0 + TILE, y0 + TILE], fill=p["floor_a"] if even else p["floor_b"])
            # pseudo-iso: top-lit bevel + bottom shade on every tile
            d.line([x0, y0, x0 + TILE - 1, y0], fill=p["tile_bevel"])
            d.line([x0, y0, x0, y0 + TILE - 1], fill=_mix(p["tile_bevel"], p["wall"], 0.35))
            d.line([x0, y0 + TILE - 1, x0 + TILE - 1, y0 + TILE - 1], fill=p["wall"])


def _mix(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _walls(d: ImageDraw.ImageDraw, env: dict) -> None:
    p = env["palette"]
    for row in range(ROWS):
        for col in range(COLS):
            if SHARED_COLLISION[row][col] != 1:
                continue
            x0, y0 = col * TILE, row * TILE
            # solid block: lighter block face + trim outline (reads as a raised wall)
            d.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=_mix(p["wall"], p["tile_bevel"], 0.30))
            d.rectangle([x0 + 4, y0 + 4, x0 + TILE - 5, y0 + TILE - 5], outline=_mix(p["wall"], p["wall_trim"], 0.25))
    # wall_trim accent stripes on the outer border
    d.rectangle([0, 0, COLS * TILE - 1, ROWS * TILE - 1], outline=p["wall_trim"], width=3)


def _doors(d: ImageDraw.ImageDraw, env: dict) -> None:
    p = env["palette"]
    for row in range(ROWS):
        for col in range(COLS):
            if SHARED_COLLISION[row][col] != 2:
                continue
            x0, y0 = col * TILE, row * TILE
            d.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=_mix(p["floor_b"], p["accent"], 0.25))
            d.rectangle([x0 + 8, y0, x0 + TILE - 9, y0 + TILE - 1], fill=p["wall"], outline=p["wall_trim"])


def _chairs(d: ImageDraw.ImageDraw, env: dict) -> None:
    p = env["palette"]
    for row in range(ROWS):
        for col in range(COLS):
            if SHARED_COLLISION[row][col] != 3:
                continue
            cx, cy = col * TILE + 16, row * TILE + 16
            d.rectangle([cx - 10, cy - 6, cx + 10, cy + 10], fill=p["chair"])
            d.rectangle([cx - 10, cy - 8, cx + 10, cy - 6], fill=p["furniture"])


def _rug(d: ImageDraw.ImageDraw, env: dict) -> None:
    """A medallion rug in the central workstation hall (style signature)."""
    p = env["palette"]
    cx, cy = 10 * TILE + 16, 11 * TILE + 16
    for r, inset, color in ((140, 0, p["accent"]), (132, 14, p["rug"]), (96, 40, _mix(p["rug"], p["accent"], 0.5))):
        d.ellipse([cx - r, cy - r * 0.72, cx + r, cy + r * 0.72], fill=color)


def _decor(env_id: str, d: ImageDraw.ImageDraw, env: dict) -> None:
    """Small original furniture silhouettes inside solid regions only."""
    p = env["palette"]
    if env_id == "continental":
        for cx in (96, 608):
            for cy in (120, 264, 520, 664):
                d.rectangle([cx - 6, cy - 22, cx + 6, cy + 22], fill=p["wall_trim"], outline=p["wall"])
        _rug(d, env)
    elif env_id == "mechanic":
        for cx, cy in ((64, 120), (64, 152), (64, 184)):
            d.rectangle([cx - 14, cy - 8, cx + 14, cy + 8], fill=p["accent"], outline=p["wall"])  # tool rack
        for cx, cy in ((608, 152), (608, 560)):
            for i in range(3):
                d.ellipse([cx - 12 - i * 6, cy - 6 - i * 6, cx + 12 + i * 6, cy + 6 + i * 6],
                          fill=p["wall"], outline=p["accent"])
        _rug(d, env)
    elif env_id == "pirate":
        for cx, cy in ((64, 180), (616, 180), (64, 588), (616, 588)):
            d.ellipse([cx - 12, cy - 16, cx + 12, cy + 16], fill=p["furniture"], outline=p["wall_trim"])
            d.line([cx, cy - 16, cx, cy + 16], fill=p["wall_trim"])
        d.line([320, 12, 320, 60], fill=p["furniture"])  # mast hint
        _rug(d, env)
    elif env_id == "garage":
        # whiteboard on the left wall (marker streaks)
        d.rectangle([56, 96, 124, 288], fill=(242, 240, 234), outline=p["furniture"])
        for i, y0 in enumerate((120, 156, 192, 228)):
            d.line([64, y0, 64 + (84 - 12 * i), y0], fill=p["furniture"], width=2)
        # neon startup sign on the right wall + glow
        d.rectangle([588, 88, 626, 176], fill=p["accent"], outline=p["wall"])
        for cy in (104, 134, 164):
            d.ellipse([596, cy - 6, 618, cy + 6], fill=(255, 214, 222), outline=p["wall"])
        # roller-shutter slats on the back wall (garage door hint)
        for sx in range(356, 612, 26):
            d.line([sx, 4, sx, 58], fill=p["wall_trim"])
        # string lights across the ceiling line
        d.line([12, 66, 308, 66], fill=p["furniture"])
        d.line([308, 66, 628, 76], fill=p["furniture"])
        for lx in range(24, 620, 44):
            d.ellipse([lx - 3, 68, lx + 3, 80], fill=p["lamp"], outline=p["furniture"])
        # stacked pizza boxes + server-rack blinkens in a corner
        for i, cy in enumerate((606, 622, 638)):
            d.rectangle([84, cy - 8, 132, cy], fill=p["furniture"] if i % 2 else p["wall_trim"],
                        outline=p["wall"])
        for cy in (320, 352, 384, 416):
            d.rectangle([598, cy - 6, 620, cy + 6], fill=p["furniture"], outline=p["wall"])
            d.ellipse([606, cy - 2, 612, cy + 2], fill=p["accent"])
        # no rug: bare concrete is the garage look (accent-mixed rug reads neon)
    else:  # treehouse
        for cx, cy in ((72, 144), (600, 144), (72, 620), (600, 620)):
            d.rectangle([cx - 10, cy - 30, cx + 10, cy + 30], fill=p["furniture"], outline=p["wall"])
            for _ in range(3):
                d.ellipse([cx - 24, cy - 44, cx + 24, cy - 12], fill=p["accent"])
        d.rectangle([608, 300, 628, 480], fill=p["accent"], outline=p["wall"])  # ladder rail
        _rug(d, env)


def build_env(env_id: str, spec: dict) -> None:
    img = Image.new("RGB", (COLS * TILE, ROWS * TILE), spec["palette"]["wall"])
    d = ImageDraw.Draw(img)
    _iso_floor(d, spec)
    _doors(d, spec)
    _walls(d, spec)
    _chairs(d, spec)
    _decor(env_id, d, spec)
    ASSETS.mkdir(parents=True, exist_ok=True)
    img.save(ASSETS / f"{env_id}.png", optimize=True)
    chairs = [[col, row] for row in range(ROWS) for col in range(COLS) if SHARED_COLLISION[row][col] == 3]
    manifest = {
        "id": env_id,
        "name": spec["name"],
        "art_style": spec["art_style"],
        "version": 1,
        "background": f"/assets/environments/{env_id}.png",
        "palette": {k: list(v) for k, v in spec["palette"].items()},
        "sprite_scale": 2,
        "collision": SHARED_COLLISION,
        "anchors": ANCHORS,
        "workstations": chairs,
    }
    text = json.dumps(manifest, indent=2)
    (ASSETS / f"{env_id}.manifest.json").write_text(text + "\n")


def validate() -> None:
    for env_id in ENVS:
        manifest = json.loads((ASSETS / f"{env_id}.manifest.json").read_text())
        collision = manifest["collision"]
        assert len(collision) == ROWS and all(len(r) == COLS for r in collision), env_id
        assert collision == SHARED_COLLISION, env_id
        bg = Path(__file__).resolve().parent.parent / manifest["background"].lstrip("/")
        assert bg.exists() and bg.stat().st_size > 5_000, env_id
        for label, (tx, ty) in manifest["anchors"].items():
            assert 0 <= tx < COLS and 0 <= ty < ROWS, (env_id, label)
        assert manifest["workstations"], env_id
        for tx, ty in manifest["workstations"]:
            assert collision[ty][tx] == 3, (env_id, tx, ty)
    # BFS navigation: every anchor reaches a workstation without crossing solids
    from collections import deque

    for env_id in ENVS:
        manifest = json.loads((ASSETS / f"{env_id}.manifest.json").read_text())
        c = manifest["collision"]
        start = tuple(manifest["anchors"]["reception"])
        seen, q = {start}, deque([start])
        while q:
            x, y = q.popleft()
            for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
                if 0 <= nx < COLS and 0 <= ny < ROWS and (nx, ny) not in seen and c[ny][nx] != 1:
                    seen.add((nx, ny))
                    q.append((nx, ny))
        for tx, ty in manifest["workstations"]:
            assert (tx, ty) in seen, (env_id, tx, ty)
    print("validate: all environments OK")


def main() -> None:
    for env_id, spec in ENVS.items():
        build_env(env_id, spec)
        print(f"built {env_id}: {env_id}.png + {env_id}.manifest.json")
    validate()


if __name__ == "__main__":
    main()
