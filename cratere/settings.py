import functools
import pathlib
from typing import Literal, Any

from pydantic import BaseSettings, Field

__all__ = [
    "Settings",
    "settings",
]


class Settings(BaseSettings):
    host: str
    scheme: Literal["http", "https"] = "http"
    port: int | None = None
    alternate_hosts: list[str] = Field(default_factory=list)
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


@functools.cache
def settings() -> Settings:
    """
    Use simple caching function to make sure settings are not created on import.
    """
    return Settings()
