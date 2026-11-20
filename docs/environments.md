# Environments (G22)

Reusable isometric office packs. Continental is the default on first deployment.

## What ships today

| id | name | style |
|---|---|---|
| `continental` | The Continental | Art Deco concierge headquarters (default) |
| `mechanic` | Backline Mechanic Shop | industrial garage / engineering office |
| `pirate` | Inferno Deck | nautical flagship deck |
| `treehouse` | Lost Pines Treehouse | woodland canopy headquarters |

All four are original, procedurally generated art (`tools/generate-environments.py`) —
no copyrighted production artwork. Regenerate with
`python3 tools/generate-environments.py` (deterministic; also runs its own validator).

## Pack layout

```
assets/environments/<id>.png              640x800 background (20x25 grid @ 32px)
assets/environments/<id>.manifest.json    manifest (see below)
```

Manifest fields: `id, name, art_style, version, background, palette, sprite_scale,
collision, anchors, workstations`.

- **Collision contract:** 20×25 grid; `0` walkable, `1` wall/solid, `2` door, `3` chair
  (chairs double as workstation interaction seats). Values match the deployed office's
  proven map.
- **Anchors:** tile coordinates `{reception, workstations, archives}` — room quick-nav
  follows the active environment's anchors.
- **Workstations:** list of chair tile coordinates (auto-extracted from the grid).
- **Sprite scale:** `sprite_scale` (current packs keep the deployed value 2).

## Navigation guarantee

All four default packs share the deployed office's walkable topology (characters,
coordinates, chairs and interactions keep their exact behavior — identity, tasks,
permissions and memory are independent of the environment). A pack MAY ship its own
collision grid: non-continental packs apply `manifest.collision` client-side
(the dashboard-edited continental layout keeps its existing precedence).

A pathfinding check (reception → every workstation, solids refused) runs as part of
`tools/generate-environments.py` and again in the npm regression suite.

## Selection and persistence

- Dashboard → Characters view → "Environment" select → applied via the authed
  `POST /api/config` flow; the choice persists in `data/pixel-config.json` and
  survives restart. Legacy config bodies (without an `environment` field) never
  erase the selection.
- Python backend: `GET /api/environment` (public) and `PUT /api/environment`
  (bearer/session) — persisted in SQLite `app_settings` (schema v3) and written
  through to `pixel-config.json` so the deployed JS baseline stays in sync.
- The office page (`index.html`) reads the selection at load; background art is
  decoded on demand (only when the environment is active).

## Adding a future pack (no backend changes)

1. Draw a 640×800 background and generate/adjust a 20×25 collision grid.
2. Drop `<id>.png` + `<id>.manifest.json` into `assets/environments/`.
3. Run the generator's `validate()` (or `python3 tools/generate-environments.py`) to
   confirm the contract: dimensions, values, anchors reachable from reception, every
   workstation on a chair cell.
4. Nothing to register: `GET /api/environments` lists packs from the directory, and
   the Python API validates ids against the same directory — the pack is selectable
   immediately.

## Failure behavior

Unknown/missing manifest or broken pack at load → the office keeps the previous or
legacy environment (fallback background `assets/oficina-placeholder.png`) and logs a
warning; the server never fails on a bad pack (catalog parsing skips broken files).
