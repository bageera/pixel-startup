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
