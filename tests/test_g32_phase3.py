"""G32 Phase-3 qualification: review traps + exit-criteria evidence.

Traps: ETag cache identity (auth/access context), deterministic placement
collisions, bounded capability chips. Every test is pure store/route work —
zero LLM (asserted in the G31 suites).
"""

import tempfile
from datetime import date
from pathlib import Path

import anyio

from pixel_startup.graph.scene import STATION_CAP, scene_manifest

TODAY = date(2026, 11, 24)


def _store():
    import os

    from pixel_startup.persistence.db import Database
    from pixel_startup.persistence.store import GraphStore

    db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    db.migrate()
    return db, GraphStore(db)


def test_g32_dense_scenes_place_every_chip_without_collision() -> None:
    db, gs = _store()
    try:
        # dense scene: 30 capabilities + 15 projects + 8 ideas, uncapped kinds
        for i in range(30):
            gs.create({"kind": "capability", "title": f"cap{i}"})
        idea = gs.create({"kind": "idea", "title": "I"})
        for i in range(15):
            gs.create({"kind": "project", "title": f"p{i}", "parent_id": idea["id"]})
        nodes, edges = gs.list("company")
        m = scene_manifest(nodes, edges, "focused", "company", TODAY)
        places = [(c["x"], c["y"]) for c in m["components"]]
        assert len(places) == len(set(places)), "chip collision — two chips share a cell"
        # deterministic: same org state → the exact same ordered manifest
        m2 = scene_manifest(nodes, edges, "focused", "company", TODAY)
        assert [tuple(c.items()) for c in m["components"]] == [
            tuple(c.items()) for c in m2["components"]
        ]
        # volume does not determine visible chips: capabilities bounded + summary
        caps = [c for c in m["components"] if c["type"] == "capability"]
        caps_summary = [
            c
            for c in m["components"]
            if c["type"] == "summary" and c.get("bucket") == "capabilities"
        ]
        assert len(caps) == STATION_CAP
        assert caps_summary and caps_summary[0]["count"] == 30 - STATION_CAP
    finally:
        db.close()


def test_g32_scene_etag_includes_access_context() -> None:
    """A 304 must never serve a stale scene: mood (policy) and scope are part
    of the cache identity — a changed CHO mode yields a different ETag, so a
    cached If-None-Match cannot answer 304 for a different scene."""

    async def drive():

        from test_graph_g30 import AUTH, _seed_idea, client

        async with client(Path(tempfile.mkdtemp())) as (c, app, llm):
            await _seed_idea(c, "curiosity")
            r = await c.get("/api/v1/scene", headers=AUTH)
            assert r.status_code == 200
            etag = r.headers["ETag"]
            again = await c.get("/api/v1/scene", headers={**AUTH, "If-None-Match": etag})
            assert again.status_code == 304, "unchanged scene answers 304"
            # access-context: flip the CHO policy through the config merge
            cur = (await c.get("/api/config")).json()
            startup = dict(cur.get("startup") or {})
            startup["mood"] = "driven"
            save = await c.post(
                "/api/v1/config",
                headers=AUTH,
                json={"startup": startup},
            )
            assert save.status_code == 200, save.text
            r2 = await c.get("/api/v1/scene", headers={**AUTH, "If-None-Match": etag})
            assert r2.status_code == 200, "policy change MUST invalidate the cached scene"
            assert r2.headers["ETag"] != etag
            assert r2.json()["policy"] == "driven"
            # scope is part of the identity: never share one ETag across scopes
            rc = await c.get("/api/v1/scene?scope=company", headers=AUTH)
            rh = await c.get("/api/v1/scene?scope=household", headers=AUTH)
            assert rc.headers["ETag"] != rh.headers["ETag"]
            assert rc.json()["scope"] == "company" and rh.json()["scope"] == "household"
            assert llm == []  # rendering + sync stay zero-LLM

    anyio.run(drive)


def test_g32_scene_components_flag_persists_through_noerase_merge() -> None:
    async def drive():
        from test_graph_g30 import AUTH, client

        async with client(Path(tempfile.mkdtemp())) as (c, app, llm):
            # G32-A flag is an owner decision in the startup config (strict merge)
            r = await c.post(
                "/api/v1/config", headers=AUTH, json={"startup": {"scene_components": True}}
            )
            assert r.status_code == 200
            cfg = (await c.get("/api/v1/config")).json()
            assert cfg["startup"]["scene_components"] is True
            # mood merge coexists (no key erasure)
            await c.post("/api/v1/config", headers=AUTH, json={"startup": {"mood": "curious"}})
            cfg = (await c.get("/api/v1/config")).json()
            assert (
                cfg["startup"]["scene_components"] is True and cfg["startup"]["mood"] == "curious"
            )
            assert llm == []

    anyio.run(drive)
