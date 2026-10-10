from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "check_n8n_workflows.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=ROOT)


def test_committed_workflows_pass():
    result = _run(str(CHECK))
    assert result.returncode == 0, result.stdout


def test_check_catches_sends_outside_send_gate_and_leftover_credentials(tmp_path: Path):
    bad = {
        "id": "abc123",
        "name": "intake",
        "nodes": [
            {"name": "Text him", "type": "n8n-nodes-base.twilio", "parameters": {}},
            {
                "name": "Close SMS",
                "type": "n8n-nodes-base.httpRequest",
                "parameters": {"url": "https://api.close.com/api/v1/activity/sms/"},
            },
            {
                "name": "DB",
                "type": "n8n-nodes-base.postgres",
                "parameters": {},
                "credentials": {"postgres": {"id": "7", "name": "Ledger"}},
            },
        ],
    }
    gate = {
        "name": "send-gate",
        "nodes": [
            {
                "name": "Close SMS",
                "type": "n8n-nodes-base.httpRequest",
                "parameters": {"url": "https://api.close.com/api/v1/activity/sms/"},
            }
        ],
    }
    (tmp_path / "intake.json").write_text(json.dumps(bad))
    (tmp_path / "send-gate.json").write_text(json.dumps(gate))
    result = _run(str(CHECK), str(tmp_path))
    assert result.returncode == 1
    out = result.stdout
    assert "Text him" in out and "Close SMS" in out and "credentials" in out and "'id'" in out
    assert "send-gate.json" not in out


def test_env_examples_hold_no_secret_values():
    result = _run(str(ROOT / "scripts" / "check_env_examples.py"))
    assert result.returncode == 0, result.stdout
