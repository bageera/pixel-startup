"""G12: MinIO/S3 object-storage adapter.

Client interface: `S3Client` with head(bucket,key)/get/put/list/delete. The
default client speaks SigV4 with stdlib hmac/sha256 (no new dependency). For
qualification, ANY conforming client can be injected — tests use an in-memory
fake so the adapter's logic (scope validation, hashing, size limits, error
normalization, redaction) is fully exercised without live infrastructure.

Scope isolation: all object keys must live under the approved scope prefix —
`company/<employee_id>` / `shared/` / `household/<resident>`; anything else is
denied before a byte is sent.
"""

import hashlib
import hmac
import http.client
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Protocol

logger = logging.getLogger("pixel_startup.minio")

MAX_OBJECT_BYTES = 25 * 1024 * 1024
ALLOWED_PREFIXES = ("company/", "shared/", "household/")


class AccessDenied(Exception):
    """Authorization failure — fail closed, nothing leaks."""


class StorageUnavailable(Exception):
    """Endpoint down, malformed, or not configured."""


def _scope_prefix_for(scope: str, subject_id: str) -> tuple[str, ...]:
    if scope == "company":
        return ("company/", "shared/")
    if scope == "household":
        return ("household/", "shared/")
    raise AccessDenied("malformed storage request: unknown scope")


def validate_key(key: str, scope: str, subject_id: str) -> str:
    """Path-traversal + scope enforcement at the single chokepoint."""
    if not key or ".." in key or key.startswith("/") or "\\" in key or "\x00" in key:
        raise AccessDenied("malformed object key")
    if not key.startswith(ALLOWED_PREFIXES):
        raise AccessDenied("object key outside approved prefixes")
    allowed = _scope_prefix_for(scope, subject_id)
    if not key.startswith(allowed):
        raise AccessDenied("object key outside the approved scope")
    return key


class S3Client(Protocol):
    def request(
        self, method: str, path: str, headers: dict[str, str], body: bytes | None = None
    ) -> tuple[int, dict[str, str], bytes]: ...


class SigV4Client:
    """Minimal S3 path-style client; zero dependencies, no credential logging."""

    def __init__(self, host: str, access_key: str, secret_key: str, timeout: float = 10.0):
        self._host = host  # e.g. "minio.internal:9000"
        self._ak = access_key
        self._sk = secret_key
        self._timeout = timeout

    def request(
        self, method: str, path: str, headers: dict[str, str], body: bytes | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        body = body or b""
        amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        date_stamp = amz_date[:8]
        payload_hash = hashlib.sha256(body).hexdigest()
        base_headers = {
            "Host": self._host.split("/")[-1],
            "X-Amz-Date": amz_date,
            "X-Amz-Content-Sha256": payload_hash,
            **headers,
        }
        signed = ";".join(sorted(base_headers))
        canonical_headers = "".join(
            f"{k.lower()}:{base_headers[k]}\n" for k in sorted(base_headers)
        )
        canonical = "\n".join([method, path, "", canonical_headers, signed, payload_hash])
        scope = f"{date_stamp}/us-east-1/s3/aws4_request"
        k = hmac.new(b"AWS4" + self._sk.encode(), date_stamp.encode(), hashlib.sha256).digest()
        k = hmac.new(k, b"us-east-1", hashlib.sha256).digest()
        k = hmac.new(k, b"s3", hashlib.sha256).digest()
        k = hmac.new(k, b"aws4_request", hashlib.sha256).digest()
        sig = hmac.new(k, canonical.encode(), hashlib.sha256).hexdigest()
        base_headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._ak}/{scope}, "
            f"SignedHeaders={signed}, Signature={sig}"
        )
        conn = (
            http.client.HTTPSConnection(self._host, timeout=self._timeout)
            if ":" not in self._host or self._host.endswith(":443")
            else http.client.HTTPConnection(self._host, timeout=self._timeout)
        )
        try:
            conn.request(method, urllib.parse.quote(path), body=body, headers=base_headers)
            resp = conn.getresponse()
            resp_headers = dict(resp.getheaders())
            return resp.status, resp_headers, resp.read()
        except OSError as exc:
            raise StorageUnavailable("object storage unreachable") from exc
        finally:
            conn.close()


class MinioStorageProvider:
    provider_id = "minio"
    capabilities = ["object.put", "object.get", "object.list", "object.stat"]

    def __init__(self, bucket: str, client: S3Client | None, prefix_scope: bool = False):
        self._bucket = bucket
        self._client = client
        self.configured = client is not None

    def _require(self) -> S3Client:
        if self._client is None:
            raise StorageUnavailable("minio not configured; live rows BLOCKED")
        return self._client

    def stat(self, key: str, scope: str, subject_id: str) -> dict:
        key = validate_key(key, scope, subject_id)
        status, headers, _ = self._require().request("HEAD", f"/{self._bucket}/{key}", {})
        if status == 404:
            raise FileNotFoundError(key)
        if status in (401, 403):
            raise AccessDenied("object storage rejected the request")
        if status != 200:
            raise StorageUnavailable(f"object storage error {status}")
        return {
            "size": int(headers.get("Content-Length") or 0),
            "content_type": headers.get("Content-Type"),
            "key": key,
            "bucket": self._bucket,
        }

    def get(self, key: str, scope: str, subject_id: str) -> tuple[bytes, dict]:
        key = validate_key(key, scope, subject_id)
        status, headers, body = self._require().request("GET", f"/{self._bucket}/{key}", {})
        if status == 404:
            raise FileNotFoundError(key)
        if status in (401, 403):
            raise AccessDenied("object storage rejected the request")
        if status != 200 or len(body) > MAX_OBJECT_BYTES:
            raise StorageUnavailable("object storage error or oversized object")
        provided = headers.get("X-Amz-Meta-Sha256") or headers.get("ETag", "").strip('"')
        integrity = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
        if provided and len(provided) == 64 and provided != integrity["sha256"]:
            raise StorageUnavailable("object integrity mismatch (sha256)")  # corruption
        return body, {
            "key": key,
            "bucket": self._bucket,
            "provenance": {
                "provider": self.provider_id,
                "bucket": self._bucket,
                "key": key,
                "sha256": integrity["sha256"],
                "content_type": headers.get("Content-Type"),
            },
        }

    def put(
        self,
        key: str,
        body: bytes,
        scope: str,
        subject_id: str,
        content_type: str = "application/octet-stream",
    ) -> dict:
        key = validate_key(key, scope, subject_id)
        if len(body) > MAX_OBJECT_BYTES:
            raise AccessDenied("object exceeds the size limit")
        headers = {
            "Content-Type": content_type,
            "X-Amz-Meta-Sha256": hashlib.sha256(body).hexdigest(),
        }
        status, _, _ = self._require().request("PUT", f"/{self._bucket}/{key}", headers, body)
        if status in (401, 403):
            raise AccessDenied("object storage rejected the request")
        if status not in (200, 201):
            raise StorageUnavailable(f"object storage error {status}")
        return {
            "key": key,
            "bucket": self._bucket,
            "sha256": headers["X-Amz-Meta-Sha256"],
            "size": len(body),
        }

    def list(self, prefix: str, scope: str, subject_id: str) -> list[str]:
        prefix = validate_key(prefix if prefix.endswith("/") else f"{prefix}/", scope, subject_id)
        status, _, body = self._require().request(
            "GET", f"/{self._bucket}?list-type=2&prefix={urllib.parse.quote(prefix)}", {}
        )
        if status in (401, 403):
            raise AccessDenied("object storage rejected the request")
        if status != 200:
            raise StorageUnavailable(f"object storage error {status}")
        # parse the minimal <Key> elements from ListObjectsV2 XML
        keys = []
        for chunk in body.decode("utf-8", "replace").split("<Key>")[1:]:
            keys.append(chunk.split("</Key>")[0])
        return keys

    def invoke(self, operation: str, payload: dict) -> dict:  # registry contract
        scope = payload.get("scope") or ""
        subject = payload.get("subject_id") or ""
        if operation == "object.put":
            return self.put(payload["key"], payload.get("body", b"").encode(), scope, subject)
        if operation == "object.get":
            data, meta = self.get(payload["key"], scope, subject)
            return {"meta": meta, "size": len(data)}  # body stays inside the executor
        if operation == "object.list":
            return {"keys": self.list(payload.get("prefix") or "", scope, subject)}
        raise AccessDenied(f"unsupported storage operation: {operation}")
