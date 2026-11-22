"""G30-D/E/F: living-startup graph API (native v1; read = auth, mutate = admin).

No LLM anywhere: layout, health and refresh are deterministic. The CHO mood
(curious/focused/driven) is a stored view preference on the office startup
config, surfaced here for the frontend ordering — not a separate model.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from pixel_startup.api.config_v1 import office_from_app, office_write_auth
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


def _snapshot(request: Request, scope: str) -> dict:
    if scope not in ("company", "household"):
        raise ValueError("scope must be company or household")
    store = _graph(request)
    nodes, edges = store.list(scope)
    projection = project_health(nodes, edges, date.today())
    return {
        "scope": scope,
        "policy": _mood(request),  # G30-F: view preference, not a data model
        "states": list(STATES),
        "nodes": [{**n, "views": projection["nodes"].get(n["id"])} for n in nodes],
        "edges": edges,
    }


@router.get("/api/v1/graph")
async def get_graph(
    request: Request, scope: str = "company", _auth: str = Depends(require_auth)
) -> dict:
    return _snapshot(request, scope)


class GraphNodeIn(BaseModel):
    kind: str = Field(..., description="goal|idea|project|milestone|task")
    title: str
    parent_id: str | None = None
    priority: int | None = None
    due_at: str | None = None
    cadence: str | None = None
    detail: str | None = None
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
