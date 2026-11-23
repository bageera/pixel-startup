"""Email INBOX reading for the office (parity with the node `email.js` path).

The node office reads mail by running an imaplib script ON the mail host over
SSH (the IMAP service is only reachable there). This adapter keeps that exact
transport contract for python, so agents (and the office UI) can check mail
through the audited capability layer instead of the legacy node proxy:

- `email.check` — latest N messages of a folder for one account (headers only).
- `email.read`  — one message body by IMAP id (read-only, never marks seen).

Accounts/passwords come from the SAME env contract as the node path:
EMAIL_DOMAIN (default example.com), EMAIL_USER/EMAIL_PASSWORD for the owner
row, EMAIL_PASS_<ID> for the rest (node parity key names preserved).
Transports, in honest order: direct IMAP when EMAIL_IMAP_HOST is set; SSH exec
when MAIL2_HOST/MAIL2_USER/SSH_KEY_PATH are set; otherwise honest offline.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Callable

UNSUPPORTED_OPERATIONS = ("email.send",)  # sending stays in the G13 comms adapter

# node `email.js` parity: every id keeps its env password key + demo identity.
_ACCOUNTS: tuple[dict, ...] = (
    {"id": "owner", "category": "owner", "env_key": "EMAIL_PASSWORD"},
    {"id": "investments", "category": "owner", "env_key": "EMAIL_2_PASSWORD"},
    {"id": "operations", "category": "operations", "env_key": "EMAIL_PASS_JON"},
    {"id": "accounts", "category": "operations", "env_key": "EMAIL_PASS_ACCOUNTS"},
    {"id": "contact", "category": "operations", "env_key": "EMAIL_PASS_CONTACT"},
    {"id": "helpdesk", "category": "support", "env_key": "EMAIL_PASS_HELPDESK"},
    {"id": "notify", "category": "notifications", "env_key": "EMAIL_PASS_NIGHTCHUTE"},
    {"id": "notification", "category": "notifications", "env_key": "EMAIL_PASS_NOTIFICATION"},
    {"id": "openproject", "category": "services", "env_key": "EMAIL_PASS_OPENPROJECT"},
    {"id": "filtering", "category": "filtering", "env_key": "EMAIL_PASS_NOCTURNALPOST"},
)

_LIST_SCRIPT = """import imaplib, email, json, os
from email.header import decode_header

def decode_str(s):
    if s is None:
        return ""
    parts = decode_header(s)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)

try:
    m = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.environ["IMAP_PORT"]))
    m.login(os.environ["EMAIL_USER"], os.environ["EMAIL_PASS"])
    m.select('"INBOX"', readonly=True)
    status, data = m.search(None, "ALL")
    if status != "OK":
        print(json.dumps({"error": "search failed"}))
        raise SystemExit
    ids = data[0].split()
    ids.reverse()
    ids = ids[: int(os.environ["LIMIT"])]
    emails = []
    for mid in ids:
        status, msg_data = m.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] FLAGS)")
        if status != "OK":
            continue
        raw = msg_data[0][1] if msg_data and msg_data[0] else b""
        msg = email.message_from_bytes(raw)
        flags = ""
        if msg_data and msg_data[0] and isinstance(msg_data[0], tuple):
            flags = str(msg_data[0][0])
        emails.append({
            "id": mid.decode(),
            "from": decode_str(msg.get("From", "")),
            "subject": decode_str(msg.get("Subject", "")),
            "date": msg.get("Date", ""),
            "seen": "\\\\Seen" in flags,
        })
    m.logout()
    result = {
        "emails": emails, "folder": "INBOX", "count": len(emails),
        "account": os.environ["EMAIL_USER"],
    }
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({"error": str(e)}))
"""

_READ_SCRIPT = """import imaplib, email, json, os

try:
    m = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.environ["IMAP_PORT"]))
    m.login(os.environ["EMAIL_USER"], os.environ["EMAIL_PASS"])
    m.select('"INBOX"', readonly=True)
    status, msg_data = m.fetch(os.environ["MESSAGE_ID"], "(BODY.PEEK[])")
    if status != "OK":
        print(json.dumps({"error": "fetch failed"}))
        raise SystemExit
    raw = msg_data[0][1] if msg_data and msg_data[0] else b""
    msg = email.message_from_bytes(raw)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and body == "":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="replace")
    m.logout()
    print(json.dumps({
        "id": os.environ["MESSAGE_ID"], "subject": msg.get("Subject", ""),
        "from": msg.get("From", ""), "date": msg.get("Date", ""), "body": body,
    }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
"""


def _ssh_runner(script: str, env_vars: dict[str, str]) -> str:
    host = os.environ.get("MAIL2_HOST") or ""
    user = os.environ.get("MAIL2_USER") or ""
    key = os.environ.get("SSH_KEY_PATH") or ""
    if not (host and user and key):
        return json.dumps({"error": "mail transport not configured"})
    prefix = (
        " ".join(f"{k}={json.dumps(str(v))}" for k, v in env_vars.items() if v not in (None, ""))
        + " "
    )
    cmd = [
        "ssh",
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{user}@{host}",
        f"{prefix}python3 -",
    ]
    proc = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=30)
    proc.check_returncode()
    return proc.stdout


def _imap_runner(host: str, port: str, script: str, env_vars: dict[str, str]) -> str:
    import email as email_mod
    import imaplib
    from email.header import decode_header

    def encode(s: str) -> str:
        return "".join(
            p.decode(e or "utf-8", errors="replace") if isinstance(p, bytes) else p
            for p, e in decode_header(s or "")
        )

    conn = imaplib.IMAP4_SSL(host, int(port))
    try:
        conn.login(env_vars["EMAIL_USER"], env_vars["EMAIL_PASS"])
        conn.select('"INBOX"', readonly=True)
        if "MESSAGE_ID" in env_vars:
            status, data = conn.fetch(env_vars["MESSAGE_ID"], "(BODY.PEEK[])")
            if status != "OK":
                return json.dumps({"error": "fetch failed"})
            entry = data[0] if data else None
            raw = entry[1] if isinstance(entry, tuple) else (entry or b"")
            raw = raw if isinstance(raw, bytes) else b""
            msg = email_mod.message_from_bytes(raw)
            body = ""
            walker = msg.walk() if msg.is_multipart() else [msg]
            for part in walker:
                if part.get_content_type() == "text/plain" and not body:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = (
                            payload.decode("utf-8", errors="replace")
                            if isinstance(payload, bytes)
                            else str(payload)
                        )
            return json.dumps(
                {
                    "id": env_vars["MESSAGE_ID"],
                    "subject": msg.get("Subject", ""),
                    "from": msg.get("From", ""),
                    "date": msg.get("Date", ""),
                    "body": body,
                }
            )
        status, data = conn.search(None, "ALL")
        if status != "OK":
            return json.dumps({"error": "search failed"})
        ids = list(reversed(data[0].split()))[: int(env_vars["LIMIT"])]
        emails = []
        for mid in ids:
            status, msg_data = conn.fetch(
                mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] FLAGS)"
            )
            if status != "OK":
                continue
            entry = msg_data[0] if msg_data else None
            raw = entry[1] if isinstance(entry, tuple) else (entry or b"")
            raw = raw if isinstance(raw, bytes) else b""
            msg = email_mod.message_from_bytes(raw)
            flags = str(entry[0]) if isinstance(entry, tuple) else ""
            emails.append(
                {
                    "id": mid.decode(),
                    "from": encode(msg.get("From", "")),
                    "subject": encode(msg.get("Subject", "")),
                    "date": msg.get("Date", ""),
                    "seen": "\\Seen" in flags,
                }
            )
        return json.dumps(
            {
                "emails": emails,
                "folder": "INBOX",
                "count": len(emails),
                "account": env_vars["EMAIL_USER"],
            }
        )
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001 - logout is best-effort
            pass


class EmailInboxAdapter:
    """G5 handler contract: invoke(operation, payload) -> dict."""

    provider_id = "comms_email_read"
    category = "comms"
    implementation = "python"

    @property
    def capabilities(self) -> list[str]:
        return ["email.check", "email.read"]

    @property
    def configured(self) -> bool:
        imap_host = os.environ.get("EMAIL_IMAP_HOST") or ""
        ssh = bool(
            (os.environ.get("MAIL2_HOST") or "")
            and (os.environ.get("MAIL2_USER") or "")
            and (os.environ.get("SSH_KEY_PATH") or "")
        )
        return bool(imap_host) or ssh

    def _account(self, account_id: str) -> dict | None:
        meta = next((a for a in _ACCOUNTS if a["id"] == account_id), None)
        if meta is None or "." in account_id:
            return None
        domain = os.environ.get("EMAIL_DOMAIN") or "example.com"
        owner = os.environ.get("EMAIL_USER") or ""
        address = owner if (account_id == "owner" and owner) else f"{account_id}@{domain}"
        password = (
            os.environ.get(meta["env_key"]) or (os.environ.get("EMAIL_PASSWORD") or "")
            if account_id == "owner"
            else os.environ.get(meta["env_key"]) or ""
        )
        return {"id": account_id, "address": address, "password": password, **meta}

    def _transport(self) -> Callable[..., str]:
        """Honest transport ladder: direct IMAP when configured, else SSH
        (which returns an honest not-configured error json when env is absent)."""
        imap_host = os.environ.get("EMAIL_IMAP_HOST") or ""
        imap_port = os.environ.get("EMAIL_IMAP_PORT") or "993"
        if imap_host:
            return lambda script, env_vars: _imap_runner(imap_host, imap_port, script, env_vars)
        return _ssh_runner

    def check(self, account_id: str, limit: int = 10, folder: str = "INBOX") -> dict:
        account = self._account(account_id)
        if account is None:
            return {
                "supported": True,
                "kind": "email.check",
                "result": {"error": f"unknown account: {account_id}"},
            }
        if not account["password"]:
            return {
                "supported": True,
                "kind": "email.check",
                "result": {"error": f"account {account_id} not configured (missing password env)"},
                "checked_at": _now(),
            }
        env_vars = {
            "IMAP_HOST": os.environ.get("EMAIL_IMAP_HOST") or "127.0.0.1",
            "IMAP_PORT": os.environ.get("EMAIL_IMAP_PORT") or "993",
            "EMAIL_USER": account["address"],
            "EMAIL_PASS": account["password"],
            "LIMIT": str(int(limit)),
        }
        raw = self._transport()(_LIST_SCRIPT, env_vars)
        return {
            "supported": True,
            "kind": "email.check",
            "checked_at": _now(),
            "result": json.loads(raw),
            "transport": "imap" if os.environ.get("EMAIL_IMAP_HOST") else "ssh",
        }

    def read(self, account_id: str, message_id: str) -> dict:
        account = self._account(account_id)
        if account is None:
            return {
                "supported": True,
                "kind": "email.read",
                "result": {"error": f"unknown account: {account_id}"},
            }
        if not account["password"]:
            return {
                "supported": True,
                "kind": "email.read",
                "result": {"error": f"account {account_id} not configured (missing password env)"},
                "checked_at": _now(),
            }
        env_vars = {
            "IMAP_HOST": os.environ.get("EMAIL_IMAP_HOST") or "127.0.0.1",
            "IMAP_PORT": os.environ.get("EMAIL_IMAP_PORT") or "993",
            "EMAIL_USER": account["address"],
            "EMAIL_PASS": account["password"],
            "MESSAGE_ID": str(message_id),
        }
        raw = self._transport()(_READ_SCRIPT, env_vars)
        return {
            "supported": True,
            "kind": "email.read",
            "checked_at": _now(),
            "result": json.loads(raw),
            "transport": "imap" if os.environ.get("EMAIL_IMAP_HOST") else "ssh",
        }

    def accounts(self) -> list[dict]:
        """Summary for the UI/agents — no passwords, ever."""
        return [
            {
                "id": a["id"],
                "address": (self._account(a["id"]) or {}).get("address"),
                "category": a["category"],
                "configured": bool((self._account(a["id"]) or {}).get("password")),
            }
            for a in _ACCOUNTS
        ]

    def invoke(self, operation: str, payload: dict) -> dict:
        payload = payload or {}
        if operation == "email.check":
            return self.check(payload.get("account_id") or "owner", int(payload.get("limit") or 10))
        if operation == "email.read":
            mid = payload.get("message_id") or ""
            if not mid:
                return {
                    "supported": True,
                    "kind": "email.read",
                    "result": {"error": "message_id required"},
                }
            return self.read(payload.get("account_id") or "owner", mid)
        if operation in UNSUPPORTED_OPERATIONS:
            return {
                "supported": False,
                "operation": operation,
                "reason": "sending lives in the G13 comms email adapter (compose + owner approval)",
            }
        return {"supported": False, "operation": operation, "reason": "unknown operation"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
