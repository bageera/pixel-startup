# G30 — Living Startup environment

The organizational graph (nodes: organization/goal/idea/project/milestone/task;
edges: depends_on/related/evidence) with a deterministic health projection and an
interactive mind map. No model calls anywhere in the pipeline.

## API (owner/bearer auth; GET reads, writes require office-write auth)

```
GET    /api/v1/graph?scope=company|household      # nodes + edges + per-node health; ETag (G32)
POST   /api/v1/graph/nodes   {"kind","title","parent_id?","priority?","due_at?","cadence?","scope?"}
PATCH  /api/v1/graph/nodes/{id}  {"status"| "verified"| ...}   # pause/reopen/complete/archive/verify
POST   /api/v1/graph/edges   {"from_id","to_id","kind"}            # idempotent, cycle-guarded, scope-checked
DELETE /api/v1/graph/edges/{id}
POST   /api/v1/config  {"startup": {"mood": "curious|focused|driven"}}   # CHO view preference (G30-F)
```

## Health semantics (all with human-readable explanations)

- seed: an idea without an agreed schedule — never decays on its own.
- growing: in progress; container shows verified share.
- thriving: verified milestone / progress on cadence.
- needs-attention: blocked (names the blocking node), behind cadence.
- withering: overdue actionable work (bubbles up through parents, naming the driver).
- dormant: paused — does not deteriorate by design.
- completed: done/verified — never rots.
- archived: parked by owner decision; excluded from health pressure.

Cadence is owner text like `"1/wk"` (verified flow per week); it becomes a
behind-cadence check only after an elapsed week. Verified progress never implies
commercial success — explanations cite verified milestones/tasks only.

## Frontend

Dashboard → "Living Startup" tab: lazy (fetches and sprites only when opened),
pan/zoom/drag, branch collapse (persisted), select→detail (state, explanation,
progress, dependencies), stalled-work panel, plant-seed form, connect-related
form (no duplicate records, depends_on cycle guard), CHO policy banner.
Positions persist per node in localStorage.

## Sprites

`assets/health/<state>.png` — 16×16, shape-distinct (readable without color),
regenerate deterministically with `python3 tools/generate-health-sprites.py`
(`--check` verifies a re-render matches byte-for-byte).

## Qualification

`tests/test_graph_g30.py` maps the 17 gates: seed-on-create, stable branches,
relationship-preserving projects, verified progress flips state, overdue
deteriorates, paused doesn't, completed doesn't rot, blockers named, deadline-free
ideas don't rot, reopen correctness, restart persistence, project isolation,
company/household isolation, scale (126 nodes projected <1s), zero LLM calls
(asserted), existing environments/sprites intact (npm suite), full regression.

## G32 resource discipline

See `docs/g32-resource-discipline.md` — the graph API answers 304 to
If-None-Match, and the frontend degrades on constrained devices / reduced-motion
before sacrificing information.
