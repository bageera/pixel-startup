"""G13 communications: synthetic Mattermost/email qualification."""

import pytest

from pixel_startup.integrations.comms import (
    CommsDenied,
    CommsUnavailable,
    EmailAdapter,
    MattermostAdapter,
)


def test_mattermost_allowlist_enforcement() -> None:
    calls: list[dict] = []

    def client(method, path, body, token):
        calls.append(body)
        return 201, {"id": "m1"}

    adapter = MattermostAdapter("http://mm.test", "tok", ("company-town-square",), client)
    result = adapter.post("company-town-square", "briefing ready", "corr-1")
    assert calls[0]["channel_id"] == "company-town-square"
    assert result["subject"] == "briefing ready"  # redaction-safe short form
    with pytest.raises(CommsDenied):  # cross-scope channel refused
        adapter.post("household-private", "leak", "corr-2")
    with pytest.raises(CommsDenied):
        adapter.post("company-town-square", "x", "")  # malformed


def test_mattermost_idempotent_delivery_and_rate_limit() -> None:
    calls: list[dict] = []

    def client(method, path, body, token):
        calls.append(body)
        return (429, {}) if len(calls) == 1 else (201, {"id": "m2"})

    adapter = MattermostAdapter("http://mm.test", "tok", ("ch-1",), client)
    first = adapter.post("ch-1", "hello", "corr-a")
    second = adapter.post("ch-1", "hello", "corr-a")  # same correlation: replayed
    assert first.get("replayed") is None and second["replayed"] is True
    assert len(calls) == 2  # the 429 retry, then success; the replay added nothing
    unconfigured = MattermostAdapter(None, None, ("ch-1",))
    with pytest.raises(CommsUnavailable, match="BLOCKED"):
        unconfigured.post("ch-1", "hi", "corr-b")


def test_email_recipient_and_attachment_validation() -> None:
    adapter = EmailAdapter()
    with pytest.raises(CommsDenied):
        adapter.compose_draft(["not-an-email"], "s", "b")
    with pytest.raises(CommsDenied):
        adapter.compose_draft([], "s", "b")
    with pytest.raises(CommsDenied):
        adapter.compose_draft(
            ["vendor@example.com"],
            "s",
            "b",
            [{"name": "../evil.sh", "content_type": "application/pdf", "data": b""}],
        )
    with pytest.raises(CommsDenied):  # disallowed content type
        adapter.compose_draft(
            ["vendor@example.com"],
            "s",
            "b",
            [{"name": "run.sh", "content_type": "application/x-sh", "data": b""}],
        )
    with pytest.raises(CommsDenied):  # oversized
        adapter.compose_draft(
            ["vendor@example.com"],
            "s",
            "b",
            [
                {
                    "name": "doc.pdf",
                    "content_type": "application/pdf",
                    "data": b"x" * (5 * 1024 * 1024 + 1),
                }
            ],
        )
    draft = adapter.compose_draft(["vendor@example.com", "vendor@example.com"], "s", "b")
    assert draft["status"] == "pending_approval"
    assert draft["recipients"] == ["vendor@example.com"]  # deduplicated


def test_email_delivery_requires_owner_approval_and_idempotency() -> None:
    sent: list[dict] = []

    def transport(draft):
        sent.append(draft)
        return 250

    adapter = EmailAdapter(transport)
    draft = adapter.compose_draft(["vendor@example.com"], "invoice", "attached")
    with pytest.raises(CommsDenied, match="owner approval"):
        adapter.send(draft, {"status": "pending"})
    with pytest.raises(CommsDenied):  # approval bypass attempt: no approver recorded
        adapter.send(draft, {"status": "approved"})
    result = adapter.send(draft, {"status": "approved", "by": "owner"})
    assert result["delivered"] is True and len(sent) == 1
    assert adapter.send(draft, {"status": "approved", "by": "owner"})["replayed"] is True
    assert len(sent) == 1  # duplicate suppressed
    live = EmailAdapter()
    with pytest.raises(CommsUnavailable, match="BLOCKED"):
        live.send(draft, {"status": "approved", "by": "owner"})


def test_forged_sender_scenarios_fail_closed() -> None:
    adapter = EmailAdapter()
    # header injection in recipients/subjects never accepted
    with pytest.raises(CommsDenied):
        adapter.compose_draft(["a@b.com\r\nBcc: victim@c.com"], "s", "b")
    with pytest.raises(CommsDenied):
        adapter.compose_draft(["a@b.com"], "Subject: x\r\nFrom: forged", "b")
