"""Health/readiness + the first protected /api/v1 route."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from pixel_startup.security.auth import require_auth

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness: public, always 200 (office viewer parity with G2 surface)."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict:
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(status_code=503, detail="starting")
    response.headers["cache-control"] = "no-store"
    return {"status": "ready"}


@router.get("/api/v1/health/services")
async def health_services(request: Request, auth_method: str = Depends(require_auth)) -> dict:
    """Per-service connectivity as the provider registry actually recorded it.

    # ponytail: registry truth + only cheap file probes (handlers with .probe,
    today just the Harness adapter) — no network fan-out per call; chromadb /
    minio / litellm report boot-time qualification (health() ran at boot).
    Mattermost/email stay candidate + unconnected until a live transport is
    wired (B3) — an endpoint alone is configuration, not a connection.
    """
    container = request.app.state.container
    services = []
    for reg in sorted(container.registry.all(), key=lambda r: r.provider_id):
        entry = {
            "id": reg.provider_id,
            "category": reg.category.value,
            "state": reg.state.value,
            "configured": reg.endpoint is not None,
            "connected": reg.state.value in ("qualified", "active"),
            "observed": None,
        }
        handler = getattr(reg, "handler", None)
        if handler is not None and hasattr(handler, "probe"):
            try:
                entry["observed"] = handler.probe()
            except Exception:
                entry["observed"] = None  # probe failure is honest, never faked
        services.append(entry)
    return {"services": services}


@router.get("/api/v1/ping")
async def ping(auth_method: str = Depends(require_auth)) -> dict:
    """First protected route: proves the G2-style auth dependency end to end."""
    return {"pong": True, "auth": auth_method}
