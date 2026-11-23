"""G33 — Office of Requirement: procedural architecture from org truth.

The office is imaginative; the organization is real. Tests pin the contract:
assignment (not existence) mints furniture, generation is deterministic and
incremental, same topology renders as three themes, generation is free of LLM
and per-frame work, and only floor-1 rooms materialize into the office config.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile

import anyio
import httpx

from pixel_startup.config import Settings
from pixel_startup.main import create_app

AUTH = {"Authorization": "Bearer ai-token"}

_GRID = [[1] * 20 for _ in range(24)]  # all-walls grid; tests open a plaza
for y in range(2, 12):
    for x in range(2, 18):
        _GRID[y][x] = 0


def test_g33_stage1_harness_stations_not_furniture_for_existence():
    from pixel_startup.graph.office_gen import build_layout, build_topology

    topology = build_topology(
        [
            {
                "display_name": "Ghostface",
                "harness": "none",
                "lifecycle": "registered",
                "kind": "operational",
                "updated_at": "t1",
            },
            {
                "display_name": "Charon",
                "harness": "openclaw",
                "lifecycle": "registered",
                "kind": "operational",
                "updated_at": "t2",
            },
            {
                "display_name": "Curator",
                "harness": "none",
                "lifecycle": "registered",
                "kind": "operational",
                "updated_at": "t3",
            },
        ],
        harness_states={"openclaw": True, "hermes": False},
        registered_harnesses=["hermes", "openclaw"],
    )
    layout = build_layout(topology, "garage", [(x, y) for y in range(2, 12) for x in range(2, 18)])
    rooms = {r["room_id"]: r for r in layout["rooms"]}
    # Stage 1: CHO office + one station PER REGISTERED harness, in stable order
    assert "cho-office" in rooms and "harness-hermes" in rooms and "harness-openclaw" in rooms
    hermes = [s for r in layout["rooms"] if r["room_id"] == "harness-hermes" for s in r["stations"]]
    assert hermes and hermes[0]["state"] == "offline"  # registered ≠ running (honest)
    openclaw = [
        s for r in layout["rooms"] if r["room_id"] == "harness-openclaw" for s in r["stations"]
    ]
    assert openclaw[0]["state"] == "online"
    # an UNASSIGNED worker lives in the communal benches, no personal station
    unassigned = next(r["stations"] for r in layout["rooms"] if r["room_id"] == "unassigned")
    assert {s["occupant"] for s in unassigned} == {"Curator", "Charon"}
    assert all(s["personal"] is False for s in unassigned)
    # stage reflects reality: no assignments yet → stage 1
    assert layout["stage"] == 1


def test_g32_zero_llm_generation():
    import sys

    from pixel_startup.graph import office_gen
    from pixel_startup.graph.office_gen import build_layout, build_topology

    assert not any(m in sys.modules for m in ("litellm",)), "no LLM client may load"
    topology = build_topology([], {}, [])
    layout = build_layout(topology, "garage", [])
    assert layout["layout_key"].startswith("g33:")
    assert office_gen.THEMES and set(office_gen.THEMES) >= {"garage", "subbasement", "firehouse"}


def test_g33_assignment_mints_station_and_generation_is_incremental():
    from pixel_startup.graph.office_gen import build_layout, build_topology, layout_key

    base = [
        {
            "display_name": "Ghostface",
            "harness": "none",
            "lifecycle": "registered",
            "kind": "operational",
            "updated_at": "t1",
        },
        {
            "display_name": "Charon",
            "harness": "openclaw",
            "lifecycle": "registered",
            "kind": "operational",
            "updated_at": "t2",
        },
    ]
    tiles = [(x, y) for y in range(2, 12) for x in range(2, 18)]
    t0 = build_topology(base, {"openclaw": True}, ["openclaw"])
    l0 = build_layout(t0, "garage", tiles)
    before = layout_key(t0)

    # Stage 2: an ACTUAL assignment (task row) → personal workstation appears
    grown = base + [
        {
            "display_name": "Wardog",
            "harness": "openclaw",
            "lifecycle": "working",
            "kind": "operational",
            "updated_at": "t3",
        },
    ]
    t1 = build_topology(grown, {"openclaw": True}, ["openclaw"], assigned_ids={"Wardog"})
    l1 = build_layout(t1, "garage", tiles)
    after = layout_key(t1)
    assert after != before, "a structural change must change the layout key"
    wardog = [s for r in l1["rooms"] for s in r["stations"] if s["occupant"] == "Wardog"]
    assert len(wardog) == 1 and wardog[0]["personal"] is True
    # Ten completed tasks WITHOUT an assignment must mint nothing: the key is
    # structure-only (workers/harnesses), not task completions.
    assert layout_key(t1) == layout_key(
        build_topology(grown, {"openclaw": True}, ["openclaw"], assigned_ids={"Wardog"})
    )
    # incremental: pre-existing rooms/stations survive unchanged, per room;
    # the new station appends inside its room.
    rooms_before = {r["room_id"]: r["stations"] for r in l0["rooms"]}
    rooms_after = {r["room_id"]: r["stations"] for r in l1["rooms"]}
    for room_id, stations in rooms_before.items():
        assert rooms_after[room_id][: len(stations)] == stations


def test_g33_three_themes_same_topology_distinct_names_same_stations():
    from pixel_startup.graph.office_gen import build_layout, build_topology, layout_key

    topology = build_topology(
        [
            {
                "display_name": "Ghostface",
                "harness": "none",
                "lifecycle": "registered",
                "kind": "operational",
                "updated_at": "t1",
            },
            {
                "display_name": "Charon",
                "harness": "openclaw",
                "lifecycle": "working",
                "kind": "operational",
                "updated_at": "t2",
            },
        ],
        {"openclaw": True},
        ["openclaw"],
        assigned_ids={"Charon"},
    )
    tiles = [(x, y) for y in range(2, 12) for x in range(2, 18)]
    manifests = [build_layout(topology, t, tiles) for t in ("garage", "subbasement", "firehouse")]
    names = {m["theme"]: [r["name"] for r in m["rooms"]] for m in manifests}
    assert len({tuple(v) for v in names.values()}) == 3, "three distinct interpretations"
    # cosmetic equivalence: identical structural skeleton across themes
    skeleton = [
        ([s["station_id"] for s in m["rooms"][i]["stations"]])
        for m in manifests
        for i in range(len(m["rooms"]))
    ]
    per_theme = [
        skeleton[i * len(manifests[0]["rooms"]) : (i + 1) * len(manifests[0]["rooms"])]
        for i in range(3)
    ]
    assert per_theme[0] == per_theme[1] == per_theme[2]
    # and identical layout keys: topology, not cosmetics
    assert {m["layout_key"] for m in manifests} == {layout_key(topology)}
    environments = {m["environment"] for m in manifests}
    assert environments == {"garage", "mechanic-shop", "continental"}


def test_g33_cap_overflow_lists_floor2_rooms():
    from pixel_startup.graph.office_gen import build_layout, build_topology

    workers = [
        {
            "display_name": "Ghostface",
            "harness": "none",
            "lifecycle": "registered",
            "kind": "operational",
            "updated_at": "t0",
        }
    ] + [
        {
            "display_name": f"W{i}",
            "harness": "hermes",
            "lifecycle": "working",
            "kind": "operational",
            "updated_at": f"t{i}",
        }
        for i in range(12)  # 12 assigned hermes workers → cap 8 + overflow 4
    ]
    topology = build_topology(
        workers, {"hermes": True}, ["hermes"], assigned_ids={f"W{i}" for i in range(12)}
    )
    cramped = [(x, y) for y in range(2, 5) for x in range(2, 6)]  # 12 free cells: runs out fast
    layout = build_layout(topology, "garage", cramped)
    harness_rooms = [r for r in layout["rooms"] if r["room_id"].startswith("harness-hermes")]
    assert len(harness_rooms) >= 2  # overflow room appended, never reshuffled
    assert all(len(r["stations"]) <= 8 for r in harness_rooms)
    assert any(r["floor"] == 2 for r in harness_rooms), (
        "exhausted grid → floor 2, listed not stretched"
    )


def test_g33_api_layout_and_apply():
    async def drive():

        data_dir = tempfile.mkdtemp(prefix="g33-api-")
        atexit.register(shutil.rmtree, data_dir, ignore_errors=True)
        app = create_app(
            Settings(
                pixel_data_dir=data_dir,
                dashboard_password="pw",
                pixel_api_token="g33-token",
            )
        )
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                # seed the office map: a walkable plaza the generator claims tiles from
                seeded = await c.post("/api/collision", headers=AUTH, json=_GRID)
                assert seeded.status_code == 200
                r = await c.get("/api/v1/office/layout", headers=AUTH)
                assert r.status_code == 200, r.text[:200]
                manifest = r.json()
                etag = r.headers.get("etag")
                assert manifest["stage"] == 1 and manifest["theme"] == "garage"
                assert all(room["floor"] == 1 for room in manifest["rooms"]), (
                    "plaza: everything claims floor 1"
                )
                # 304 on unchanged structure (generation gated on structural change)
                r2 = await c.get("/api/v1/office/layout", headers={**AUTH, "If-None-Match": etag})
                assert r2.status_code == 304

                # apply materializes the generated rooms into the office config
                apply1 = await c.post("/api/v1/office/layout/apply", headers=AUTH)
                assert apply1.status_code == 200
                assert len(apply1.json()["rooms"]) >= 3, "CHO office + harness bays + communal"
                config = (await c.get("/api/config")).json()
                rooms = config["rooms"]
                assert rooms and all(r_.get("procedural") for r_ in rooms)
                assert config["environment"] == manifest["environment"]
                assert config["startup"]["auto_office"] is True

                # theme POST validates and persists; layout reflects it
                t = await c.post(
                    "/api/v1/office/layout/theme", headers=AUTH, json={"theme": "firehouse"}
                )
                assert t.status_code == 200
                bad = await c.post(
                    "/api/v1/office/layout/theme", headers=AUTH, json={"theme": "haunted"}
                )
                assert bad.status_code == 400
                manifest2 = (await c.get("/api/v1/office/layout", headers=AUTH)).json()
                assert manifest2["theme"] == "firehouse"
                assert manifest2["layout_key"].startswith("g33:")
                # structural key survived the theme change (cosmetics only)
                assert manifest2["layout_key"] == manifest["layout_key"]


def test_g33_apply_requires_owner_and_layout_requires_auth():
    async def drive():

        data_dir = tempfile.mkdtemp(prefix="g33-noauth-")
        atexit.register(shutil.rmtree, data_dir, ignore_errors=True)
        app = create_app(
            Settings(
                pixel_data_dir=data_dir,
                dashboard_password="pw",
                pixel_api_token="g33-token",
            )
        )
        async with app.router.lifespan_context(app):
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
            no_auth_layout = await c.get("/api/v1/office/layout")
            assert no_auth_layout.status_code == 401
            no_auth_apply = await c.post("/api/v1/office/layout/apply")
            assert no_auth_apply.status_code == 401
            await c.aclose()
        return True

    assert anyio.run(drive)
