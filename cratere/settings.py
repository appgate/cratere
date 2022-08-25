import pathlib
from typing import Literal

from pydantic import BaseSettings

__all__ = [
    "Settings",
    "settings",
]


class Settings(BaseSettings):
    scheme: Literal["http", "https"] = "http"
    host: str = "172.17.0.1"
    port: int = 8000
    index: pathlib.Path = pathlib.Path("crates.io-index-master")
    cache: pathlib.Path = pathlib.Path("storage")
    # Schedule for crates index update https://crontab.guru/#0_4_*_*_*
    # Default: At 04:00.
    index_update_schedule: str = "0 4 * * *"
    # Schedule for crates cache cleanup https://crontab.guru/#0_3_*_*_*
    # Default: At 03:00.
    cleanup_cache_schedule: str = "0 3 * * *"
    # Cached files will be deleted if they have not been used within the given timeframe
    # Default: about half a year
    max_days_unused: int = 30 * 6

    class Config:
        env_prefix = "cratere_"


settings = Settings()
