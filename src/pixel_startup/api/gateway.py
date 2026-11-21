"""G20 cut-over gateway: Python is THE public backend; node is deprecated.

Strangler pattern — every route not implemented natively in this app is caught
here (registered LAST so native routes always win) and either:
  - proxied to the deprecated node upstream (NODE_UPSTREAM), preserving
    sessions (cookie passthrough incl. Set-Cookie from /api/auth/login) and
    status codes, or
  - refused with an honest 501 when no upstream is configured.
Hardened node routes (/server.js, /.env, /package.json — G2) never proxy.
"""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from pixel_startup.config import Settings

router = APIRouter()

# G2 hardening: these node files must keep 404ing, never leak through the proxy
_BLOCKED = ("/server.js", "/.env", "/package.json")
_DEPRECATED_501_BODY = {"error": "route not migrated to the python backend", "deprecated": "node"}


def _upstream(request: Request) -> Settings:
    return request.app.state.settings


def _make_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


async def _proxy(request: Request, path: str) -> Response:
    settings = _upstream(request)
    target = (settings.node_upstream or "").rstrip("/")
    if not target:
        return JSONResponse(_DEPRECATED_501_BODY, status_code=501)
    url = f"{target}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "connection", "transfer-encoding")
    }
    timeout = settings.node_upstream_timeout_seconds
    try:
        async with _make_client(timeout) as client:
            upstream = await client.request(
                request.method, url, headers=headers,
                content=await request.body()
                if request.method in ("POST", "PUT", "PATCH") else None,
            )
    except httpx.HTTPError:
        return JSONResponse(
            {"error": "deprecated node upstream unreachable", "deprecated": "node"},
            status_code=502,
        )
    passthrough = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() in ("set-cookie", "www-authenticate")
    }
    return Response(
        content=upstream.content, status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"), headers=passthrough,
    )


async def _guard(request: Request, path: str) -> Response | None:
    stripped = "/" + path.strip("/")
    if any(stripped == b or stripped.startswith(b + "/") for b in _BLOCKED):
        return Response(status_code=404)
    return None


@router.get("/{path:path}", include_in_schema=False)
@router.post("/{path:path}", include_in_schema=False)
@router.put("/{path:path}", include_in_schema=False)
@router.patch("/{path:path}", include_in_schema=False)
@router.delete("/{path:path}", include_in_schema=False)
async def catch_all(request: Request, path: str) -> Response:
    blocked = await _guard(request, path)
    if blocked is not None:
        return blocked
    return await _proxy(request, path)
