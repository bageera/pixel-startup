"""G11 ChromaDB adapter: synthetic integration tests via httpx.MockTransport."""

import json

import httpx
import pytest

from pixel_startup.integrations.chromadb import (
    ChromaMemoryProvider,
    IntegrationDenied,
    ProviderUnavailable,
)


def _provider(handler, token="synthetic-token"):
    transport = httpx.MockTransport(handler)
    provider = ChromaMemoryProvider("http://chroma.test:8000", token, timeout=2.0)
    provider._client = lambda: httpx.Client(  # type: ignore[method-assign]
        base_url="http://chroma.test:8000",
        auth=None,
        headers={"Authorization": f"Bearer {token}"},
        timeout=2.0,
        transport=transport,
    )
    return provider


def _stub_handler(captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/api/v2/heartbeat":
            return httpx.Response(200, json={"nanosecond heartbeat": 1})
        if request.url.path == "/api/v2/collections":
            return httpx.Response(200, json=[{"id": "c1", "name": "institutional"}])
        if request.url.path == "/api/v2/collections/c1/query":
            return httpx.Response(
                200,
                json={
                    "ids": [["d1", "d2"]],
                    "documents": [["company playbook", "household secret"]],
                    "metadatas": [[{"scope": "company"}, {"scope": "household"}]],
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    return handler


def test_authorized_search_with_provenance_and_scope_filter() -> None:
    captured: list[httpx.Request] = []
    provider = _provider(_stub_handler(captured))
    hits = provider.search("institutional", ["playbook"], "company")
    # server-side scope filter present in the request body
    body = json.loads(captured[-1].content)
    assert body["where"] == {"scope": "company"}
    assert body["query_texts"] == ["playbook"]
    # provenance metadata returned on each hit
    assert hits[0]["provenance"] == {
        "provider": "chromadb",
        "collection": "institutional",
        "document_id": "d1",
    }
    assert hits[0]["metadata"]["scope"] == "company"


def test_scope_isolation_drops_cross_scope_hits() -> None:
    provider = _provider(_stub_handler([]))
    hits = provider.search("institutional", ["playbook"], "company")
    names = [h["document"] for h in hits]
    assert "company playbook" in names
    assert "household secret" not in names  # metadata disagrees with requested scope


def test_household_scope_sends_separate_filter() -> None:
    captured: list[httpx.Request] = []
    provider = _provider(_stub_handler(captured))
    provider.search("institutional", ["medical"], "household")
    assert json.loads(captured[-1].content)["where"] == {"scope": "household"}
    assert captured[0].url.path == "/api/v2/collections"


def test_auth_failure_fail_closed() -> None:
    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    with pytest.raises(IntegrationDenied):
        _provider(deny).health()


def test_service_unavailable_timeout_and_malformed() -> None:
    def boom_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json{")

    with pytest.raises(ProviderUnavailable):
        _provider(boom_503).health()
    with pytest.raises(ProviderUnavailable, match="malformed"):
        _provider(malformed).health()
    with pytest.raises(ProviderUnavailable, match="not configured"):
        ChromaMemoryProvider(None, None).health()


def test_secrets_never_in_errors_or_provider_repr() -> None:
    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    secret_token = "synthetic-token"
    provider = _provider(deny, token=secret_token)
    with pytest.raises(IntegrationDenied) as exc:
        provider.health()
    assert secret_token not in repr(exc.value)
    assert secret_token not in repr(provider)
    assert secret_token not in str(type(provider).__module__)


def test_invoke_contract_and_unsupported_operations() -> None:
    provider = _provider(_stub_handler([]))
    health = provider.invoke("health", {})
    assert health["online"] is True
    with pytest.raises(IntegrationDenied):  # arbitrary lookup surface denied
        provider.invoke("raw.collection.dump", {})
