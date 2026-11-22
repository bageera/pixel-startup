"""G31-F/G: CHO requirement resolver + versioned scene manifest.

The CHO decides business direction and focus; this module only decides how to
present it. Pure function over stored rows + injected parameters — no LLM, no
new records, no layout churn: component positions are derived from the node id,
so minor organizational updates never rearrange an existing scene.

Theme / requirement / layout / state / capability remain separate concepts:
- theme:   visual pack identifier (echoed, never interpreted here);
- requirement: which components the scene needs for the CHO's mode;
- layout:  stable grid positions from ids;
- state:   health + attention projections;
- capability: verified harness functionality ONLY — unverified shows
  availability "suggestion", never "available".
"""

from __future__ import annotations

from datetime import date
from hashlib import md5

from pixel_startup.graph.attention import attention, dependency_impact
from pixel_startup.graph.health import project_health

SCHEMA_VERSION = 1
MODES = ("curious", "focused", "driven")
STATION_CAP = 10  # bounded rendering: at most ten project workstations
DECISION_CAP = 5  # and five decision stations
ATTENTION_CAP = 10  # and ten attention components


def _grid(seed: str, layer: int) -> tuple[int, int]:
    h = int(md5(seed.encode()).hexdigest(), 16)
    return (h % 12) * 52 + 12, layer * 96 + ((h >> 6) % 4) * 22


def scene_manifest(
    nodes: list[dict],
    edges: list[dict],
    mood: str,  # "curious"|"focused"|"driven" — asserted below
    scope: str,
    today: "date",
    theme: str = "office",
) -> dict:
    assert mood in MODES, f"unsupported CHO mode: {mood}"
    health = project_health(nodes, edges, today)["nodes"]
    proj = dependency_impact(nodes, edges, today)
    titles = {n["id"]: n["title"] for n in nodes}
    components: list[dict] = []
    by_node = {n["id"]: n for n in nodes}

    # organization root: always present
    org = next((n for n in nodes if n["kind"] == "organization"), None)
    if org is not None:
        x, y = _grid(org["id"], 0)
        components.append(
            {
                "type": "org",
                "ref": org["id"],
                "zone": "organization",
                "label": org["title"],
                "x": x,
                "y": y,
                "health": health.get(org["id"], {}).get("state", "unknown"),
            }
        )

    # project workstations, bounded. Priority (5 most important) then id.
    projects = sorted(
        (n for n in nodes if n["kind"] == "project" and n["status"] != "archived"),
        key=lambda n: (-int(n.get("priority") or 2), n["id"]),
    )
    blockers_by_ref = {}
    for b in proj["blockers"]:
        for ref in b["direct_ids"]:
            blockers_by_ref.setdefault(ref, []).append(b)
    for p in projects[:STATION_CAP]:
        x, y = _grid(p["id"], 1)
        v = health.get(p["id"], {})
        act = attention(p, today) if today is not None else {}
        dependencies = sorted(b["title"] for b in blockers_by_ref.get(p["id"], []))
        components.append(
            {
                "type": "station",
                "ref": p["id"],
                "zone": "projects",
                "label": p["title"],
                "x": x,
                "y": y,
                "health": v.get("state", "unknown"),
                "attention": act.get("state", "unknown"),
                "blocked_by": dependencies or None,
                "outcomes": sum(
                    1 for e in edges if e["kind"] == "evidence" and e["from_id"] == p["id"]
                )
                + sum(
                    1
                    for k in nodes
                    if k.get("parent_id") == p["id"]
                    and (k["status"] == "done" or k.get("verified"))
                ),
            }
        )
    rest = len(projects) - min(len(projects), STATION_CAP)
    if rest > 0:  # G32-C: aggregate rather than render forty elaborate stations
        x, y = _grid(f"{scope}-projects-summary", 1)
        components.append(
            {
                "type": "summary",
                "id": f"summary-projects-{scope}",
                "bucket": "projects",
                "zone": "projects",
                "label": f"{rest} more projects",
                "count": rest,
                "x": x,
                "y": y,
            }
        )

    # ideation: curious shows the seeds, focused/driven summarize
    ideas = [n for n in nodes if n["kind"] == "idea" and n["status"] == "active"]
    if mood == "curious":
        for i in sorted(ideas, key=lambda n: n["id"])[:STATION_CAP]:
            x, y = _grid(i["id"], 2)
            components.append(
                {
                    "type": "seed",
                    "ref": i["id"],
                    "zone": "ideation",
                    "label": i["title"],
                    "x": x,
                    "y": y,
                    "health": health.get(i["id"], {}).get("state", "unknown"),
                }
            )
        if len(ideas) > STATION_CAP:
            x, y = _grid(f"{scope}-ideas-summary", 2)
            components.append(
                {
                    "type": "summary",
                    "id": f"summary-ideas-{scope}",
                    "bucket": "ideas",
                    "zone": "ideation",
                    "label": f"{len(ideas) - STATION_CAP} more ideas",
                    "count": len(ideas) - STATION_CAP,
                    "x": x,
                    "y": y,
                }
            )
    elif ideas:
        x, y = _grid(f"{scope}-ideas-summary", 2)
        components.append(
            {
                "type": "summary",
                "id": f"summary-ideas-{scope}",
                "bucket": "ideas",
                "zone": "ideation",
                "label": f"{len(ideas)} ideas (curious reveals them)",
                "count": len(ideas),
                "x": x,
                "y": y,
            }
        )

    # decisions + shared-impact: the bottleneck station the driven CHO needs
    if mood == "driven":
        for b in proj["blockers"][:DECISION_CAP]:
            ref = b["decision"]
            n = by_node.get(ref)
            if n is None:
                continue
            x, y = _grid(ref, 3)
            components.append(
                {
                    "type": "decision",
                    "ref": ref,
                    "zone": "decisions",
                    "label": n["title"],
                    "x": x,
                    "y": y,
                    "health": health.get(ref, {}).get("state", "unknown"),
                    "directly_blocked": b["directly_blocked"],
                    "downstream_affected": len(b["downstream_affected"]),
                }
            )
        if proj["risks"]:
            x, y = _grid(f"{scope}-risks", 3)
            components.append(
                {
                    "type": "summary",
                    "id": f"summary-risks-{scope}",
                    "bucket": "risks",
                    "zone": "decisions",
                    "label": f"{len(proj['risks'])} scheduling risks",
                    "count": len(proj["risks"]),
                    "x": x,
                    "y": y,
                }
            )

    # attention components: what literally needs the CHO today (bounded)
    for n in sorted(nodes, key=lambda x: x["id"]):
        if len([c for c in components if c["type"] == "attention"]) >= ATTENTION_CAP:
            break
        act = attention(n, today)
        if not act["requires_action"]:
            continue
        x, y = _grid(n["id"], 4)
        components.append(
            {
                "type": "attention",
                "ref": n["id"],
                "zone": "attention",
                "label": n.get("title", n["id"]),
                "x": x,
                "y": y,
                "attention": act["state"],
                "overdue": act["overdue"],
            }
        )

    # capabilities: verified only. Unverified = suggestion, never availability.
    for n in sorted((n for n in nodes if n["kind"] == "capability"), key=lambda x: x["id"]):
        x, y = _grid(n["id"], 5)
        if n["status"] == "archived":
            availability = "unavailable"
        elif n.get("verified"):
            availability = "verified"
        else:
            availability = "suggestion"
        components.append(
            {
                "type": "capability",
                "ref": n["id"],
                "zone": "capability",
                "label": n["title"],
                "x": x,
                "y": y,
                "availability": availability,
            }
        )

    max_updated = max((n["updated_at"] for n in nodes), default="")
    revision = md5(f"{scope}|{mood}|{len(nodes)}|{len(edges)}|{max_updated}".encode()).hexdigest()[
        :16
    ]
    types = sorted({c["type"] for c in components})
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": scope,
        "theme": theme,
        "layout_seed": 42,
        "scene_revision": revision,
        "policy": mood,
        "data_freshness": max_updated or None,
        "types": types,  # unknown types are skipped by the frontend fallback
        "components": components,
    }
