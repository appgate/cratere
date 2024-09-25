import anyio
import functools
from typing import Literal, Annotated
from pydantic import Field, PlainSerializer, PlainValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "Settings",
    "settings",
]


FromPath = PlainValidator(lambda x: anyio.Path(x))
ToPath = PlainSerializer(str)


class Settings(BaseSettings):
    host: str
    scheme: Literal["http", "https"] = "http"
    port: int | None = None
    alternate_hosts: list[str] = Field(default_factory=list)
    index: Annotated[anyio.Path, FromPath, ToPath] = anyio.Path(
        "crates.io-index-master"
    )
    cache: Annotated[anyio.Path, FromPath, ToPath] = anyio.Path("storage")
    # Schedule for crates index update https://crontab.guru/#0_4_*_*_*
    # Default: At 04:00.
    index_update_schedule: str = "0 4 * * *"
    # Schedule for crates cache cleanup https://crontab.guru/#0_3_*_*_*
    # Default: At 03:00.
    cleanup_cache_schedule: str = "0 3 * * *"
    # Cached files will be deleted if they have not been used within the given timeframe
    # Default: about half a year
    max_days_unused: int = 30 * 6

    model_config = SettingsConfigDict(
        env_prefix="cratere_",
        env_nested_delimiter="__",
    )


@functools.cache
def settings() -> Settings:
    """
    Use simple caching function to make sure settings are not created on import.
    """
    return Settings()
