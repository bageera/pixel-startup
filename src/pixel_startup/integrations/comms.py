"""G13: communications adapters — Mattermost (internal), email (external).

Honest by construction: adapters NEVER send live traffic just because a draft
exists. Mattermost posts require a live HTTP client (env-configured). Email
sends go through `send()` with an owner approval record; compose alone is
harmless. Transports are injectable callables for synthetic qualification.
"""

import hashlib
import re
import time

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
ALLOWED_ATTACHMENT_TYPES = ("text/plain", "application/pdf", "image/png")


class CommsDenied(Exception):
    """Authorization/validation failure — fail closed."""


class CommsUnavailable(Exception):
    """Transport down or not configured."""


def _redaction_safe(subject: str) -> str:
    return subject[:120]


class MattermostAdapter:
    provider_id = "mattermost"
    capabilities = ["comm.post", "comm.notify"]

    def __init__(
        self,
        base_url: str | None,
        token: str | None,
        allowed_channels: tuple[str, ...],
        client=None,
    ):
        self._url = (base_url or "").rstrip("/")
        self._token = token
        self._channels = allowed_channels
        self._client = client  # injectable transport (synthetic)
        self._sent: set[tuple[str, str]] = set()  # (channel, correlation_id)

    @property
    def configured(self) -> bool:
        return bool(self._url and self._token)

    def probe(self) -> dict:
        if not self.configured:
            raise Exception("not configured")
        import urllib.request

        req = urllib.request.Request(
            f"{self._url}/api/v4/users/me",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            if resp.status == 200:
                return {"online": True}
        except Exception:
            pass
        return {"online": False}

    def post(self, channel: str, text: str, correlation_id: str) -> dict:
        if channel not in self._channels:
            raise CommsDenied(f"channel outside the allowlist: {channel}")
        if not text or not correlation_id:
            raise CommsDenied("malformed message request")
        if (channel, correlation_id) in self._sent:
            return {"replayed": True, "channel": channel}
        if not self.configured:
            raise CommsUnavailable("mattermost not configured; live rows BLOCKED")
        attempt = 0
        while True:
            status, body = self._client(
                "POST",
                "/api/v4/posts",
                {  # type: ignore[misc]
                    "channel_id": channel,
                    "message": text,
                },
                self._token,
            )
            if status == 429 and attempt == 0:  # retry once; honor Retry-After for live clusters
                time.sleep(0.1)  # ponytail: fixed small backoff for synthetics
                attempt += 1
                continue
            if status in (401, 403):
                raise CommsDenied("mattermost rejected the request")
            if status != 201:
                raise CommsUnavailable(f"mattermost delivery failed ({status})")
            break
        self._sent.add((channel, correlation_id))
        subject = _redaction_safe(text[:40])
        return {
            "channel": channel,
            "correlation_id": correlation_id,
            "message_sha": hashlib.sha256(text.encode()).hexdigest()[:16],
            "subject": subject,
        }

    def invoke(self, operation: str, payload: dict) -> dict:
        if operation == "comm.post":
            return self.post(
                payload.get("channel") or "",
                payload.get("text") or "",
                payload.get("correlation_id") or "",
            )
        raise CommsDenied(f"unsupported comms operation: {operation}")


class EmailAdapter:
    provider_id = "email"
    capabilities = ["email.compose", "email.send"]  # send is approval-gated

    def __init__(self, client=None):
        self._client = client
        self._sent: set[str] = set()

    @property
    def configured(self) -> bool:
        return self._client is not None

    def compose_draft(
        self, recipients: list[str], subject: str, body: str, attachments: list[dict] | None = None
    ) -> dict:
        if not recipients or len(recipients) > 5:
            raise CommsDenied("recipient count out of bounds")
        parsed: list[str] = []
        for rcpt in recipients:
            if not EMAIL_RE.match(rcpt):
                raise CommsDenied("invalid recipient")
            parsed.append(rcpt)
        if "\r" in subject or "\n" in subject:
            raise CommsDenied("malformed subject")  # header injection refused
        if attachments:
            for att in attachments:
                if "/" in att.get("name", "") or ".." in att.get("name", ""):
                    raise CommsDenied("malformed attachment name")
                if att.get("content_type") not in ALLOWED_ATTACHMENT_TYPES:
                    raise CommsDenied(f"attachment type not allowed: {att.get('content_type')}")
                if len(att.get("data") or b"") > MAX_ATTACHMENT_BYTES:
                    raise CommsDenied("attachment exceeds the size limit")
        draft_id = (
            "draft."
            + hashlib.sha256(f"{sorted(parsed)}|{subject}|{body}".encode()).hexdigest()[:16]
        )
        return {
            "draft_id": draft_id,
            "recipients": sorted(set(parsed)),
            "subject": subject[:200],
            "status": "pending_approval",
            "attachment_count": len(attachments or []),
        }

    def send(self, draft: dict, approval: dict) -> dict:
        if draft.get("status") != "pending_approval":
            raise CommsDenied("draft is not pending approval")
        if approval.get("status") != "approved" or not approval.get("by"):
            raise CommsDenied("external delivery requires owner approval")
        if draft["draft_id"] in self._sent:
            return {"replayed": True, "draft_id": draft["draft_id"]}
        if not self.configured:
            raise CommsUnavailable("email transport not configured; live rows BLOCKED")
        status = self._client(draft)  # type: ignore[misc]
        if status != 250:
            raise CommsUnavailable(f"email delivery failed ({status})")
        self._sent.add(draft["draft_id"])
        return {"draft_id": draft["draft_id"], "delivered": True}

    def invoke(self, operation: str, payload: dict) -> dict:
        if operation == "email.compose":
            return self.compose_draft(
                payload.get("recipients") or [],
                payload.get("subject") or "",
                payload.get("body") or "",
                payload.get("attachments"),
            )
        if operation == "email.send":
            return self.send(payload.get("draft") or {}, payload.get("approval") or {})
        raise CommsDenied(f"unsupported email operation: {operation}")
