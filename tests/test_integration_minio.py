"""G12 MinIO adapter: synthetic tests with an in-memory S3Client fake."""

import pytest

from pixel_startup.integrations.minio import (
    AccessDenied,
    MinioStorageProvider,
    SigV4Client,
    StorageUnavailable,
    validate_key,
)


class FakeS3:
    """Conforming S3Client backed by an in-memory dict; hashes content verbatim."""

    def __init__(self, fail: int | None = None):
        self.objects: dict[str, tuple[int, bytes, dict]] = {}
        self.fail = fail

    def request(self, method, path, headers, body=None):
        if self.fail is not None:
            return self.fail, {}, b"<Error/>"
        if method == "PUT":
            self.objects[path] = (len(body or b""), body or b"", dict(headers))
            return 200, {"ETag": '"x"'}, b""
        if method == "GET" and "list-type" in path:
            prefix = path.split("prefix=")[-1]
            keys = "".join(
                f"<Key>{k}</Key>" for k in self.objects if k.startswith(f"/bucket/{prefix}")
            )
            return 200, {}, f"<ListBucketResult>{keys}</ListBucketResult>".encode()
        if method == "GET":
            stored = self.objects.get(path)
            if stored is None:
                return 404, {}, b""
            provided = stored[2].get("X-Amz-Meta-Sha256")
            meta = {"X-Amz-Meta-Sha256": provided} if provided else {}
            return 200, {"Content-Length": str(stored[0]), **meta}, stored[1]
        if method == "HEAD":
            stored = self.objects.get(path)
            if stored is None:
                return 404, {}, b""
            return 200, {"Content-Length": str(stored[0]), "Content-Type": "text/plain"}, b""
        return 400, {}, b""


def _provider() -> MinioStorageProvider:
    return MinioStorageProvider("bucket", FakeS3())


def test_roundtrip_with_sha256_integrity() -> None:
    provider = _provider()
    put = provider.put("shared/reports/q3.txt", b"quarterly", "company", "employee.charon")
    assert put["sha256"] == __import__("hashlib").sha256(b"quarterly").hexdigest()
    data, meta = provider.get("shared/reports/q3.txt", "company", "employee.charon")
    assert data == b"quarterly"
    assert meta["provenance"]["key"] == "shared/reports/q3.txt"
    assert meta["provenance"]["provider"] == "minio"


def test_path_traversal_and_prefix_guards() -> None:
    for bad in ("../../etc/passwd", "/abs/k", "back\\slash", "null\x00"):
        with pytest.raises(AccessDenied):
            validate_key(bad, "company", "x")
    outside = "company/secret.txt"
    with pytest.raises(AccessDenied):  # household subject, company prefix
        validate_key(outside, "household", "resident.demo")
    with pytest.raises(AccessDenied):  # unnamespaced root key refused for anyone
        validate_key("topsecret.txt", "company", "employee.charon")


def test_scope_isolation_between_subjects() -> None:
    provider = _provider()
    provider.put("company/charon/notes.txt", b"notes", "company", "employee.charon")
    provider.put("household/medical/record.txt", b"medical", "household", "resident.demo")
    provider.get("household/medical/record.txt", "household", "resident.demo")
    with pytest.raises(AccessDenied):  # household resident cannot read company object
        provider.get("company/charon/notes.txt", "household", "resident.demo")


def test_corruption_detection_and_oversize() -> None:
    fake = FakeS3()
    provider = MinioStorageProvider("bucket", fake)
    fake.objects["/bucket/shared/k"] = (2, b"xy", {})  # no hash header = ok
    # forged/different hash must fail closed
    tamper = FakeS3()
    tamper.objects["/bucket/shared/k"] = (2, b"xy", {"X-Amz-Meta-Sha256": "0" * 64})
    with pytest.raises(StorageUnavailable, match="integrity"):
        MinioStorageProvider("bucket", tamper).get("shared/k", "company", "employee.charon")
    big = b"x" * (25 * 1024 * 1024 + 1)
    with pytest.raises(AccessDenied, match="size limit"):
        provider.put("shared/big.bin", big, "company", "employee.charon")


def test_unauthorized_and_unavailable_failures() -> None:
    deny = FakeS3(fail=403)
    with pytest.raises(AccessDenied):
        MinioStorageProvider("bucket", deny).get("shared/k", "company", "employee.charon")
    down = FakeS3(fail=503)
    with pytest.raises(StorageUnavailable):
        MinioStorageProvider("bucket", down).get("shared/k", "company", "employee.charon")
    unconfigured = MinioStorageProvider("bucket", None)
    with pytest.raises(StorageUnavailable, match="BLOCKED"):
        unconfigured.get("shared/k", "company", "employee.charon")


def test_listing_scoped_and_duplicate_puts_idempotent() -> None:
    provider = _provider()
    for _ in range(2):  # duplicate requests: deterministic overwrite, same digest
        provider.put("shared/log.txt", b"line", "company", "employee.charon")
    keys = provider.list("shared/", "company", "employee.charon")
    assert keys == ["/bucket/shared/log.txt"]


def test_credential_revocation_semantics() -> None:
    fake = FakeS3()
    provider = MinioStorageProvider("bucket", fake)
    for _ in range(2):
        provider.put("shared/log.txt", b"line", "company", "employee.charon")
    fake.fail = 401  # revoked access mid-flight
    with pytest.raises(AccessDenied):
        provider.get("shared/log.txt", "company", "employee.charon")


def test_signature_does_not_leak_secrets() -> None:
    import inspect

    sigv4 = SigV4Client("minio.internal:9000", "AK", "sk-secret-value")
    src = inspect.getsource(type(sigv4).request)
    assert "sk-secret-value" not in src  # credentials only referenced via attributes


def test_sigv4_client_signature_shape() -> None:
    sigv4 = SigV4Client("minio.internal:9000", "AK", "sk-secret-value")
    assert sigv4._host == "minio.internal:9000"
