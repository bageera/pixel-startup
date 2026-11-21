"""G19 backup/restore tool qualification (conventional SQLite method)."""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.backup import backup, restore  # noqa: E402


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('alpha')")
    conn.commit()
    conn.close()


def test_backup_restore_roundtrip_with_integrity():
    root = Path(tempfile.mkdtemp(prefix="bk-"))
    db = str(root / "ops.sqlite3")
    _seed(db)
    snapshot = backup(db, str(root / "ops.bak"))
    assert snapshot["integrity"] == "ok" and snapshot["sha256"]
    # live write AFTER backup: WAL-era data is NOT in the snapshot
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO t VALUES ('beta')")
    conn.commit()
    conn.close()
    out = str(root / "ops.sqlite3")  # stem-derived restore target
    info = restore(str(root / "ops.bak"), str(root))
    assert info["verified_against_meta"] is True
    assert info["restored_to"] == out
    check = sqlite3.connect(out).execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert check == 1  # snapshot semantics (pre-beta), integrity verified


def test_corruption_refused():
    root = Path(tempfile.mkdtemp(prefix="bk2-"))
    db = str(root / "ops.sqlite3")
    _seed(db)
    backup(db, str(root / "ops.bak"))
    blob = bytearray((root / "ops.bak").read_bytes())
    blob[200:216] = b"corrupted!!!!!!!"  # bit-rot simulation
    (root / "ops.bak").write_bytes(bytes(blob))
    with pytest.raises(SystemExit, match="integrity mismatch"):
        restore(str(root / "ops.bak"), str(root))
