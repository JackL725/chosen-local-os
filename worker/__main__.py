"""`uv run python -m worker` (dev) or the cl-worker systemd unit (production)."""

from __future__ import annotations

import sys

from worker import logs
from worker.config import get_settings
from worker.local_llm import LocalLLM
from worker.main import Worker
from worker.slack import SlackWebhook


def main() -> int:
    logs.configure()
    settings = get_settings()
    llm = LocalLLM(
        settings.local_llm_base_url,
        settings.local_llm_model,
        thinking=settings.local_llm_thinking,
        timeout_s=settings.local_llm_timeout_s,
    )
    worker = Worker(settings, llm, SlackWebhook(settings.slack_alert_webhook_url))
    worker.install_signal_handlers()
    try:
        worker.run()
    finally:
        llm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
