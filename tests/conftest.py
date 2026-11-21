"""Shared pytest fixtures. Nothing else lives here."""

import json
import os

import pytest


@pytest.fixture
def hermes_home_qualified(tmp_path):
    """Controlled Hermes home whose gateway state says RUNNING with this
    process's pid (alive by definition), so the G8 boot probe flips
    hermes_home -> qualified deterministically.

    Why: production code falls back to the HOST's ~/.hermes; Jon's Mac has a
    real (online) home but CI runners do not — host-dependent tests, the exact
    hermeticity gap CI caught on the first run."""
    home = tmp_path / ".hermes-home"
    home.mkdir()
    (home / "gateway_state.json").write_text(
        json.dumps({"gateway_state": "running", "pid": os.getpid(), "platforms": {}})
    )
    return home
