from pathlib import Path
import os

import anyio
import pytest

from cratere.cache import CleanupCacheData, cleanup_cache


@pytest.mark.asyncio
async def test_cleanup_cache(tmp_path: Path) -> None:

    (tmp_path / "bar").mkdir()
    (tmp_path / "buzz").mkdir()
    (tmp_path / "foo-1").mkdir()
    (tmp_path / "foo-2").mkdir()
    (tmp_path / "foo-3").mkdir()
    (tmp_path / "foo").symlink_to((tmp_path / "foo-3"))

    data = CleanupCacheData(cache_dir=anyio.Path(tmp_path), max_days_unused=2)

    async def last_access_time(path: anyio.Path) -> float:
        stat = await path.lstat()
        if path.name == "bar":
            return (stat.st_atime - (data.max_days_unused * 3600 * 24.0)) - 60.0
        return stat.st_atime

    await cleanup_cache(data, last_access_time=last_access_time)

    assert not (tmp_path / "bar").exists()
    assert (tmp_path / "buzz").exists()
    assert (tmp_path / "foo-1").exists()
    assert (tmp_path / "foo-2").exists()
    assert (tmp_path / "foo-3").exists()
    assert (tmp_path / "foo").exists()
