"""Email INBOX routes (python-native; the audited comms_email_read provider).

Owner-session read access. The capability path (`POST /api/v1/capabilities/
execute` with capability `email.check`) stays the agent-harness surface; these
routes are the human/UI surface. Zero LLM; no credentials ever in responses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from pixel_startup.api.office_compat import office_write_auth, require_auth

router = APIRouter()


def _inbox(request: Request):
    return request.app.state.container.providers.get("comms_email_read")


@router.get("/api/v1/email/accounts")
async def email_accounts(request: Request, _auth: str = Depends(require_auth)) -> Any:
    inbox = _inbox(request)
    if inbox is None:
        return JSONResponse({"error": "email reader not registered"}, status_code=503)
    return {"accounts": inbox.accounts()}


@router.get("/api/v1/email/check")
async def email_check(
    request: Request,
    account: str = "owner",
    limit: int = 10,
    _auth: str = Depends(require_auth),
) -> Any:
    inbox = _inbox(request)
    if inbox is None:
        return JSONResponse({"error": "email reader not registered"}, status_code=503)
    limit = max(1, min(int(limit), 50))
    payload = inbox.invoke("email.check", {"account_id": account, "limit": limit})
    if payload.get("result", {}).get("error"):
        return JSONResponse(payload, status_code=503)
    return payload


@router.get("/api/v1/email/message")
async def email_message(
    request: Request,
    account: str,
    message_id: str,
    _auth: str = Depends(office_write_auth),
) -> Any:
    inbox = _inbox(request)
    if inbox is None:
        return JSONResponse({"error": "email reader not registered"}, status_code=503)
    payload = inbox.invoke("email.read", {"account_id": account, "message_id": message_id})
    if payload.get("result", {}).get("error"):
        return JSONResponse(payload, status_code=503)
    return payload
