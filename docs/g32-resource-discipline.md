# G32 — Resource discipline (acceptance spec + design evidence)

Rule: **Organizational complexity can grow. Presentation complexity must remain controlled.**
The cost of observing the business must not grow proportionally with the size of the
business; the CHO sees the most consequential information, not the largest quantity.

## Acceptance table → design evidence (as of G30/G32)

| Resource | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| Browser CPU | Minimal when idle | DONE (graph) + **DONE (office, G32-B)** | Living Startup renders SVG only on data change or interaction; no timers/animation loops added. The office loop now runs `assets/office-loop.js`: idle ambient cap 100 ms (5 fps lite, 30 fps walking) with cosmetic logic at rAF cadence — measured in `docs/g31-baseline.md` (idle full redraws 32/2s → 14/2s in the same env; 60 fps→≤10 fps on live browsers). |
| Browser memory | Bounded by visible scene | DONE | Collapsed branches are excluded from the DOM (`g30Children`), edges/nodes are text+16px sprites; positions/policies in localStorage, not memory. |
| GPU | No continuous rendering without a reason | DONE | Graph: no canvas, no rAF, static PNG sprites. Office: the only canvas redraws at the policy caps — no continuous 60 fps redraw anymore (`.pixel-lite`/reduced-motion collapses it to 5 fps). |
| Network | Incremental over full-state repeats | DONE (G32) | `GET /api/v1/graph` answers with a weak ETag summarizing state (scope, policy, node count, max `updated_at`); `If-None-Match` gets **304**, client skips re-render. Test: `test_g32_network_incremental_and_bounds`. Frontend `loadGraph` sends the cached ETag. The Office Scene tab reuses the same ETag/304 pattern for `GET /api/v1/scene`. |
| Python backend | Cached projections, event-driven recompute | DEFERRED (measured need) | Projection is a single-pass O(n) over stored rows, exercised only on read; 126-node graph projects in <1s (tested). Add a version-keyed cache (last node write timestamp keyed) only when graphs outgrow that. |
| LLM inference | Zero for rendering/monitoring/health | DONE + TESTED | Health, layout, explanations are pure functions of rows + `today` (`graph/health.py`); tests assert **zero litellm invocations** across graph creation, reads and refresh (G30-G15 ×2). |
| Storage | Retain evidence without endless duplication | DONE | Edge triples UNIQUE (re-link returns the same record — tested); household/company rows never interleave; org is a single seeded row; MinIO access log appends one line per startup (bounded, date-keyed). |

## Graceful degradation

- `prefers-reduced-motion: reduce` → all animation/transition durations collapse (CSS, both frontend files).
- Constrained device (`hardwareConcurrency <= 2` or `deviceMemory <= 2`) → `pixel-lite` class kills animation, transitions and filters; information (text/SVG/data) is never sacrificed.
- The dashboard (no 3D) is the permanent lightweight view; the isometric office is opt-in.

## Applicability to G31

The same rule binds the office view (G31): idle inference stays zero, the render loop
only animates when the scene is open and visible, and detail tiers must degrade
before information fidelity does. G30/G32 infrastructure (health projection, policy,
graph API with ETag) is the shared substrate for it.

## Phase 4 — Economic qualification (G32-D/E/F), 2026-11-22

**Test environment:** host macOS 26.6.2 (arm), Docker `pixel-startup:latest`
(7a5815e build), Python 3.12 in image, pytest via host `.venv`, browser =
harness headless Chrome (throttled VM, rAF floor ≈15 Hz — 60 Hz budgets are
quoted per-cap math, not this floor). Numbers from
`uv run pytest tests/test_g32_phase4.py -s` plus the curl/CDP runs recorded
below; fixtures are deterministic synthetic tiers (fixed ids, fixed day).

### G32-D — measured resource budgets

| Budget (spec target) | Measured | Where |
| --- | --- | --- |
| Idle animation 0 FPS when disabled | static mode: 0 draws / 1.5 s after the dirty frame | CDP + node test |
| Active ambient ≤ 15–30 FPS | caps 33/100/200 ms = ≤30/10/5 FPS; VM floor observed p50 83 ms | office-loop + CDP |
| Rendering inference 0 LLM calls | asserted across scene/graph/config/drill-down reads | phase 3+4 suites |
| Health projection inference 0 LLM | project_health/attention/scene_manifest: pure rows→dict | code + suite |
| Recompose on meaningful change only | ETag: unchanged org → 304 (recompose skipped client-side) | phase-2/3 tests |
| No repeated full-scene redraws | static env layer: 1 drawImage/draw, rebuilt only on background src change | phase 3 |
| Network sync incremental/scoped | scene payload 5.2 KB @ 22.6k records (S3); 5.3 KB @ 213k (S4) | /api/v1/scene bytes |
| Scene memory bounded | bounded chip layout (≤8 + "+N"), dashboard static SVG, heap flat | phase-3 CDP |
| Visual update < 100 ms after data | routes: scene 13.6–21 ms, 304 ≈20 ms, graph 16–21 ms; paint ≤ one frame cap after arrival | curl medians ×5 (VM) |

Cost separation: rendering = bounded browser work above; org projection =
O(n) SQLite single-pass, S3 ≤ 93 ms; network = 304-empty / ≤5.3 KB; storage
and audit = existing store, unchanged; harness execution = outside the scene
path; LLM = 0 by test.

### G32-E — scaling curve (records → visible components)

| Tier | Records | Compose ms | Visible components | Payload bytes |
| --- | --- | --- | --- | --- |
| S1 | 235 | 2.5 | 26 | 4,175 |
| S2 | 2,279 | 11.2 | 33 | 5,219 |
| S3 | 22,612 | 86–93 | 33 | 5,243 |
| S4 (env-gated run) | 213,052 | 1,072 | 33 | 5,303 |

Records ×~900 → visible components flat at 33; payload ~5 KB. Fixture shapes:
sparse deps, dense shared blocker (surfaced ONCE with an impact count — 268
shared at S2), 10-deep chains, dormant + external masses, cyclic input
(resolver returns, never spins). Two real quadratic hotspots found and fixed
in-commit: per-iteration component rescan in the scene attention loop
(scene.py) and the per-blocker by_target edge-index rebuild (attention.py) —
S3 compose 1,715 → 93 ms. Honest limit: compose is O(nodes+edges) single-pass;
S4 (~213k records) costs ~1.1 s per recompose — acceptable for rare
CHO-driven recomposes; add a version-keyed cache if S4 recomposes get
frequent. Backend restart recovery verified live (make restart → healthz →
client 200/304 refetch).

### G32-F — degradation order evidence

Server truth has no lite fork: degraded clients fetch the SAME manifest
(tested byte-equal). Browser ladder (office-loop/CSS) drops, in order:
ambient animation → redraw frequency (lite 200 ms) → full redraws (static,
dirty-only) — information surfaces stay complete (bounded chips + drill-down
+ the dashboard's Office Scene textual view, verified live). Never degraded:
scope isolation, attention/evidence fields in the manifest (tested), CHO
approval routes.

**Skips (honest):** sustained profiling on a non-throttled 60 Hz display
(VM floor ≈15 Hz); multi-client concurrency soak; S4 in the default suite
(minutes-scale; PIXEL_STRESS_S4=1). Add when a real deployment profile exists.
