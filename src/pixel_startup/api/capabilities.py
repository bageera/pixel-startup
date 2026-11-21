"""G10 HTTP surface: POST /api/v1/capabilities/execute.

workload authentication reuses require_auth evidence: bearer = automation
workload (the executor re-verifies the token), session cookie = owner workload.
Identity proof comes from the verified runtime binding chain (G9), never from
the request body. Denials map to a generic 403 — nothing sensitive is echoed.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from pixel_startup.capabilities.delegation import DelegationFailure
from pixel_startup.capabilities.executor import CapabilityExecutor
from pixel_startup.capabilities.registry import CapabilityDenied
from pixel_startup.security.auth import require_auth

router = APIRouter()


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
