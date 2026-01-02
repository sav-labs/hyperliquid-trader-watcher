from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    bot_admins: set[int] = Field(default_factory=set, alias="BOT_ADMINS")

    hl_poll_interval_seconds: int = Field(default=10, alias="HL_POLL_INTERVAL_SECONDS", ge=2, le=3600)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO", alias="LOG_LEVEL")
    max_log_files: int = Field(default=50, alias="MAX_LOG_FILES", ge=1, le=500)

    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    db_path: Path = Field(default=Path("./data/db/app.sqlite3"), alias="DB_PATH")
    log_dir: Path = Field(default=Path("./data/logs"), alias="LOG_DIR")

    @field_validator("bot_admins", mode="before")
    @classmethod
    def _parse_admins(cls, v: object) -> set[int]:
        if v is None:
            return set()
        if isinstance(v, (set, list, tuple)):
            return {int(x) for x in v}
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return set()
            return {int(x.strip()) for x in raw.split(",") if x.strip()}
        return {int(v)}  # type: ignore[arg-type]


