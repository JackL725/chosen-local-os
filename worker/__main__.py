"""`uv run python -m worker` (dev) or the cl-worker systemd unit (production).

Runs WORKER_CONCURRENCY slots. Each slot is a full Worker with its own worker_id
("<host>-<pid>-s<n>"), database connection, heartbeat and model client, so slots never
share a lease, and SIGTERM lets every slot finish its current job before exiting.
"""

from __future__ import annotations

import signal
import sys
import threading

from worker import logs
from worker.config import Settings, get_settings
from worker.local_llm import LocalLLM
from worker.main import Worker
from worker.slack import SlackWebhook


def _slot_settings(settings: Settings, n: int) -> Settings:
    if settings.worker_concurrency == 1:
        return settings
    return settings.model_copy(update={"worker_id": f"{settings.worker_id}-s{n}"})


def main() -> int:
    logs.configure()
    settings = get_settings()
    alert = SlackWebhook(settings.slack_alert_webhook_url)
    slots: list[tuple[Worker, LocalLLM]] = []
    for n in range(1, settings.worker_concurrency + 1):
        slot = _slot_settings(settings, n)
        llm = LocalLLM(
            slot.local_llm_base_url,
            slot.local_llm_model,
            thinking=slot.local_llm_thinking,
            timeout_s=slot.local_llm_timeout_s,
        )
        slots.append((Worker(slot, llm, alert), llm))

    def stop(*_: object) -> None:
        for worker, _llm in slots:
            worker.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threads = [
        threading.Thread(target=worker.run, name=f"slot-{n}")
        for n, (worker, _llm) in enumerate(slots, 1)
    ]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    finally:
        for _worker, llm in slots:
            llm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
