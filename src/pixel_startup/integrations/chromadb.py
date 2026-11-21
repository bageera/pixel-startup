"""G11: ChromaDB institutional-memory adapter over the documented REST API (v2).

Scope + provenance are enforced in BOTH request and response: every query
carries a `scope` metadata filter, and any result whose metadata disagrees is
dropped (server-side filter + client-side guard). The API token never appears
in logs, errors, or audited payloads — errors are normalized to typed local
exceptions. Unconfigured = honest BLOCKED, never fabricated.
"""

import json
import logging

import httpx

logger = logging.getLogger("pixel_startup.chromadb")


class IntegrationDenied(Exception):
    """Authentication/authorization failure at the integration (fail closed)."""


class ProviderUnavailable(Exception):
    """Endpoint unreachable, malformed, or not configured."""


class ChromaMemoryProvider:
    provider_id = "chromadb"
    supported = True  # client logic supported; live rows need a configured endpoint

    def __init__(self, url: str | None, token: str | None, timeout: float = 5.0):
        self._url = (url or "").rstrip("/")
        self._token = token  # never logged, never serialized
        self._timeout = timeout
        self.configured = bool(url)

    def _client(self) -> httpx.Client:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.Client(
            base_url=self._url, headers=headers, timeout=self._timeout
        )

    def _request(self, method: str, path: str, json_body: dict | None = None) -> dict | list:
        if not self.configured:
            raise ProviderUnavailable("chromadb not configured; live rows BLOCKED")
        try:
            with self._client() as client:
                resp = client.request(method, path, json=json_body)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.info("chromadb unreachable: %s", exc.__class__.__name__)
            raise ProviderUnavailable("chromadb unreachable") from exc
        if resp.status_code in (401, 403):
            raise IntegrationDenied("chromadb rejected the request (401/403)")
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"chromadb service error {resp.status_code}")
        if resp.status_code >= 400:
            raise IntegrationDenied(f"chromadb rejected the request ({resp.status_code})")
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderUnavailable("chromadb returned a malformed response") from exc

    def health(self) -> dict:
        body = self._request("GET", "/api/v2/heartbeat")
        if not isinstance(body, dict) or "nanosecond heartbeat".lower() not in " ".join(
            str(k) for k in body
        ).lower():
            # Chroma answers heartbeat with a nanosecond-timestamp dict; shape only.
            if not isinstance(body, dict):
                raise ProviderUnavailable("chromadb malformed heartbeat")
        return {"online": True, "heartbeat_keys": sorted(body.keys())}

    def collections(self) -> list[dict]:
        rows = self._request("GET", "/api/v2/collections")
        if not isinstance(rows, list):
            raise ProviderUnavailable("chromadb malformed collection list")
        return [
            {"name": row.get("name"), "id": row.get("id"), "metadata": row.get("metadata")}
            for row in rows
            if isinstance(row, dict) and row.get("name")
        ]

    def _collection_id(self, name: str) -> str:
        for entry in self.collections():
            if entry["name"] == name:
                return entry["id"] or ""
        raise ProviderUnavailable(f"collection not found: {name}")

    def search(self, collection: str, query_texts: list[str], scope: str, k: int = 5) -> list[dict]:
        """Scoped semantic search; every hit carries provenance metadata."""
        if not query_texts or scope not in ("company", "household"):
            raise IntegrationDenied("malformed memory request")
        cid = self._collection_id(collection)
        rows_body = self._request("POST", f"/api/v2/collections/{cid}/query", {
            "query_embeddings": None,
            "query_texts": query_texts,
            "n_results": min(k, 25),
            "include": ["documents", "metadatas", "ids"],
            "where": {"scope": scope},  # server-side scope isolation
        })
        if not isinstance(rows_body, dict):
            raise ProviderUnavailable("chromadb malformed query response")
        body = rows_body
        rows = body.get("ids") or []
        out: list[dict] = []
        for i, ids in enumerate(rows):
            docs = body["documents"][i]
            metas = body["metadatas"][i]
            for pos, doc_id in enumerate(ids):
                meta = metas[pos] or {}
                if meta.get("scope") != scope:  # client-side isolation guard
                    continue  # dropped, never returned across scopes
                out.append({
                    "document": docs[pos],
                    "metadata": meta,
                    "provenance": {
                        "provider": self.provider_id,
                        "collection": collection,
                        "document_id": doc_id,
                    },
                })
        return out

    # provider-registry contract (invoke(operation, payload) -> dict)
    def invoke(self, operation: str, payload: dict) -> dict:
        if operation == "health":
            return self.health()
        if operation == "collections":
            return {"collections": self.collections()}
        if operation == "memory.search":
            hits = self.search(
                payload.get("collection") or "",
                payload.get("queries") or [],
                payload.get("scope") or "",
                int(payload.get("k") or 5),
            )
            return {"hits": hits}
        raise IntegrationDenied(f"unsupported memory operation: {operation}")
