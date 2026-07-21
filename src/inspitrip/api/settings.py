from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import dotenv_values

from inspitrip.paths import DEFAULT_ENV_PATH


@dataclass(frozen=True)
class RuntimeSettings:
    mode: str
    dify_api_base: str
    dify_api_key: str
    database_url: str

    @classmethod
    def load(cls) -> "RuntimeSettings":
        file_values = dotenv_values(DEFAULT_ENV_PATH)

        def value(name: str, default: str = "") -> str:
            return str(os.environ.get(name, file_values.get(name, default)) or default).strip()

        mode = value("INSPITRIP_MODE", "demo").lower()
        if mode not in {"demo", "full"}:
            mode = "demo"
        return cls(
            mode=mode,
            dify_api_base=value("DIFY_APP_API_BASE").rstrip("/"),
            dify_api_key=value("DIFY_APP_API_KEY"),
            database_url=value("DATABASE_URL"),
        )
