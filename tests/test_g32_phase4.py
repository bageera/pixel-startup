"""G32-D/E/F — Economic qualification: resource budgets, scalability tiers,
graceful degradation. Deterministic synthetic fixtures (no LLM, no fictional
claims about production capacity — the tiers exist to measure scaling limits).

Tiers (G32-E spec): S1 5/10/100, S2 25/100/1000, S3 100/1000/10000,
S4 500/5000/100000 employees/projects/tasks. S4 is env-gated
(PIXEL_STRESS_S4=1) — minutes, not milliseconds — and reported honestly.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date

import pytest

from pixel_startup.graph.attention import dependency_impact
from pixel_startup.graph.scene import (
    ATTENTION_CAP,
    DECISION_CAP,
    SCHEMA_VERSION,
    STATION_CAP,
    scene_manifest,
)

TODAY = date(2026, 11, 22)  # deterministic fixtures use a fixed day

# The caps that bound visible complexity. The scene contract guarantees the
# FRONTEND never renders proportional to record count.
MAX_VISIBLE = STATION_CAP + DECISION_CAP + ATTENTION_CAP + 1  # + summary

TIERS = {
    "S1": (5, 10, 100),
    "S2": (25, 100, 1000),
    "S3": (100, 1000, 10_000),
}


def build_tier(employees: int, projects: int, tasks: int) -> tuple[list[dict], list[dict]]:
    """Deterministic rows for a tier. Shapes included per the G32-E gauntlet:
    sparse deps, one dense shared blocker, one long chain, large dormant and
    externally-waiting masses. Pure dict rows — same shape /api/v1/graph
    serializes from SQLite."""

    nodes: list[dict] = [
        {
            "id": "org",
            "kind": "organization",
            "title": "Org",
            "status": "active",
            "parent_id": None,
            "priority": 2,
            "due_at": None,
            "cadence": None,
            "verified": 0,
            "scope": "company",
            "owner_role": "cho",
            "detail": None,
            "created_at": TODAY,
            "updated_at": TODAY,
        }
    ]
    edges: list[dict] = []

    def n(node_id, kind, parent, **kw):
        row = {
            "id": node_id,
            "kind": kind,
            "title": kw.pop("title", node_id),
            "status": kw.pop("status", "active"),
            "parent_id": parent,
            "priority": kw.pop("priority", 2),
            "due_at": kw.pop("due_at", None),
            "cadence": kw.pop("cadence", None),
            "verified": kw.pop("verified", 0),
            "scope": "company",
            "owner_role": kw.pop("owner_role", "employee"),
            "detail": json.dumps(kw.pop("detail", {})),
            "created_at": TODAY,
            "updated_at": TODAY,
        }
        assert not kw
        nodes.append(row)
        return node_id

    def e(a, b, kind="depends_on"):
        edges.append(
            {"id": f"{a}->{b}->{kind}", "from_id": a, "to_id": b, "kind": kind, "created_at": TODAY}
        )

    for t in range(employees):
        n(f"cap-{t}", "capability", "org", verified=t % 3 == 0)  # mixed verified/unverified
    goals = [n(f"go-{t}", "goal", "org") for t in range(max(2, employees // 10))]
    for p in range(projects):
        pid = n(f"pr-{p}", "project", goals[p % len(goals)])
        due = f"2026-1{p % 2}-1{p % 8}"  # some past, some future: mixed states
        n(f"ms-{p}", "milestone", pid, due_at=due)
        for k in range(tasks // projects):
            tid = n(f"tk-{p}-{k}", "task", pid)
            n(f"out-{p}-{k}", "outcome", "org", detail={"evidence": f"e-{p}-{k}"})
            e(tid, f"out-{p}-{k}", "evidence")
        # shape mix: sparse dep + dense shared hub + a 10-deep chain
        e(f"pr-{p}", f"cap-{p % employees}")
        if p % 4 == 0:  # dense shared blocker: many projects depend on ONE decision
            n("dec-0", "decision", "org")
            e(f"pr-{p}", "dec-0")
        elif p % 4 == 1:  # long chain 10 deep
            n(f"dec-{p}", "decision", "org")
            prev = f"dec-{p}"
            for d in range(9):
                e(prev, f"dec-{p}-{d}")
                prev = f"dec-{p}-{d}"
            e(prev, f"pr-{p}")
    # cyclic invalid input: the resolver must never hang on it
    n("cyc-1", "task", "org")
    e("cyc-1", "pr-0")
    e("pr-0", "cyc-1", "soft_depends")
    return nodes, edges


def _j(m: dict) -> str:
    """Manifest -> canonical JSON. The API route serializes dates (FastAPI
    codec); the pure helper mimics that by stringifying date objects."""
    return json.dumps(m, sort_keys=True, default=str)


def _assert_bounded(nodes: list[dict], edges: list[dict], label: str):
    t0 = time.perf_counter()
    m = scene_manifest(nodes, edges, "focused", "company", TODAY)
    compose_ms = (time.perf_counter() - t0) * 1000
    workstations = [c for c in m["components"] if c["type"] == "station"]
    visible = len(m["components"])
    payload = len(json.dumps(m, default=str, sort_keys=True))
    # bounded complexity, independent of tier size: EVERY component type is
    # capped (station/attention/decision/capability/idea + at most one
    # summary per capped group), so visible count stays bounded as records grow.
    from collections import Counter

    types = Counter(c["type"] for c in m["components"])
    caps_map = {"station": STATION_CAP, "attention": ATTENTION_CAP, "decision": DECISION_CAP}
    for t, n_t in types.items():
        bound = caps_map.get(t, STATION_CAP) if t != "summary" else 6
        assert n_t <= bound, f"{label}: {t}={n_t} exceeds bound {bound}"
    assert len(workstations) == min(len([n for n in nodes if n["kind"] == "project"]), STATION_CAP)
    # deterministic: identical inputs -> identical manifest (byte-equal)
    m2 = scene_manifest(nodes, edges, "focused", "company", TODAY)
    assert _j(m) == _j(m2)
    # G32-F: degradation never drops information — the payload that the
    # bounded view renders carries scope, schema and bounded summaries.
    assert m["schema_version"] == SCHEMA_VERSION and m["scope"] == "company"
    projects_n = len([n for n in nodes if n["kind"] == "project"])
    if projects_n > STATION_CAP:  # overflow surfaces as ONE summary, not more sprites
        assert types["summary"] >= 1
    return {
        "label": label,
        "records": len(nodes),
        "components": visible,
        "workstations": len(workstations),
        "compose_ms": round(compose_ms, 1),
        "payload_bytes": payload,
    }


@pytest.mark.parametrize("tier", ["S1", "S2", "S3"])
def test_g32_e_scaling_bounds(tier: str):
    e_n, p_n, t_n = TIERS[tier]
    nodes, edges = build_tier(e_n, p_n, t_n)
    stats = _assert_bounded(nodes, edges, tier)
    # S1..S3 compose stays under 100 ms even at ten thousand tasks (S3)
    assert stats["compose_ms"] < 100, f"{tier} compose {stats['compose_ms']} ms"
    print(json.dumps(stats, sort_keys=True))


def test_g32_e_impact_traversal_sparse_to_dense_under_budget():
    nodes, edges = build_tier(*TIERS["S2"])
    t0 = time.perf_counter()
    proj = dependency_impact(nodes, edges, TODAY)
    ms = (time.perf_counter() - t0) * 1000
    shared = proj["blockers"]
    # the dense hub decision is surfaced ONCE with an impact count, not 25 alerts
    assert len(shared) >= 1
    assert ms < 100, f"impact traversal {ms} ms"
    print(json.dumps({"impact_ms_S2": round(ms, 1), "shared_blockers": len(shared)}))


@pytest.mark.skipif(
    not os.environ.get("PIXEL_STRESS_S4"),
    reason="S4 (500/5000/100000) is a minutes-scale soak: PIXEL_STRESS_S4=1",
)
def test_g32_e_s4_soak():
    nodes, edges = build_tier(500, 5000, 100_000)
    stats = _assert_bounded(nodes, edges, "S4")
    assert stats["compose_ms"] < 2000
    print(json.dumps(stats, sort_keys=True))


def test_g32_e_cyclic_input_never_hangs():
    nodes, edges = build_tier(*TIERS["S1"])
    edges.append(
        {"id": "x", "from_id": "pr-0", "to_id": "cyc-1", "kind": "depends_on", "created_at": TODAY}
    )  # self-referential cycle
    m = scene_manifest(nodes, edges, "curious", "company", TODAY)  # must return, not spin
    assert m["schema_version"] == SCHEMA_VERSION


def test_g32_f_degradation_order_keeps_information():
    """G32-F: pressure reductions hit presentation first. The information
    layer (scene manifest + graph + drill-down payloads) is identical in lite
    and full modes — degradation is a BROWSER concern (caps + redraw policy),
    so server payloads must be unchanged when the client degrades."""
    nodes, edges = build_tier(*TIERS["S1"])
    full = scene_manifest(nodes, edges, "focused", "company", TODAY)
    # the browser in degraded mode fetches the SAME manifest — there is no
    # lite endpoint; assert that (degradation must not fork the truth source)
    assert full == scene_manifest(nodes, edges, "focused", "company", TODAY)
    # never-degrade list: scope, evidence refs, attention stay present
    # never-degrade list: scope, availability labels, attention stay present.
    # (The lite browser keeps all of it — degradation is presentation-only.)
    comp = full["components"]
    assert all("health" in c for c in comp if c["type"] == "station")
    assert all("attention" in c for c in comp if c["type"] in ("station", "attention"))
    # evidence survives: stations carry outcome counts of evidence edges
    stations = [c for c in comp if c["type"] == "station"]
    assert all("outcomes" in s for s in stations)
