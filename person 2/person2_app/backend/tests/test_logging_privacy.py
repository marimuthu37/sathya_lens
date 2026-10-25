"""
tests/test_logging_privacy.py
─────────────────────────────
Regression: with default settings (DEBUG=true in .env.example), SQL echo logged
every bound parameter, so case descriptions and extracted payment values were
written to the server log. SQL echo is now opt-in and parameters are hidden.
Runs in a fresh process so the real module-level engine/config are exercised.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = r"""
import io, logging
buf = io.StringIO()
logging.getLogger("sqlalchemy.engine").addHandler(logging.StreamHandler(buf))
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as c:
    assert c.post("/api/cases", json={"description": "CANARY-payee-Riya-5000"}).status_code == 201
print("LEAK" if "CANARY-payee-Riya-5000" in buf.getvalue() else "CLEAN")
"""


def _run(tmp_path, **env_overrides) -> str:
    env = dict(os.environ, PYTHONUTF8="1", DEBUG="true",
               DATABASE_URL=f"sqlite:///{(tmp_path / 'p.db').as_posix()}", **env_overrides)
    if "SQL_ECHO" not in env_overrides:
        env.pop("SQL_ECHO", None)
    out = subprocess.run([sys.executable, "-c", _SCRIPT], cwd=Path(__file__).parent.parent,
                         env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1]


def test_default_settings_do_not_log_request_content(tmp_path):
    assert _run(tmp_path) == "CLEAN"


def test_sql_echo_still_hides_parameters(tmp_path):
    assert _run(tmp_path, SQL_ECHO="true") == "CLEAN"
