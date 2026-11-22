"""G30-B: deterministic health projection.

Inputs are stored rows plus the injected `today` — nothing else. Every state
carries a human-readable explanation naming the driver. Contract:

- never infer commercial success: explanations cite verified milestones/tasks;
- ideas without an agreed schedule never decay (seed, by design);
- paused -> dormant, archived -> archived: neither deteriorates;
- done/verified -> completed: completed work never rots;
- overdue actionable work -> withering; blocked work names the actual dependency;
- cadence (owner-defined, e.g. "1/wk") becomes a behind-cadence check only after
  one week has elapsed.
"""

from __future__ import annotations

from datetime import date, datetime

STATES = (
    "seed",
    "growing",
    "stable",
    "thriving",
    "needs-attention",
    "withering",
    "blocked",
    "waiting-external",
    "dormant",
    "recovering",
    "completed",
    "archived",
    "unknown",
)

_WORST = {  # aggregate severity, used to bubble child states up to parents
    "withering": 5,
    "blocked": 5,
    "needs-attention": 4,
    "growing": 3,
    "stable": 3,
    "thriving": 2,
    "recovering": 2,
    "seed": 1,
    "waiting-external": 1,
    "dormant": 1,
    "unknown": 1,
    "completed": 0,
    "archived": 0,
}


def parse_cadence(text: str | None) -> float | None:
    """'1/wk' -> 1.0 (expected verified flow per week); anything else None."""
    try:
        return float(text.split("/wk")[0].strip()) if text and "/wk" in text else None
    except ValueError:
        return None


def _resumed_at(n: dict) -> date | None:
    import json as _json

    try:
        det = _json.loads(n.get("detail") or "{}")
        if isinstance(det, dict) and det.get("resumed_at"):
            return _d(det["resumed_at"])
    except (TypeError, ValueError):
        pass
    return None


def _d(text: str | None) -> date | None:
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def project_health(nodes: list[dict], edges: list[dict], today: date) -> dict:
    """Whole-graph projection -> {"nodes": {id: {state, progress, explanation}}}.

    Cycle-safe: a depends_on cycle never resolves, so the blocked side stays
    honestly blocked (the edge list shows the loop); no recursion blowup.
    """
    done_ids = {n["id"] for n in nodes if n["status"] == "done" or n.get("verified")}
    titles = {n["id"]: n["title"] for n in nodes}
    blockers: dict[str, list[str]] = {}
    for e in edges:
        if e["kind"] == "depends_on" and e["to_id"] not in done_ids:
            blockers.setdefault(e["from_id"], []).append(titles.get(e["to_id"], e["to_id"]))

    children: dict[str | None, list[dict]] = {}
    for n in nodes:
        children.setdefault(n.get("parent_id"), []).append(n)

    healths: dict[str, dict] = {}

    def _evidence(n: dict, kids: list[dict]) -> dict | None:
        """Blocked / overdue / cadence / ownership checks and leaf signals."""
        deps = blockers.get(n["id"])
        if deps:
            return {
                "state": "blocked",
                "progress": 0.0,
                "explanation": f"Blocked by: {', '.join(deps)} —"
                " cannot progress until it is done or verified.",
            }
        if kids or n["kind"] not in ("task", "milestone"):
            return None  # containers flow into child aggregation (or seed branch)
        overdue = n.get("due_at") and (_d(n["due_at"]) or today) < today
        if n.get("verified"):
            if n["status"] == "done":
                return {
                    "state": "completed",
                    "progress": 1.0,
                    "explanation": "Completed and verified — completed work never rots.",
                }
            return {
                "state": "thriving",
                "progress": 1.0,
                "explanation": "Verified milestone: proven progress, not an inference.",
            }
        if n["status"] == "done":
            return {
                "state": "completed",
                "progress": 1.0,
                "explanation": "Completed work never rots.",
            }
        cad = parse_cadence(n.get("cadence"))
        if overdue:
            return {
                "state": "withering",
                "progress": 0.0,
                "explanation": f"Overdue: actionable work with due {n['due_at']} — deteriorating"
                " until done, verified or paused.",
            }
        if cad is not None:
            return {
                "state": "needs-attention",
                "progress": 0.0,
                "explanation": f"Behind cadence: {n['cadence']} expected; none registered yet.",
            }
        if n.get("due_at"):
            return {
                "state": "growing",
                "progress": 0.0,
                "explanation": f"In progress, due {n['due_at']}.",
            }
        resumed = _resumed_at(n)
        if resumed and (today - resumed).days <= 14:
            return {
                "state": "recovering",
                "progress": 0.0,
                "explanation": f"Recovering: resumed {resumed.isoformat()} — back in active work.",
            }
        owner = n.get("owner_role")
        if owner in ("client", "vendor", "external"):
            return {
                "state": "waiting-external",
                "progress": 0.0,
                "explanation": f"Waiting externally ({owner}) — silence is not a failure.",
            }
        if owner in ("cho", "employee"):
            return {
                "state": "growing",
                "progress": 0.0,
                "explanation": "In progress (no deadline agreed — decays nothing).",
            }
        return {
            "state": "unknown",
            "progress": 0.0,
            "explanation": "Unknown: no deadline, cadence or ownership data — nothing is invented.",
        }

    def visit(n: dict) -> dict:
        if n["id"] in healths:
            return healths[n["id"]]
        healths[n["id"]] = {"state": "growing", "progress": 0.0, "explanation": "…"}
        out = _compute(n)
        healths[n["id"]] = out
        return out

    def _compute(n: dict) -> dict:
        if n["status"] == "archived":
            return {
                "state": "archived",
                "progress": 0.0,
                "explanation": "Archived: parked by owner decision; health pressure excluded.",
            }
        if n["status"] == "paused":
            return {
                "state": "dormant",
                "progress": 0.0,
                "explanation": "Dormant: paused — not deteriorating by design.",
            }
        kids = sorted(children.get(n["id"], []), key=lambda x: x["id"])
        pre = _evidence(n, kids)
        if pre is not None:
            return pre
        if not kids:
            if n["kind"] == "idea":
                return {
                    "state": "seed",
                    "progress": 0.0,
                    "explanation": "Seed: schedule-free — never rots; grows when work attaches.",
                }
            if n["kind"] == "goal":
                return {
                    "state": "seed",
                    "progress": 0.0,
                    "explanation": "Goal without projects yet.",
                }
            return {
                "state": "growing",
                "progress": 0.0,
                "explanation": "Planned: attach milestones or tasks to make progress countable.",
            }
        subs = [visit(k) for k in kids]
        n_done = sum(1 for s in subs if s["state"] == "completed")
        act = [s for k, s in zip(kids, subs) if k["status"] not in ("archived", "paused")]
        progress = round(
            sum(1.0 for s in act if s["state"] in ("completed", "thriving")) / max(len(act), 1), 2
        )
        if all(s["state"] == "completed" for s in subs):
            return {
                "state": "completed",
                "progress": 1.0,
                "explanation": f"Completed: all {len(subs)} children done or verified. Never rots.",
            }
        worst = max(act, key=lambda s: _WORST.get(s["state"], 0), default=None)
        if worst is None:
            return {
                "state": "dormant",
                "progress": 0.0,
                "explanation": "All children paused or archived — dormant, not deteriorating.",
            }
        if _WORST.get(worst["state"], 0) >= _WORST["needs-attention"]:
            return {
                "state": worst["state"],
                "progress": progress,
                "explanation": f"From child work: {worst['explanation']}",
            }
        cad = parse_cadence(n.get("cadence"))
        created = _d(n.get("created_at"))
        weeks = max(0, (today - created).days // 7) if created else 0
        if cad is not None and weeks >= 1 and progress < 0.75:
            return {
                "state": "needs-attention",
                "progress": progress,
                "explanation": f"Behind cadence: {n['cadence']} expected verified flow;"
                f" verified share {progress:.0%}.",
            }
        thriving = worst["state"] == "thriving" and progress >= 0.5
        if thriving:
            return {
                "state": "thriving",
                "progress": progress,
                "explanation": f"Thriving: verified progress {progress:.0%}"
                + (f" meets the {n['cadence']} cadence" if cad else "")
                + ".",
            }
        pressed = _WORST.get(worst["state"], 0) >= _WORST["needs-attention"] or (
            cad is not None and weeks >= 1 and progress < 0.75
        )
        if progress >= 0.5 and not pressed:
            return {
                "state": "stable",
                "progress": progress,
                "explanation": f"Stable: verified progress {progress:.0%}, no pressure.",
            }
        extra = f", {len(kids) - len(act)} paused" if len(kids) > len(act) else ""
        return {
            "state": "growing",
            "progress": progress,
            "explanation": f"{n_done}/{len(subs)} completed{extra}; latest: {worst['explanation']}",
        }

    for n in sorted(nodes, key=lambda x: x["id"]):
        visit(n)
    return {"nodes": healths}


def dimensions(nodes: list[dict], edges: list[dict], today: date) -> dict:
    """G31-E: three INDEPENDENT dimensions per node — never collapsed into one
    score. execution = the health projection above (progress per commitments);
    attention = from the responsibility model (required / waiting / clear);
    outcome = EVIDENCE, the only accepted proof of business result: 'seed'
    (nothing completed), 'unproven' (completions without recorded outcomes —
    completing a task does not prove a positive outcome), 'evidenced' (>=1
    completion carries an evidence edge to an outcome record — negative
    results count as evidence, never as failure)."""
    from pixel_startup.graph.attention import attention as _att

    proj = project_health(nodes, edges, today)["nodes"]
    kids: dict[str | None, list[dict]] = {}
    for n in nodes:
        kids.setdefault(n.get("parent_id"), []).append(n)

    def descendants(node_id: str) -> list[dict]:
        out, stack = [], [node_id]
        while stack:
            cur = stack.pop()
            for k in kids.get(cur, []):
                if k["id"] != node_id:
                    out.append(k)
                    stack.append(k["id"])
        return out

    by_from: dict[str, bool] = {}  # completed node -> evidence edge exists (either direction)
    for e in edges:
        if e["kind"] == "evidence":
            by_from[e["from_id"]] = True
            by_from[e["to_id"]] = True

    out = {}
    for n in nodes:
        health = proj[n["id"]]
        act = _att(n, today)
        att = (
            "required"
            if act["requires_action"]
            else ("waiting-external" if act["state"] == "waiting" else "clear")
        )
        kids_here = descendants(n["id"]) + [n]
        if att == "clear":
            for k in kids_here:
                ka = _att(k, today)
                if ka["requires_action"]:
                    att = "required"
                    break
        done = [k for k in descendants(n["id"]) if k["status"] == "done" or k.get("verified")]
        if not done and (n["status"] not in ("done",) and not n.get("verified")):
            outcome = "seed"
        elif any(by_from.get(k["id"]) for k in done):
            outcome = "evidenced"
        else:
            outcome = "unproven"
        if n["id"] in by_from:
            outcome = "evidenced"
        out[n["id"]] = {
            "execution": health["state"],
            "attention": att,
            "outcome": outcome,
        }
    return out
