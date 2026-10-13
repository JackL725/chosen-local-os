"""Worker configuration.

Everything comes from the environment. In production that is the systemd unit's
EnvironmentFile (/home/clworker/.config/chosen-local/worker.env); nothing here reads a
shell profile. Models are configuration, not code: change them here or in the env.
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    database_url: str

    # Local model server (llama.cpp llama-server). Must be on this machine.
    local_llm_base_url: str = "http://127.0.0.1:8080/v1"
    local_llm_model: str = "qwen3.6-27b-q4_k_m"
    local_llm_thinking: bool = False
    local_llm_timeout_s: float = 120.0

    # Private data. Required, never defaulted: in dev it points at evals/synthetic.
    private_data_dir: Path

    # Alerts. Empty means "log only" (dev without a Slack channel).
    slack_alert_webhook_url: str = ""
    review_console_base_url: str = ""

    # Queue behaviour.
    worker_id: str = Field(default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}")
    worker_poll_interval_s: float = 2.0
    worker_lease_s: int = 300
    worker_heartbeat_s: float = 30.0
    worker_reap_interval_s: float = 60.0
    worker_retry_base_s: float = 30.0
    worker_enable_test_jobs: bool = False
    git_sha: str = ""

    @field_validator("local_llm_base_url")
    @classmethod
    def _local_only(cls, value: str) -> str:
        host = urlparse(value).hostname or ""
        if host not in LOCAL_HOSTS:
            raise ValueError("LOCAL_LLM_BASE_URL must point at this machine (127.0.0.1)")
        return value.rstrip("/")

    @field_validator("private_data_dir")
    @classmethod
    def _private_dir(cls, value: Path) -> Path:
        if str(value).strip() == "":
            raise ValueError("PRIVATE_DATA_DIR must be set")
        return value


# Model profiles. A system's manifest binds each thing it asks a model to do to one of
# these by name (systems/_contract.py ModelUse). Endpoints and model names come from the
# environment above; this table only says where each profile runs. A cloud profile is
# added here, behind worker/cloud_llm.py, when the first system that may use one exists.
MODEL_PROFILES: dict[str, str] = {
    "local": "local",  # llama-server at LOCAL_LLM_BASE_URL, model LOCAL_LLM_MODEL
}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
