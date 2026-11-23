"""G10 HTTP surface: POST /api/v1/capabilities/execute.

workload authentication reuses require_auth evidence: bearer = automation
workload (the executor re-verifies the token), session cookie = owner workload.
Identity proof comes from the verified runtime binding chain (G9), never from
the request body. Denials map to a generic 403 — nothing sensitive is echoed.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from pixel_startup.capabilities import federation
from pixel_startup.capabilities.delegation import DelegationFailure
from pixel_startup.capabilities.executor import CapabilityExecutor
from pixel_startup.capabilities.registry import CapabilityDenied
from pixel_startup.providers.registry import ProviderError
from pixel_startup.security.auth import require_auth

router = APIRouter()


@router.get("/api/v1/delegate/{task_id}/plan")
async def delegation_plan(
    task_id: str,
    request: Request,
    auth_method: str = Depends(require_auth),
):
    """G23-3a: fetch-or-generate the execution plan; approval pends meanwhile."""
    _ = auth_method
    flow = request.app.state.container.providers.get("delegation_flow")
    if flow is None:
        raise HTTPException(status_code=503, detail="delegation service unavailable")
    try:
        info = flow["plan_for"](task_id)
    except DelegationFailure as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    # Mattermost, when configured, is the NOTIFICATION surface; the approval
    # decision itself is the approvals-row record (never channel text).
    notified = {"notified": False, "reason": "mattermost not configured"}
    try:
        provider = request.app.state.container.registry.get("mattermost")
    except ProviderError:
        provider = None
    if provider is not None and provider.handler is not None and provider.handler.configured:
        try:
            provider.handler.invoke(
                "comm.post",
                {
                    "channel": (provider.handler.channels or [""])[0],
                    "text": f"Plan review requested: {info['task_id']} — approve in the office.",
                    "correlation_id": f"plan.{info['task_id']}",
                },
            )
            notified = {"notified": True}
        except Exception as exc:
            notified = {"notified": False, "reason": f"notify failed: {type(exc).__name__}"}
    return {**info, "mattermost": notified}


@router.post("/api/v1/delegate/{task_id}/review")
async def delegation_review(
    task_id: str,
    request: Request,
    auth_method: str = Depends(require_auth),
):
    """G23-3a: owner decides. The approvals-row record is the ONLY authorization."""
    _ = auth_method
    body = await request.json()
    decision = body.get("decision") if isinstance(body, dict) else None
    flow = request.app.state.container.providers.get("delegation_flow")
    if flow is None:
        raise HTTPException(status_code=503, detail="delegation service unavailable")
    decided_by = "owner.session"
    try:
        return flow["review"](task_id, decision, decided_by)
    except DelegationFailure as exc:
        detail = str(exc)
        raise HTTPException(
            status_code=404 if "unknown task" in detail else 409, detail=detail
        ) from exc


@router.get("/api/v1/delegate/{task_id}/result")
async def delegation_result(
    task_id: str,
    request: Request,
    auth_method: str = Depends(require_auth),
) -> dict:
    _ = auth_method  # session owner OR automation bearer; identity already proven
    retriever = request.app.state.container.providers.get("delegation_results")
    if retriever is None:
        raise HTTPException(status_code=503, detail="delegation service unavailable")
    try:
        return retriever(task_id)
    except DelegationFailure as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@router.post("/api/v1/federation/events")
async def federation_events(request: Request, auth_method: str = Depends(require_auth)):
    """Harness → organization: sanitized event (id, capability, summary).

    Idempotent by event id — retries replay, never re-dispatch."""
    _ = auth_method
    body = await request.json()
    ingest = request.app.state.container.providers["federation_ingest"]
    try:
        return ingest(body if isinstance(body, dict) else {})
    except DelegationFailure as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/api/v1/federation/manifest")
async def federation_manifest(_auth_method: str = Depends(require_auth)) -> dict:
    """Published capability manifest: one capability, one execution authority."""
    return {"manifest": federation.MANIFEST}


@router.post("/api/v1/capabilities/execute")
async def execute_capability(request: Request, auth_method: str = Depends(require_auth)):
    body = await request.json()
    if not isinstance(body, dict):
        body = {}
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    request_body = {
        "binding_id": body.get("binding_id"),
        "capability": body.get("capability"),
        "request_key": body.get("request_key"),
        "payload": payload,
        "scope": body.get("scope"),
        "employee_id": body.get("employee_id"),
    }
    executor: CapabilityExecutor = request.app.state.container.providers["executor"]
    request_body["pre_authenticated_workload"] = (
        "workload.owner" if auth_method == "session" else None
    )
    if auth_method != "session":
        header = request.headers.get("authorization", "")
        request_body["workload_token"] = header[len("Bearer ") :]
    try:
        return executor.execute(request_body)
    except CapabilityDenied:
        raise HTTPException(status_code=403, detail="capability execution denied") from None
    except DelegationFailure as exc:
        # refusal at the delegation boundary is client-side (missing target/
        # objective, unknown worker), not a server fault — 400, honest detail.
        raise HTTPException(status_code=400, detail=str(exc)) from None
