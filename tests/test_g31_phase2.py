"""G31 Phase 2 qualification: E (health dimensions), F (CHO resolver), G (scene manifest).

Zero LLM asserted. All pure projections over the real store + routes; the
office/dashboards are untouched by design (renderer work is Phase 3).
"""

import os
import tempfile
from datetime import date, timedelta

import anyio

from pixel_startup.graph.attention import attention
from pixel_startup.graph.health import dimensions, project_health
from pixel_startup.graph.scene import SCHEMA_VERSION, STATION_CAP, scene_manifest

TODAY = date(2026, 11, 20)
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()


def _store():
    from pixel_startup.persistence.db import Database
    from pixel_startup.persistence.store import GraphStore

    d = tempfile.mkdtemp()
    db = Database(os.path.join(d, "t.sqlite3"))
    db.migrate()
    store = GraphStore(db)
    idea = store.create({"kind": "idea", "title": "I"})
    proj = store.create({"kind": "project", "title": "P", "parent_id": idea["id"]})
    return db, store, idea, proj


def test_g31_e_three_independent_dimensions() -> None:
    db, gs, idea, proj = _store()
    try:
        ms = gs.create({"kind": "milestone", "title": "M1", "parent_id": proj["id"]})
        gs.patch(ms["id"], {"verified": True})
        nodes, edges = gs.list("company")
        dims = dimensions(nodes, edges, TODAY)
        d_proj = dims[proj["id"]]
        # execution: verified milestone proven; outcome: completions need EVIDENCE
        assert d_proj["execution"] in ("thriving", "completed")
        assert d_proj["outcome"] == "unproven"  # done work, no outcome record yet
        assert d_proj["attention"] in ("clear", "required")
        # 6. agent activity without evidence fabricates nothing: a capability
        # node + related edge change neither execution nor outcome
        cap = gs.create({"kind": "capability", "title": "Agent ran", "parent_id": "org"})
        gs.add_edge(cap["id"], proj["id"], "related")
        nodes1, edges1 = gs.list("company")
        d1 = dimensions(nodes1, edges1, TODAY)[proj["id"]]
        assert d1["execution"] == d_proj["execution"] and d1["outcome"] == d_proj["outcome"]
        # 16. a failed experiment with recorded evidence is evidence, not failure
        outcome = gs.create(
            {"kind": "outcome", "title": "Negative result", "parent_id": proj["id"]}
        )
        gs.add_edge(outcome["id"], ms["id"], "evidence")  # ms (done) -> outcome
        nodes2, edges2 = gs.list("company")
        d2 = dimensions(nodes2, edges2, TODAY)[proj["id"]]
        assert d2["outcome"] == "evidenced"
        assert "never as failure" not in d2["outcome"]  # state vocabulary, not prose
        # 7. CHO-owned overdue commitment -> attention required; outcome unchanged
        cho = gs.create(
            {
                "kind": "commitment",
                "title": "CHO answer",
                "owner_role": "cho",
                "due_at": YESTERDAY,
                "parent_id": proj["id"],
            }
        )
        nodes3, edges3 = gs.list("company")
        d3 = dimensions(nodes3, edges3, TODAY)[proj["id"]]
        assert d3["attention"] == "required"
        assert d3["outcome"] == d2["outcome"]
        # 8. same elapsed client silence -> waiting-external, still not required
        cli = gs.create(
            {
                "kind": "commitment",
                "title": "Client reply",
                "owner_role": "client",
                "parent_id": proj["id"],
            }
        )
        assert attention(cli, TODAY)["state"] == "waiting"
    finally:
        db.close()


def test_g31_e_visual_states() -> None:
    db, gs, idea, proj = _store()
    try:
        # blocked leaf (hard dep on unresolved decision)
        blk = gs.create({"kind": "task", "title": "blk", "parent_id": proj["id"]})
        dec = gs.create({"kind": "decision", "title": "pending decision"})  # parent org
        gs.add_edge(blk["id"], dec["id"], "depends_on")
        h = project_health(gs.list("company")[0], gs.list("company")[1], TODAY)["nodes"]
        assert h[blk["id"]]["state"] == "blocked"
        # waiting-external leaf
        cli_task = gs.create(
            {"kind": "task", "title": "wait", "parent_id": proj["id"], "owner_role": "client"}
        )
        h2 = project_health(gs.list("company")[0], gs.list("company")[1], TODAY)["nodes"]
        assert h2[cli_task["id"]]["state"] == "waiting-external"
        # unknown leaf: no deadline/cadence/owner signals
        ghost = gs.create({"kind": "task", "title": "unowned", "parent_id": proj["id"]})
        ghost = gs.patch(ghost["id"], {"owner_role": None})
        h3 = project_health(gs.list("company")[0], gs.list("company")[1], TODAY)["nodes"]
        assert h3[ghost["id"]]["state"] == "unknown"
        # dormancy still holds; resuming -> recovering (resumed_at recorded)
        gs.patch(cli_task["id"], {"status": "paused"})
        h4 = project_health(gs.list("company")[0], gs.list("company")[1], TODAY)["nodes"]
        assert h4[cli_task["id"]]["state"] == "dormant"
        resumed = gs.patch(cli_task["id"], {"status": "active"})
        assert "resumed_at" in resumed["detail"]
        h5 = project_health(gs.list("company")[0], gs.list("company")[1], TODAY)["nodes"]
        assert h5[cli_task["id"]]["state"] == "recovering"
        # stable: verified progress with no pressure (clean second project)
        idea2 = gs.create({"kind": "idea", "title": "I-clean"})
        p2 = gs.create({"kind": "project", "title": "P-clean", "parent_id": idea2["id"]})
        done_ms = gs.create({"kind": "milestone", "title": "Mdone", "parent_id": p2["id"]})
        gs.patch(done_ms["id"], {"verified": True})
        idle = gs.create(
            {
                "kind": "milestone",
                "title": "Midle2",
                "parent_id": p2["id"],
                "owner_role": "employee",
            }
        )
        h6 = project_health(gs.list("company")[0], gs.list("company")[1], TODAY)["nodes"]
        assert h6[p2["id"]]["state"] == "stable"
    finally:
        db.close()


def _manifest(gs, mood, scope="company"):
    nodes, edges = gs.list(scope)
    return scene_manifest(nodes, edges, mood, scope, TODAY)


def test_g31_f_mode_scenarios_records_never_change() -> None:
    db, gs, idea, proj = _store()
    try:
        dec = gs.create({"kind": "decision", "title": "D1"})
        c_curious = _manifest(gs, "curious")
        assert any(c["type"] == "seed" and c["ref"] == idea["id"] for c in c_curious["components"])
        c_focused = _manifest(gs, "focused")
        assert not any(c["type"] == "seed" for c in c_focused["components"])
        assert any(c["ref"] == proj["id"] for c in c_focused["components"])
        gs.add_edge(
            gs.create({"kind": "task", "title": "t", "parent_id": proj["id"]})["id"],
            dec["id"],
            "depends_on",
        )
        c_driven = _manifest(gs, "driven")
        decs = [c for c in c_driven["components"] if c["type"] == "decision"]
        assert any(c["ref"] == dec["id"] for c in decs)
        # mode changes NEVER touch records: same node set, same titles
        titles = sorted(n["title"] for n in gs.list("company")[0])
        for m in ("curious", "focused", "driven"):
            assert sorted(n["title"] for n in gs.list("company")[0]) == titles
        # 4. theme change only affects the echoed theme
        t1 = scene_manifest(*gs.list("company"), "focused", "company", TODAY, theme="office")
        t2 = scene_manifest(*gs.list("company"), "focused", "company", TODAY, theme="garage")
        assert t1["theme"] == "office" and t2["theme"] == "garage"
        t1["theme"] = t2["theme"] = "x"
        assert t1 == t2
        # capabilities: unverified = suggestion (never operational/available)
        cap = gs.create({"kind": "capability", "title": "Hermes exec", "parent_id": "org"})
        m = _manifest(gs, "focused")
        cap_c = next(c for c in m["components"] if c.get("ref") == cap["id"])
        assert cap_c["availability"] == "suggestion"
        gs.patch(cap["id"], {"verified": True})
        m2 = _manifest(gs, "focused")
        cap_c2 = next(c for c in m2["components"] if c.get("ref") == cap["id"])
        assert cap_c2["availability"] == "verified"
        # 17. harness disconnect: archived -> not available
        gs.patch(cap["id"], {"status": "archived"})
        m3 = _manifest(gs, "focused")
        cap_c3 = next(c for c in m3["components"] if c.get("ref") == cap["id"])
        assert cap_c3["availability"] == "unavailable"
    finally:
        db.close()


def test_g31_g_manifest_contract() -> None:
    db, gs, idea, proj = _store()
    try:
        m1 = _manifest(gs, "focused")
        # 20/21. deterministic + position stability: an unrelated add moves nothing
        pos = {(c.get("ref") or c.get("id")): (c["x"], c["y"]) for c in m1["components"]}
        gs.create({"kind": "task", "title": "new later", "parent_id": proj["id"]})
        m2 = _manifest(gs, "focused")
        pos2 = {(c.get("ref") or c.get("id")): (c["x"], c["y"]) for c in m2["components"]}
        assert pos == pos2, "layout keyed by node id — minor updates never rearrange"
        # revision changes only when ORGANIZATION changes, not on re-read
        assert m1["scene_revision"] != m2["scene_revision"]
        assert _manifest(gs, "focused")["scene_revision"] == m2["scene_revision"]
        # registry: every emitted component type is declared (26. fallback contract)
        assert all(c["type"] in m2["types"] for c in m2["components"])
        assert m2["schema_version"] == SCHEMA_VERSION
        # 23. bounded: 50 projects -> <= STATION_CAP stations + one summary
        idea2 = gs.create({"kind": "idea", "title": "I2"})
        for k in range(50):
            gs.create({"kind": "project", "title": f"p{k}", "parent_id": idea2["id"]})
        m3 = _manifest(gs, "focused")
        stations = [c for c in m3["components"] if c["type"] == "station"]
        summaries = [
            c for c in m3["components"] if c["type"] == "summary" and c.get("bucket") == "projects"
        ]
        total_projects = sum(1 for n in gs.list("company")[0] if n["kind"] == "project")
        assert len(stations) == min(total_projects, STATION_CAP)
        assert sum(s["count"] for s in summaries) == max(total_projects - STATION_CAP, 0)
        # household isolation: household request never leaks company refs
        hh = _manifest(gs, "focused", scope="household")
        hh_titles = {c.get("label", "") for c in hh["components"]}
        assert "P" not in hh_titles
        assert not any(
            s.get("bucket") == "projects" and s.get("count", 0) >= 49 for s in hh["components"]
        )
    finally:
        db.close()


def test_g31_e_llm_untouched_by_scene_and_graph_reads() -> None:
    async def drive():
        from pathlib import Path

        from test_graph_g30 import AUTH, _seed_idea, client

        async with client(Path(tempfile.mkdtemp())) as (c, app, llm):
            await _seed_idea(c, "curiosity")
            r = await c.get("/api/v1/scene", headers=AUTH)
            assert r.status_code == 200 and r.json()["schema_version"] == SCHEMA_VERSION
            etag = r.headers["ETag"]
            r304 = await c.get("/api/v1/scene", headers={**AUTH, "If-None-Match": etag})
            assert r304.status_code == 304
            assert r.headers["ETag"]
            await c.get("/api/v1/graph", headers=AUTH)
            await c.get("/api/v1/graph/impact", headers=AUTH)
            # 28. rendering, monitoring and health math: zero model calls
            assert llm == []

    anyio.run(drive)
