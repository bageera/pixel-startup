"""G30-G qualification: the 17 gates for the living-startup environment.

Boots the real app per test (real routes, real store, real health projection).
No model calls anywhere (gate 15 asserts zero litellm invocations on refresh).
"""

import contextlib
from datetime import date, timedelta

import anyio
import httpx

from pixel_startup.config import Settings
from pixel_startup.main import create_app

AUTH = {"Authorization": "Bearer g30-token"}
TODAY = date(2026, 11, 21)
NEXT_WK = (TODAY + timedelta(days=7)).isoformat()
LAST_WK = (TODAY - timedelta(days=7)).isoformat()


@contextlib.asynccontextmanager
async def client(tmp_path, litellm_calls=None):
    if litellm_calls is None:
        litellm_calls = []
    from pixel_startup.integrations import infra

    def fake_post(url, token, path, body):
        litellm_calls.append(path)
        return 200, {"choices": [{"message": {"content": "chat"}}], "model": "glm"}

    real_post = infra._make_post_client
    infra._make_post_client = lambda: fake_post
    infra._POST_CLIENT = fake_post
    try:
        app = create_app(
            Settings(
                pixel_data_dir=str(tmp_path / "data"),
                dashboard_password="pw",
                pixel_api_token="g30-token",
                pixel_session_secret="secret",
                litellm_url="http://llm.test",
            )
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                yield c, app, litellm_calls
    finally:
        infra._make_post_client = real_post
        infra._POST_CLIENT = None


async def _seed_idea(c, title="AI ops niche"):
    r = await c.post(
        "/api/v1/graph/nodes",
        headers=AUTH,
        json={"kind": "idea", "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_g30_g1_to_10_lifecycle(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as (c, app, llm):
            # 1. new idea appears as a seed
            idea = await _seed_idea(c)
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            view = next(n for n in snap["nodes"] if n["id"] == idea["id"])
            assert view["views"]["state"] == "seed"
            assert "does not rot" in view["views"]["explanation"]

            # 2. related ideas form stable branches (idempotent edges)
            idea2 = await _seed_idea(c, "Second idea")
            e1 = (
                await c.post(
                    "/api/v1/graph/edges",
                    headers=AUTH,
                    json={"from_id": idea["id"], "to_id": idea2["id"], "kind": "related"},
                )
            ).json()
            e2 = (
                await c.post(
                    "/api/v1/graph/edges",
                    headers=AUTH,
                    json={"from_id": idea["id"], "to_id": idea2["id"], "kind": "related"},
                )
            ).json()
            assert e1["id"] == e2["id"]  # one record, never duplicated

            # 3. project creation preserves idea relationships
            proj = (
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={"kind": "project", "title": "P1", "parent_id": idea["id"]},
                )
            ).json()
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            assert proj["parent_id"] == idea["id"]
            assert any(e["id"] == e1["id"] for e in snap["edges"])

            # 9. an idea without a deadline does not rot automatically
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            v_idea = next(n for n in snap["nodes"] if n["id"] == idea["id"])
            assert v_idea["views"]["state"] != "withering"
            assert v_idea["views"]["state"] != "needs-attention"

            # 4. verified progress changes the visual state
            ms = (
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={"kind": "milestone", "title": "M1", "parent_id": proj["id"]},
                )
            ).json()
            patched = (
                await c.patch(
                    f"/api/v1/graph/nodes/{ms['id']}", headers=AUTH, json={"verified": True}
                )
            ).json()
            assert patched["status"] == "done"
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            v_ms = next(n for n in snap["nodes"] if n["id"] == ms["id"])
            assert v_ms["views"]["state"] in ("thriving", "completed")  # verified: proven progress

            # 5. overdue actionable work shows deterioration
            task = (
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={
                        "kind": "task",
                        "title": "Late task",
                        "parent_id": proj["id"],
                        "due_at": LAST_WK,
                    },
                )
            ).json()
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            v_task = next(n for n in snap["nodes"] if n["id"] == task["id"])
            assert v_task["views"]["state"] == "withering"
            v_proj = next(n for n in snap["nodes"] if n["id"] == proj["id"])
            assert v_proj["views"]["state"] == "withering"  # bubbles up with driver
            assert (
                "Overdue" in v_proj["views"]["explanation"]
                or "child work" in v_proj["views"]["explanation"]
            )

            # 6. paused work does not deteriorate
            p = (
                await c.patch(
                    f"/api/v1/graph/nodes/{task['id']}", headers=AUTH, json={"status": "paused"}
                )
            ).json()
            assert p["status"] == "paused"
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            v_task = next(n for n in snap["nodes"] if n["id"] == task["id"])
            assert v_task["views"]["state"] == "dormant"
            assert "not deteriorating" in v_task["views"]["explanation"]
            # parent no longer withering: paused child is parked, M1 thriving
            v_proj = next(n for n in snap["nodes"] if n["id"] == proj["id"])
            assert v_proj["views"]["state"] != "withering"

            # 7. completed work does not rot
            done = (
                await c.patch(
                    f"/api/v1/graph/nodes/{ms['id']}", headers=AUTH, json={"status": "done"}
                )
            ).json()
            assert done["status"] == "done"
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            v_ms = next(n for n in snap["nodes"] if n["id"] == ms["id"])
            assert v_ms["views"]["state"] in ("completed", "thriving")

            # 8. blocked work identifies its actual dependency
            blk = (
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={"kind": "task", "title": "Blocked task", "parent_id": proj["id"]},
                )
            ).json()
            dep_task = (
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={"kind": "task", "title": "Dependency source", "parent_id": proj["id"]},
                )
            ).json()
            await c.post(
                "/api/v1/graph/edges",
                headers=AUTH,
                json={"from_id": blk["id"], "to_id": dep_task["id"], "kind": "depends_on"},
            )
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            v_blk = next(n for n in snap["nodes"] if n["id"] == blk["id"])
            assert v_blk["views"]["state"] == "needs-attention"
            assert "Dependency source" in v_blk["views"]["explanation"]

            # 10. reopening work updates its state correctly
            re = (
                await c.patch(
                    f"/api/v1/graph/nodes/{task['id']}", headers=AUTH, json={"status": "active"}
                )
            ).json()
            assert re["status"] == "active"
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            v_task = next(n for n in snap["nodes"] if n["id"] == task["id"])
            assert v_task["views"]["state"] == "withering"  # overdue again: honest

            # 11. graph survives restart (same data dir, new app)
            app2 = create_app(
                Settings(
                    pixel_data_dir=str(tmp_path / "data"),
                    dashboard_password="pw",
                    pixel_api_token="g30-token",
                    pixel_session_secret="secret",
                )
            )
            async with app2.router.lifespan_context(app2):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app2), base_url="http://t"
                ) as c2:
                    snap2 = (await c2.get("/api/v1/graph", headers=AUTH)).json()
                    ids2 = {n["id"] for n in snap2["nodes"]}
                    assert idea["id"] in ids2 and proj["id"] in ids2 and blk["id"] in ids2
                    v_blk2 = next(n for n in snap2["nodes"] if n["id"] == blk["id"])
                    assert v_blk2["views"]["state"] == "needs-attention"

    anyio.run(drive)


def test_g30_g12_to_14_isolation_and_scale(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as (c, app, llm):
            # 12. multiple projects remain isolated (structure + health)
            i1 = await _seed_idea(c, "One")
            i2 = await _seed_idea(c, "Two")
            p1 = (
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={"kind": "project", "title": "P-one", "parent_id": i1["id"]},
                )
            ).json()
            p2 = (
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={"kind": "project", "title": "P-two", "parent_id": i2["id"]},
                )
            ).json()
            (
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={
                        "kind": "task",
                        "title": "T-one",
                        "parent_id": p1["id"],
                        "due_at": LAST_WK,
                    },
                )
            ).json()
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            by_id = {n["id"]: n for n in snap["nodes"]}
            assert by_id[p1["id"]]["views"]["state"] == "withering"
            assert by_id[p2["id"]]["views"]["state"] == "growing"  # no bleed

            # 13. company/household scope isolation holds
            hh = (
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={"kind": "idea", "title": "Household idea", "scope": "household"},
                )
            ).json()
            comp = (await c.get("/api/v1/graph?scope=company", headers=AUTH)).json()
            hhv = (await c.get("/api/v1/graph?scope=household", headers=AUTH)).json()
            assert hh["id"] not in {n["id"] for n in comp["nodes"]}
            assert hh["id"] in {n["id"] for n in hhv["nodes"]}
            # cross-scope edge refused
            r = await c.post(
                "/api/v1/graph/edges",
                headers=AUTH,
                json={"from_id": hh["id"], "to_id": i1["id"], "kind": "related"},
            )
            assert r.status_code == 422
            # cross-scope parenting refused
            r = await c.post(
                "/api/v1/graph/nodes",
                headers=AUTH,
                json={
                    "kind": "project",
                    "title": "sneak",
                    "parent_id": i1["id"],
                    "scope": "household",
                },
            )
            assert r.status_code == 422

            # 14. large graphs remain navigable (deterministic layout cost, O(n))
            import time

            t0 = time.monotonic()
            for k in range(120):
                await c.post(
                    "/api/v1/graph/nodes",
                    headers=AUTH,
                    json={"kind": "task", "title": f"bulk-{k}", "parent_id": p1["id"]},
                )
            t1 = time.monotonic()
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()  # one projection pass
            t2 = time.monotonic()
            assert len(snap["nodes"]) >= 126
            assert t2 - t1 < 1.0, f"projection too slow: {t2 - t1:.3f}s"
            assert t1 - t0 < 20.0

            # 15. office idle inference remains zero
            assert llm == [], f"litellm was invoked during graph ops: {llm}"

    anyio.run(drive)


def test_g30_f_policy_view_preference(tmp_path) -> None:
    async def drive():
        async with client(tmp_path) as (c, app, llm):
            await _seed_idea(c, "Policy probe")
            # set the CHO mood through the existing authorized config API
            cfg = (await c.get("/api/v1/config", headers=AUTH)).json()
            cfg["startup"]["mood"] = "driven"
            await c.post("/api/v1/config", headers=AUTH, json=cfg)
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            assert snap["policy"] == "driven"
            # invalid mood refused: the previous valid preference is kept
            cfg["startup"]["mood"] = "wild"
            await c.post("/api/v1/config", headers=AUTH, json=cfg)
            snap = (await c.get("/api/v1/graph", headers=AUTH)).json()
            assert snap["policy"] == "driven"  # invalid save never clobbers state
            assert llm == []

    anyio.run(drive)


def test_g30_g6_to_7_paused_completed_never_rot_pure() -> None:
    from pixel_startup.graph.health import project_health

    org = {
        "id": "org",
        "kind": "organization",
        "title": "org",
        "status": "active",
        "parent_id": None,
        "created_at": "2026-01-01",
        "verified": 0,
    }
    idea = {
        "id": "i1",
        "kind": "idea",
        "title": "I",
        "status": "active",
        "parent_id": "org",
        "created_at": "2026-01-01",
        "verified": 0,
    }
    proj = {
        "id": "p1",
        "kind": "project",
        "title": "P",
        "status": "active",
        "parent_id": "i1",
        "created_at": "2026-01-01",
        "verified": 0,
    }
    paused = {
        "id": "t1",
        "kind": "task",
        "title": "paused",
        "status": "paused",
        "parent_id": "p1",
        "created_at": "2026-01-01",
        "due_at": LAST_WK,
        "verified": 0,
    }
    done = {
        "id": "m1",
        "kind": "milestone",
        "title": "m",
        "status": "done",
        "parent_id": "p1",
        "created_at": "2026-01-01",
        "verified": 1,
    }
    nodes, edges = [org, idea, proj, paused, done], []
    h = project_health(nodes, edges, TODAY)["nodes"]
    assert h["t1"]["state"] == "dormant"  # 6: overdue but paused -> dormant
    assert h["m1"]["state"] == "completed"  # verified + done: proven, completed
    assert h["p1"]["state"] != "withering"  # paused child doesn't rot the parent
    assert h["p1"]["progress"] == 1.0
    # pause the milestone too: project -> dormant (all parked), still not withered
    paused2 = {**paused, "id": "p2", "status": "paused"}
    done2 = {**done, "id": "m2", "status": "paused", "verified": 0}
    h2 = project_health([org, idea, proj, paused2, done2], [], TODAY)["nodes"]
    assert h2["p1"]["state"] != "withering"
    # completed container never rots
    all_done = [
        org,
        idea,
        {**proj, "status": "done"},
        {**paused, "id": "t3", "status": "done", "verified": 1},
        done,
    ]
    h3 = project_health(all_done, [], TODAY)["nodes"]
    assert h3["p1"]["state"] == "completed"
