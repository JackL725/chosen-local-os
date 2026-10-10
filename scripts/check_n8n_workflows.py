"""Check exported n8n workflows (.claude/rules/n8n-and-db.md).

Fails if:
  * any workflow other than send-gate contains a node that sends to a prospect or coach
    (email/SMS nodes, or an HTTP Request to Close's SMS/email endpoints);
  * any node still carries a credential reference (exports must be stripped);
  * any workflow carries instance-specific IDs (workflow id, versionId, instanceId).

Usage: python scripts/check_n8n_workflows.py [folder]   (default: n8n/workflows)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SEND_GATE = "send-gate"
SENDING_NODE_TYPES = {
    "n8n-nodes-base.emailSend",
    "n8n-nodes-base.gmail",
    "n8n-nodes-base.microsoftOutlook",
    "n8n-nodes-base.sendGrid",
    "n8n-nodes-base.mailgun",
    "n8n-nodes-base.mailjet",
    "n8n-nodes-base.twilio",
    "n8n-nodes-base.vonage",
    "n8n-nodes-base.messageBird",
    "n8n-nodes-base.plivo",
}
# Close API paths that send to a person (activity creation for SMS and email).
CLOSE_SEND_PATHS = ("/activity/sms", "/activity/email", "/activity/whatsapp")
INSTANCE_KEYS = ("id", "versionId")


def check_workflow(path: Path) -> list[str]:
    problems: list[str] = []
    data = json.loads(path.read_text(encoding="utf-8"))
    name = path.stem
    for key in INSTANCE_KEYS:
        if data.get(key):
            problems.append(f"{path.name}: remove instance-specific '{key}'")
    if (data.get("meta") or {}).get("instanceId"):
        problems.append(f"{path.name}: remove meta.instanceId")
    for node in data.get("nodes", []):
        label = f"{path.name}: node '{node.get('name', '?')}'"
        if node.get("credentials"):
            problems.append(f"{label} still references credentials; strip them before export")
        if name == SEND_GATE:
            continue
        node_type = node.get("type", "")
        if node_type in SENDING_NODE_TYPES:
            problems.append(f"{label} sends messages ({node_type}); only send-gate may")
        if node_type == "n8n-nodes-base.httpRequest":
            url = json.dumps(node.get("parameters", {})).lower()
            if "close.com" in url and any(p in url for p in CLOSE_SEND_PATHS):
                problems.append(f"{label} calls a Close send endpoint; only send-gate may")
    return problems


def main(argv: list[str]) -> int:
    folder = (
        Path(argv[1])
        if len(argv) > 1
        else Path(__file__).resolve().parent.parent / "n8n" / "workflows"
    )
    problems: list[str] = []
    for path in sorted(folder.glob("*.json")):
        problems.extend(check_workflow(path))
    for p in problems:
        print(p)
    if problems:
        return 1
    print(f"n8n workflows ok ({len(list(folder.glob('*.json')))} checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
