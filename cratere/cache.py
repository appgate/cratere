import dataclasses
import errno
import os
import time
from typing import Callable, Awaitable

import anyio

from cratere.logger import log
from cratere.settings import settings

__all__ = [
    "cleanup_cache",
    "CleanupCacheData",
]


@dataclasses.dataclass(frozen=True)
class CleanupCacheData:
    cache_dir: anyio.Path
    max_days_unused: int


async def path_st_atime(path: anyio.Path) -> float:
    stat = await path.lstat()
    return stat.st_atime


async def cleanup_cache(
    data: CleanupCacheData,
    last_access_time: Callable[[anyio.Path], Awaitable[float]] = path_st_atime,
) -> None:
    """
    Cleanup cached artifacts that have not been used for at most x days.
    """
    max_days_unused = data.max_days_unused
    cache_dir = data.cache_dir

    log.info(
        "Starting cleanup of cache directory with %d max days unused", max_days_unused
    )

    vfs_stats = await anyio.to_thread.run_sync(os.statvfs, cache_dir)

    if vfs_stats.f_flag & os.ST_NOATIME > 0:
        raise ValueError(
            "crates cache cleanup requires atime or relatime enabled on the filesystem"
        )

    if vfs_stats.f_flag & os.ST_RELATIME > 0 and max_days_unused < 2:
        raise ValueError(
            "crates cache cleanup only has a precision of 1 day"
            " when relatime is enabled on the filesystem"
        )

    current_time = time.time()
    seconds_in_day = 3600 * 24
    max_seconds_unused = max_days_unused * seconds_in_day
    async for crate_dir_path in anyio.Path(cache_dir).iterdir():
        crate_dir_last_access_time = await last_access_time(crate_dir_path)
        dir_time_unused = current_time - crate_dir_last_access_time
        if dir_time_unused <= max_seconds_unused:
            continue

        # Crate directory has not been visited in a while, let's look deeper
        files_visited = 0
        files_deleted = 0
        async for crate_path in crate_dir_path.iterdir():
            files_visited += 1
            crate_last_access_time = await last_access_time(crate_path)
            time_unused = current_time - crate_last_access_time
            if (current_time - crate_last_access_time) > max_seconds_unused:
                # Crate has not been used for a while, delete it!
                log.info(
                    "Deleting crate %s/%s, unused for %d days",
                    crate_dir_path.name,
                    crate_path.name,
                    time_unused // seconds_in_day,
                )
                await crate_path.unlink()
                files_deleted += 1

        # All files in crate directory were deleted, remove the crate directory as well.
        if files_visited == files_deleted:
            try:
                await crate_dir_path.rmdir()
            except OSError as e:
                if e.errno == errno.ENOTEMPTY:
                    # Looks like the directory isn't empty anymore
                    pass
            log.info(
                "Deleted crate directory %s, unused for %d days",
                crate_dir_path.name,
                dir_time_unused // seconds_in_day,
            )
