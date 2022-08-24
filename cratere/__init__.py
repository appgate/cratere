import asyncio
import errno
import json
import datetime
import logging
import os
import subprocess
import time
from typing import Literal
from pathlib import Path

import acron
import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse
import httpx
from pydantic import BaseModel, Field, BaseSettings


log = logging.getLogger("purple-crl-updater")
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z"
)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
log.addHandler(stream_handler)
log.setLevel(logging.INFO)


class Settings(BaseSettings):
    scheme: Literal["http", "https"] = "http"
    host: str = "172.17.0.1"
    port: int = 8000
    index: Path = Path("crates.io-index-master")
    cache: Path = Path("storage")
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
app = FastAPI()


class CratesConfigModel(BaseModel):
    dl: str
    api: str


class PackageDepModel(BaseModel):
    name: str
    req: str
    features: list[str]
    optional: bool
    default_features: bool
    target: str | None
    kind: str
    registry: str | None
    package: str | None


class PackageMetadataModel(BaseModel):
    name: str
    vers: str
    deps: list[PackageDepModel]
    cksum: str
    features: dict[str, list[str]]
    yanked: bool
    links: list[str] | None
    features2: dict[str, list[str]] = Field(default_factory=dict)


async def download_crates_index(index_path: Path) -> None:
    """The crates index is just a git repo, clone it!"""
    if index_path.exists():
        log.info("Index already downloaded")
        return

    log.info("Downloading crates.io index using git clone ...")
    # cargo doens't like a shallow copy
    await anyio.run_process(
        [
            "git",
            "clone",
            "https://github.com/rust-lang/crates.io-index.git",
            "--single-branch",
            str(index_path),
        ]
    )
    log.info("Downloaded crates.io index to %s", index_path)


async def update_crates_index(index_path: Path) -> None:
    # Resolve symlink to current index
    if index_path.exists():
        assert index_path.is_symlink(), "index path should be a symlink"

    # Use suffix based on current datetime with iso accuracy, e.g. 2022-08-19T13:40:33
    suffix = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()[:-13]
    new_index = index_path.parent / f"{index_path.name}.{suffix}"

    # Download new index
    await download_crates_index(new_index)
    await write_crates_config(anyio.Path(new_index))

    # Atomically replace symlink to point to new index
    new_index_link = new_index.parent / f"{new_index}.name.lnk"
    cmd = ["ln", "-s", str(new_index), str(new_index_link)]
    await anyio.run_process(cmd, check=True)
    cmd = ["mv", "-T", str(new_index_link), str(index_path)]
    await anyio.run_process(cmd, check=True)
    log.info("Index %s updated to %s", index_path, new_index)


def read_crates_config(index_path: Path) -> CratesConfigModel:
    """See https://doc.rust-lang.org/cargo/reference/registries.html#index-format"""
    config_path = index_path / "config.json"
    with config_path.open("rb") as f:
        config = json.load(f)
    config_model = CratesConfigModel(**config)
    return config_model


def read_package_metadata(name: str) -> list[PackageMetadataModel]:
    """See https://doc.rust-lang.org/cargo/reference/registries.html#index-format

    - Packages with 1 character names are placed in a directory named 1.
    - Packages with 2 character names are placed in a directory named 2.
    - Packages with 3 character names are placed in the directory 3/{first-character} where {first-character} is the first character of the package name.
    - All other packages are stored in directories named {first-two}/{second-two} where the top directory is the first two characters of the package name, and the next subdirectory is the third and fourth characters of the package name. For example, cargo would be stored in a file named ca/rg/cargo.
    """
    assert len(name) > 0
    index_path = settings.index
    if len(name) <= 3:
        metadata_path = index_path / str(len(name)) / name
    else:
        metadata_path = index_path / name[:2] / name[2:4] / name

    metadata = []
    with metadata_path.open("rb") as f:
        for line in f:
            metadata.append(PackageMetadataModel(**json.loads(line)))
    return metadata


async def write_crates_config(index_path: anyio.Path) -> None:
    """Override the default crates.io config to point to this proxy instead"""
    crates_config_model = CratesConfigModel(
        dl=f"{settings.scheme}://{settings.host}:{settings.port}/api/v1/crates",
        api=f"{settings.scheme}://{settings.host}:{settings.port}",
    )
    if crates_config_model == read_crates_config(Path(index_path)):
        return

    config_path = index_path / "config.json"
    log.info("Writing crates config %s to %s", crates_config_model, config_path)
    await config_path.write_text(
        json.dumps(crates_config_model.dict(), indent=4) + "\n"
    )
    await anyio.run_process(
        [
            "git",
            "--git-dir",
            str(index_path / ".git"),
            "--work-tree",
            str(index_path),
            "commit",
            "-m",
            "update config.json for cratere",
            config_path.name,
        ],
        env={
            "GIT_AUTHOR_NAME": "cratere",
            "GIT_AUTHOR_EMAIL": "cratere@noreply.appgate.com",
            "GIT_COMMITTER_NAME": "cratere",
            "GIT_COMMITTER_EMAIL": "cratere@noreply.appgate.com",
        },
    )


class CleanupCacheData(BaseModel):
    cache_dir: anyio.Path
    max_days_unused: int


async def cleanup_cache(data: CleanupCacheData) -> None:
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
        crate_dir_stat = await crate_dir_path.lstat()
        dir_time_unused = current_time - crate_dir_stat.st_atime
        if dir_time_unused <= max_seconds_unused:
            continue

        # Crate directory has not been visited in a while, let's look deeper
        files_visited = 0
        files_deleted = 0
        async for crate_path in crate_dir_path.iterdir():
            files_visited += 1
            crate_stat = await crate_path.lstat()
            time_unused = current_time - crate_stat.st_atime
            if (current_time - crate_stat.st_atime) > max_seconds_unused:
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


@app.on_event("startup")
async def run() -> None:
    if not settings.index.exists():
        # Update crates index if it doesn't exist, then update it on a schedule.
        await update_crates_index(settings.index)

    # Write crates config in case it has change since last start
    await write_crates_config(anyio.Path(settings.index))

    crates_config = read_crates_config(settings.index)
    log.info("Started with crates config %s", crates_config)

    log.info(
        "Will update crates index on the following cron schedule: %s",
        settings.index_update_schedule,
    )
    update_crates_index_job = acron.Job(
        name="Update crates index",
        schedule=settings.index_update_schedule,
        func=update_crates_index,
        data=settings.index,
    )
    cleanup_cache_job = acron.Job(
        name="Cleanup crates cache",
        schedule=settings.cleanup_cache_schedule,
        func=cleanup_cache,
        data=CleanupCacheData(
            cache_dir=anyio.Path(settings.cache),
            max_days_unused=settings.max_days_unused,
        ),
    )
    jobs: set[acron.Job] = {update_crates_index_job, cleanup_cache_job}
    asyncio.create_task(acron.run(jobs))


@app.get("/crates.io-index/info/refs")
async def get_index_refs(request: Request):
    """See https://git-scm.com/docs/http-protocol"""

    async def stream_pack_local_index():
        index_path = settings.index
        cmd = ["git", "upload-pack", "--http-backend-info-refs", str(index_path)]
        completed_process = await anyio.run_process(cmd, check=True)
        # Header for git http protocol
        yield b"001e# service=git-upload-pack\n0000"
        # Content coming from upload-pack
        yield completed_process.stdout

    return StreamingResponse(
        stream_pack_local_index(),
        headers={"content-type": "application/x-git-upload-pack-advertisement"},
    )


@app.post("/crates.io-index/git-upload-pack")
async def post_index_upload_pack(request: Request):
    """See https://git-scm.com/docs/http-protocol"""
    body = await request.body()

    async def stream_pack_local_index():
        index_path = settings.index
        async with await anyio.open_process(
            ["git", "upload-pack", str(index_path)], stdin=subprocess.PIPE
        ) as process:
            await process.stdin.send(body)
            async for chunk in BufferedByteReceiveStream(process.stdout):
                yield chunk

    return StreamingResponse(
        stream_pack_local_index(),
        headers={"content-type": "application/x-git-upload-pack-result"},
    )


@app.get("/api/v1/crates/{name}/{version}/download")
async def get_crate(name: str, version: str, request: Request):
    """Serve crate download."""
    body = await request.body()

    storage = anyio.Path(settings.cache)
    await storage.mkdir(exist_ok=True)

    cached_file_path = storage / name / version
    if await cached_file_path.exists():
        log.info("Serving %s/%s from cached file %s", name, version, cached_file_path)
        return FileResponse(cached_file_path)

    async def stream_remote_download():
        async with httpx.AsyncClient() as client:
            r = await client.get(f"https://crates.io{request.url.path}")
            assert r.status_code == 302

            location = r.headers["location"]
            async with client.stream("GET", location) as r:
                r.raise_for_status()
                await (storage / name).mkdir(exist_ok=True)
                part_path = cached_file_path.with_suffix(".part")
                try:
                    async with await anyio.open_file(part_path, "xb") as f:
                        log.debug("Writing cached crate to %s", part_path)
                        async for chunk in r.aiter_bytes():
                            yield chunk
                            # Write to cache too!
                            await f.write(chunk)
                    log.debug(
                        "Moving cached crate from %s to %s", part_path, cached_file_path
                    )
                    await part_path.rename(cached_file_path)
                except FileExistsError:
                    # Cached file is already being downloaded, just stream directly bypassing caching logic.
                    log.debug("Crate already being cached to %s", part_path)
                    async for chunk in r.aiter_bytes():
                        yield chunk

    return StreamingResponse(stream_remote_download())


def main():
    anyio.run(run)
