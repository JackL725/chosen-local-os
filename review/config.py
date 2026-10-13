"""Review console configuration, from the environment the systemd unit provides
(EnvironmentFile=/home/clworker/.config/chosen-local/worker.env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class ConsoleSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # The console's own login (cl_console): the only one that may act as a founder.
    # Dev falls back to DATABASE_URL.
    console_database_url: str = Field(
        validation_alias=AliasChoices("CONSOLE_DATABASE_URL", "DATABASE_URL")
    )
    private_data_dir: Path
    review_console_host: str = "127.0.0.1"
    review_console_port: int = 8090
    review_console_base_url: str = ""
    slack_alert_webhook_url: str = ""
    # Both this switch and the founder's own flag must be on for password-only sign-in
    # from the workstation itself (decision 11, Oct 13: Jack only).
    console_allow_local_signin: bool = False
    # Upload limit for vetting evidence, in megabytes.
    console_max_upload_mb: int = 500

    @field_validator("review_console_host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        if value not in LOOPBACK:
            raise ValueError(
                "REVIEW_CONSOLE_HOST must be a loopback address; Tailscale publishes it"
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> ConsoleSettings:
    return ConsoleSettings()  # type: ignore[call-arg]
