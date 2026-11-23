"""Email INBOX reading (comms_email_read) + OpenClaw execution surface.

Email: the harness-facing capability. Transports are injected (imap/ssh
runners are monkeypatched at module level); the adapter must read over the
SAME env contract as the node `email.js` path and fail honestly when no
transport/account is configured. No credentials ever appear in results.
OpenClaw: task.submit executes one chat-completion round on the gateway's
documented OpenAI-compatible surface; result comes back stored, offline
refuses honestly.
"""

from __future__ import annotations

import json

import httpx

from pixel_startup.harnesses.openclaw import WS_ONLY_OPERATIONS, OpenClawGatewayAdapter
from pixel_startup.integrations import email_read
from pixel_startup.integrations.email_read import EmailInboxAdapter

# === email.check / email.read ================================================


def test_email_check_over_ssh_transport(monkeypatch):
    monkeypatch.setenv("EMAIL_DOMAIN", "example.com")
    monkeypatch.setenv("EMAIL_PASS_ACCOUNTS", "pw-demo")
    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    seen = {}

    def fake_ssh(script, env_vars):
        seen.update(env_vars)
        assert "IMAP4_SSL" in script  # the real imaplib script ships to the mail host
        return json.dumps(
            {
                "emails": [
                    {
                        "id": "9",
                        "from": "client@corp.example",
                        "subject": "Invoice",
                        "date": "Mon",
                        "seen": False,
                    }
                ],
                "folder": "INBOX",
                "count": 1,
                "account": env_vars["EMAIL_USER"],
            }
        )

    monkeypatch.setattr(email_read, "_ssh_runner", fake_ssh)
    adapter = EmailInboxAdapter()
    assert adapter.accounts()[2]["configured"] is False  # operations: no env password
    ops = next(a for a in adapter.accounts() if a["id"] == "accounts")
    assert ops["configured"] is True and ops["address"] == "accounts@example.com"

    out = adapter.invoke("email.check", {"account_id": "accounts", "limit": 5})
    assert out["supported"] is True and out["kind"] == "email.check" and out["transport"] == "ssh"
    assert out["result"]["count"] == 1 and out["result"]["emails"][0]["subject"] == "Invoice"
    assert seen["EMAIL_USER"] == "accounts@example.com" and seen["EMAIL_PASS"] == "pw-demo"
    assert "pw" not in out["result"]


def test_email_check_direct_imap_transport(monkeypatch):
    monkeypatch.setenv("EMAIL_IMAP_HOST", "mail.internal")
    monkeypatch.setenv("EMAIL_IMAP_PORT", "1993")
    monkeypatch.setenv("EMAIL_USER", "owner@corp.internal")
    monkeypatch.setenv("EMAIL_PASSWORD", "ownerpw")

    def fake_imap(host, port, script, env_vars):
        assert (host, port) == ("mail.internal", "1993")
        assert env_vars["EMAIL_USER"] == "owner@corp.internal"
        return json.dumps(
            {"emails": [], "folder": "INBOX", "count": 0, "account": env_vars["EMAIL_USER"]}
        )

    monkeypatch.setattr(email_read, "_imap_runner", fake_imap)
    out = EmailInboxAdapter().invoke("email.check", {"account_id": "owner"})
    assert out["transport"] == "imap"
    assert out["result"]["count"] == 0


def test_email_read_body_and_unconfigured_are_honest(monkeypatch):
    monkeypatch.setenv("EMAIL_IMAP_HOST", "mail.internal")

    def fake_imap(host, port, script, env_vars):
        assert env_vars["MESSAGE_ID"] == "9"
        return json.dumps({"id": "9", "body": "The invoice is attached.", "subject": "Invoice"})

    monkeypatch.setattr(email_read, "_imap_runner", fake_imap)
    monkeypatch.setenv("EMAIL_PASS_ACCOUNTS", "pw")
    adapter = EmailInboxAdapter()
    body = adapter.invoke("email.read", {"account_id": "accounts", "message_id": "9"})
    assert body["result"]["body"] == "The invoice is attached."

    unconfigured = adapter.invoke("email.check", {"account_id": "notify"})
    assert unconfigured["result"]["error"].startswith("account notify not configured")
    unknown = adapter.invoke("email.read", {"account_id": "ghost", "message_id": "1"})
    assert unknown["result"]["error"] == "unknown account: ghost"
    send = adapter.invoke("email.send", {})
    assert send["supported"] is False  # sending stays in the G13 comms adapter


def test_email_provider_registered_with_capabilities():
    import atexit
    import shutil
    import tempfile

    import anyio

    from pixel_startup.config import Settings
    from pixel_startup.main import create_app

    data_dir = tempfile.mkdtemp(prefix="email-prov-")
    atexit.register(shutil.rmtree, data_dir, ignore_errors=True)
    app = create_app(
        Settings(pixel_data_dir=data_dir, dashboard_password="pw", pixel_api_token="tk")
    )

    async def drive():
        async with app.router.lifespan_context(app):  # registers the providers
            reg = app.state.container.registry
            registration = reg.get("comms_email_read")
            assert registration is not None
            assert (
                "email.check" in registration.capabilities
                and "email.read" in registration.capabilities
            )
            assert registration.implementation == "python"

    anyio.run(drive)


# === OpenClaw task surface ====================================================


def _gw(offline: bool = False) -> httpx.MockTransport:
    def mock(request: httpx.Request) -> httpx.Response:
        if offline:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path.endswith("/v1/chat/completions"):
            body = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "model": body.get("model"),
                    "choices": [{"message": {"content": f"ack: {body['messages'][0]['content']}"}}],
                },
            )
        return httpx.Response(200, json={"data": [{"id": "model.one"}]})

    return httpx.MockTransport(mock)


def test_openclaw_task_submits_through_chat_completions():
    adapter = OpenClawGatewayAdapter(gateway_url="http://gw.test", transport=_gw(), timeout=0.5)
    submit = adapter.invoke("task.submit", {"task_id": "t-1", "objective": "Summarize the inbox"})
    assert submit["supported"] is True and submit["kind"] == "execution"
    assert submit["result"]["state"] == "completed"
    assert submit["result"]["output"] == "ack: Summarize the inbox"
    assert submit["result"]["model"] == "model.one"  # first documented model used

    result = adapter.invoke("task.result", {"task_id": "t-1"})
    assert result["supported"] is True
    assert result["result"]["output"] == "ack: Summarize the inbox"

    unknown = adapter.invoke("task.result", {"task_id": "t-none"})
    assert unknown["supported"] is False  # honest: no recorded execution


def test_openclaw_offline_and_ws_ops_stay_honest():
    adapter = OpenClawGatewayAdapter(gateway_url="http://gw.test", transport=_gw(offline=True))
    submit = adapter.invoke("task.submit", {"task_id": "t-2", "objective": "do thing"})
    assert submit["supported"] is False and submit["reason"] == "gateway offline"

    online = OpenClawGatewayAdapter(gateway_url="http://gw.test", transport=_gw())
    for op in WS_ONLY_OPERATIONS:
        resp = online.invoke(op, {"task_id": "t"})
        assert resp["supported"] is False and "not implemented" in resp["reason"]


def test_openclaw_registry_declares_task_capabilities():
    import atexit
    import shutil
    import tempfile

    import anyio

    from pixel_startup.config import Settings
    from pixel_startup.main import create_app

    data_dir = tempfile.mkdtemp(prefix="openclaw-prov-")
    atexit.register(shutil.rmtree, data_dir, ignore_errors=True)
    app = create_app(
        Settings(pixel_data_dir=data_dir, dashboard_password="pw", pixel_api_token="tk")
    )

    async def drive():
        async with app.router.lifespan_context(app):
            registration = app.state.container.registry.get("openclaw_gateway")
            assert (
                "task.submit" in registration.capabilities
                and "task.result" in registration.capabilities
            )

    anyio.run(drive)
