"""G30-D/E/F: living-startup graph API (native v1; read = auth, mutate = admin).

No LLM anywhere: layout, health and refresh are deterministic. The CHO mood
(curious/focused/driven) is a stored view preference on the office startup
config, surfaced here for the frontend ordering — not a separate model.
"""

from __future__ import annotations

from datetime import date
from hashlib import md5

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field

from pixel_startup.api.config_v1 import office_from_app, office_write_auth
from pixel_startup.graph.attention import attention, dependency_impact
from pixel_startup.graph.health import STATES, project_health
from pixel_startup.security.auth import require_auth

router = APIRouter()


def _graph(request: Request):
    store = request.app.state.container.providers.get("graph")
    if store is None:
        from pixel_startup.persistence.store import GraphStore

        store = GraphStore(request.app.state.container.providers["db"])
        request.app.state.container.providers["graph"] = store
    return store


def _mood(request: Request) -> str:
    office = office_from_app(request)
    mood = str((office.startup or {}).get("mood") or "focused").lower()
    return mood if mood in ("curious", "focused", "driven") else "focused"


def _etag(nodes: list[dict], scope: str, policy: str) -> str:
    # G32 (network): the etag summarizes STATE, not payload — same state, same
    # tag, so an unchanged graph costs a 304 instead of a full download.
    max_updated = max((n["updated_at"] for n in nodes), default="")
    seed = f"{scope}|{policy}|{len(nodes)}|{max_updated}"
    return 'W/"%s"' % md5(seed.encode()).hexdigest()[:16]


async def _snapshot_response(request: Request, scope: str, if_none_match: str | None):
    if scope not in ("company", "household"):
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="scope must be company or household")
    store = _graph(request)
    nodes, edges = store.list(scope)
    policy = _mood(request)  # G30-F: view preference, not a data model
    etag = _etag(nodes, scope, policy)
    if if_none_match and etag in if_none_match:
        return Response(status_code=304, headers={"ETag": etag})
    projection = project_health(nodes, edges, date.today())
    from fastapi.responses import JSONResponse

    return JSONResponse(
        {
            "scope": scope,
            "policy": policy,
            "states": list(STATES),
            "nodes": [
                {
                    **n,
                    "views": projection["nodes"].get(n["id"]),
                    "attention": attention(n, date.today()),
                }
                for n in nodes
            ],
            "edges": edges,
        },
        headers={"ETag": etag},
    )


@router.get("/api/v1/graph/impact")
async def get_impact(
    request: Request, scope: str = "company", _auth: str = Depends(require_auth)
) -> dict:
    """G31-D: shared blockers surfaced once, with distinct blocked/affected/risks."""
    store = _graph(request)
    nodes, edges = store.list(scope)
    return dependency_impact(nodes, edges, date.today())


@router.get("/api/v1/graph")
async def get_graph(
    request: Request,
    scope: str = "company",
    if_none_match: str | None = Header(None),
    _auth: str = Depends(require_auth),
):
    return await _snapshot_response(request, scope, if_none_match)


class GraphNodeIn(BaseModel):
    kind: str = Field(..., description="typed org node kind (see graph KIND_PARENTS)")
    title: str
    parent_id: str | None = None
    priority: int | None = None
    due_at: str | None = None
    cadence: str | None = None
    detail: str | None = None
    owner_role: str | None = Field(None, description="cho|employee|client|vendor|external")
    scope: str | None = Field(None, description="company (default) | household")


class GraphEdgeIn(BaseModel):
    from_id: str
    to_id: str
    kind: str


class GraphPatchIn(BaseModel):
    title: str | None = None
    status: str | None = None
    priority: int | None = None
    due_at: str | None = None
    cadence: str | None = None
    detail: str | None = None
    verified: bool | None = None


@router.post("/api/v1/graph/nodes", status_code=201)
async def create_node(
    payload: GraphNodeIn, request: Request, _auth: str = Depends(office_write_auth)
) -> dict:
    try:
        return _graph(request).create(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/api/v1/graph/nodes/{node_id}")
async def patch_node(
    node_id: str, payload: GraphPatchIn, request: Request, _auth: str = Depends(office_write_auth)
) -> dict:
    try:
        return _graph(request).patch(node_id, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/graph/edges", status_code=201)
async def create_edge(
    payload: GraphEdgeIn, request: Request, _auth: str = Depends(office_write_auth)
) -> dict:
    try:
        return _graph(request).add_edge(payload.from_id, payload.to_id, payload.kind)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/api/v1/graph/edges/{edge_id}")
async def delete_edge(
    edge_id: str, request: Request, _auth: str = Depends(office_write_auth)
) -> dict:
    from fastapi import HTTPException

    if not _graph(request).remove_edge(edge_id):
        raise HTTPException(status_code=404, detail="edge not found")
    return {"removed": edge_id}
