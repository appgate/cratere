import anyio
import functools
from typing import Literal

from pydantic import BaseSettings, Field, validator

__all__ = [
    "Settings",
    "settings",
]


class Settings(BaseSettings):
    host: str
    scheme: Literal["http", "https"] = "http"
    port: int | None = None
    alternate_hosts: list[str] = Field(default_factory=list)
    index: anyio.Path = anyio.Path("crates.io-index-master")
    cache: anyio.Path = anyio.Path("storage")
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
        json_encoders = {anyio.Path: str}

    @validator("index", "cache", pre=True)
    def anyio_path_validate(cls, v):
        """
        pydantic is not great for custom type handlers...
        """
        return anyio.Path(v)


@functools.cache
def settings() -> Settings:
    """
    Use simple caching function to make sure settings are not created on import.
    """
    return Settings()
