"""G31-C/D: responsibility-aware attention + decision/dependency impact.

Pure functions over stored rows plus an injected `today`. No LLM. Two gates:

- G31-C: identical elapsed times produce different states based on ownership,
  explicit commitments and CHO policy. Elapsed time alone never implies failure:
  an ownerless node or an externally-waiting one with no deadline is never
  "overdue". Business calendars are NOT silently assumed — a `business_days`
  follow-up policy is computed on Mon-Fri only when explicitly configured,
  otherwise plain days.
- G31-D: actually blocked vs potentially affected vs scheduling risks are
  DISTINCT outputs; a shared blocker surfaces once with its impact count.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

KIND_ROLES = {"decision": "cho", "commitment": None}  # default roles by kind


def _d(text: str | None) -> date | None:
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _detail(node: dict) -> dict:
    try:
        v = json.loads(node.get("detail") or "{}")
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


RESOLVED = ("done", "archived", "paused")


def attention(node: dict, today: date) -> dict:
    """Responsibility model for one node (G31-C).

    action states: actionable | waiting | blocked | dormant | resolved | unknown
    """
    owner = node.get("owner_role")
    kind = str(node.get("kind") or "")
    if owner is None and KIND_ROLES.get(kind):
        owner = KIND_ROLES[kind]
    elif owner is None and kind in ("task", "milestone", "project"):
        owner = "employee"
    status = node["status"]
    if status == "done":
        return {
            "owner": owner,
            "state": "resolved",
            "requires_action": False,
            "overdue": False,
            "explanation": "Resolved: completed work.",
        }
    if status == "archived":
        return {
            "owner": owner,
            "state": "resolved",
            "requires_action": False,
            "overdue": False,
            "explanation": "Resolved: archived by the CHO.",
        }
    if status == "paused":
        return {
            "owner": owner,
            "state": "dormant",
            "requires_action": False,
            "overdue": False,
            "explanation": "Dormant: intentionally paused — elapsed time does not count.",
        }
    det = _detail(node)
    due = _d(node.get("due_at"))
    overdue = bool(due and due < today)

    if kind in ("decision", "commitment", "task", "milestone", "project", "goal", "idea"):
        if owner in ("cho", "employee"):
            if overdue:
                return {
                    "owner": owner,
                    "state": "actionable",
                    "requires_action": True,
                    "overdue": True,
                    "explanation": f"Overdue actionable commitment ({owner}): due "
                    f"{node['due_at']} — {owner.upper()} response expected.",
                }
            if due:
                return {
                    "owner": owner,
                    "state": "actionable",
                    "requires_action": True,
                    "overdue": False,
                    "explanation": f"Actionable ({owner}): due {node['due_at']}.",
                }
            return {
                "owner": owner,
                "state": "unknown",
                "requires_action": False,
                "overdue": False,
                "explanation": "No deadline agreed — no elapsed-time judgement is inferred.",
            }
        if owner in ("client", "vendor", "external"):
            state = "waiting"
            expl = f"Waiting externally ({owner}): silence alone is not a failure."
            followup = det.get("followup") or {}
            days = followup.get("days")
            if days:
                base = _d(node.get("updated_at"))
                if base and followup.get("kind") == "business_days":
                    days_left = _business_days_between(base, today)
                elif base:
                    days_left = (today - base).days
                else:
                    days_left = None
                if days_left is not None and days_left >= int(days):
                    return {
                        "owner": owner,
                        "state": "actionable",
                        "requires_action": True,
                        "overdue": False,
                        "explanation": f"Follow-up policy ({owner}, "
                        f"{followup.get('kind') or 'days'}={days}) reached —"
                        " follow-up opportunity.",
                    }
            return {
                "owner": owner,
                "state": state,
                "requires_action": False,
                "overdue": False,
                "explanation": expl,
            }
    # contact / capability / organization: no actor semantics
    return {
        "owner": owner,
        "state": "unknown",
        "requires_action": False,
        "overdue": False,
        "explanation": "Unknown: no ownership or commitment data — nothing is invented.",
    }


def _business_days_between(a: date, b: date) -> int:
    days, cur = 0, a
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def dependency_impact(nodes: list[dict], edges: list[dict], today: date) -> dict:
    """G31-D impact summary. DISTINCT outputs:
    blocked (hard depends_on, unresolved), affected (downstream of blocked via
    parent chain or chained depends_on), risks (soft_depends), and shared
    blockers surfaced ONCE with their impact counts. Cycle-safe."""
    done_ids = {n["id"] for n in nodes if n["status"] == "done" or n.get("verified")}
    superseded_ids = {n["id"] for n in nodes if _detail(n).get("superseded_by")}
    resolved = done_ids | superseded_ids
    titles = {n["id"]: n["title"] for n in nodes}
    parents = {n["id"]: n.get("parent_id") for n in nodes}

    direct: dict[str, list[dict]] = {}  # blocker id -> hard deps pointing at it
    risks: list[dict] = []
    for e in edges:
        if e["kind"] == "soft_depends" and e["to_id"] not in resolved:
            risks.append(
                {
                    "from": titles.get(e["from_id"], e["from_id"]),
                    "on": titles.get(e["to_id"], e["to_id"]),
                }
            )
            continue
        if e["kind"] != "depends_on" or e["to_id"] in resolved:
            continue
        direct.setdefault(e["to_id"], []).append(e)

    def ancestors(node_id: str | None, acc: list[str]) -> list[str]:
        seen = set()
        while node_id:
            node_id = parents.get(node_id)
            if node_id and node_id not in seen and node_id not in acc:
                seen.add(node_id)
                acc.append(node_id)
        return acc

    blocked_by_id = {}  # task id -> blocker id(s)
    for blocker, deps in direct.items():
        for e in deps:
            blocked_by_id.setdefault(e["from_id"], []).append(blocker)

    summaries = []
    for blocker, deps in sorted(direct.items(), key=lambda kv: kv[0]):
        direct_ids = [e["from_id"] for e in deps]
        affected: list[str] = []
        for nid in direct_ids:
            ancestors(nid, affected)
        # chained: nodes depending on any directly blocked node
        by_target: dict[str, list[str]] = {}
        for e in edges:
            if e["kind"] == "depends_on" and e["to_id"] not in resolved:
                by_target.setdefault(e["to_id"], []).append(e["from_id"])
        frontier, seen = list(direct_ids), set()
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in by_target.get(cur, []):
                if nxt not in direct_ids and nxt not in affected:
                    affected.append(nxt)
                    frontier.append(nxt)
        summaries.append(
            {
                "decision": blocker,
                "title": titles.get(blocker, blocker),
                "directly_blocked": len(direct_ids),
                "direct_ids": sorted(direct_ids),
                "downstream_affected": sorted(set(affected) - set(direct_ids)),
                "shared": len(direct_ids) > 1,
            }
        )
    resolved_blockers = {s["decision"] for s in summaries}
    return {
        "blockers": summaries,
        "risks": risks,
        "unblocked": sorted(set(blocked_by_id) - resolved_blockers),
    }
