#!/usr/bin/env python3
"""G19 backup/restore for the Python operational store.

Conventional, verified method: the SQLite backup API (consistent snapshot while
live) + integrity_check + SHA-256 sidecar. mdrc (research archive format) is
recorded BLOCKED — its binary is not installed on this host and the research
artifacts are not yet qualified as production backup tooling.

    backup:  python3 tools/backup.py backup  <db> <dest>
    restore: python3 tools/backup.py restore <backup> <dbdir>
"""

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def backup(db_path: str, dest_path: str) -> dict:
    src = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(dest_path)
        with dest:
            src.backup(dest)  # backup API: consistent snapshot across live WAL
        dest.close()
        check = sqlite3.connect(dest_path)
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        check.close()
        if integrity != "ok":
            raise SystemExit(f"backup failed integrity_check: {integrity}")
    finally:
        src.close()
    blob = Path(dest_path).read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    meta = Path(dest_path + ".meta")
    meta.write_text(json.dumps({
        "source": str(Path(db_path).resolve()),
        "created_at": _now(), "sha256": sha, "size": len(blob),
        "tool": "sqlite3-backup-api", "integrity": "ok",
    }))
    return {"dest": dest_path, "sha256": sha, "size": len(blob), "integrity": "ok"}


def restore(backup_path: str, db_dir: str) -> dict:
    meta_path = Path(backup_path + ".meta")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    blob = Path(backup_path).read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if meta.get("sha256") and digest != meta["sha256"]:
        raise SystemExit("backup integrity mismatch — refusing to restore")  # corruption
    target = Path(db_dir) / (Path(backup_path).stem + ".sqlite3")
    target.parent.mkdir(parents=True, exist_ok=True)
    # never clobber a live WAL sidecar set: remove stale -wal/-shm first
    for suffix in ("-wal", "-shm"):
        stale = target.parent / (target.name + suffix)
        if stale.exists():
            stale.unlink()
    # the verified snapshot IS the restored file; verify after placing
    Path(target).write_bytes(blob)
    conn = sqlite3.connect(str(target))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"restored file failed integrity_check: {integrity}")
    finally:
        conn.close()
    return {"restored_to": str(target), "sha256": digest,
            "verified_against_meta": bool(meta.get("sha256"))}


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("backup", "restore"):
        raise SystemExit(__doc__)
    if sys.argv[1] == "backup":
        print(json.dumps(backup(sys.argv[2], sys.argv[3]), indent=1))
    else:
        print(json.dumps(restore(sys.argv[2], sys.argv[3]), indent=1))
