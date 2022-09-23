import asyncio
import functools
import subprocess

import acron
import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse
import httpx

from cratere.settings import settings
from cratere.logger import log
from cratere.index import (
    update_crates_index,
    read_crates_config,
    write_crates_configs,
    alternate_index_path,
)
from cratere.cache import cleanup_cache, CleanupCacheData

__all__ = ["main"]

app = FastAPI()


@app.on_event("startup")
async def run() -> None:
    index_path = anyio.Path(settings.index)

    if not await index_path.exists():
        # Update crates index if it doesn't exist, then update it on a schedule.
        await update_crates_index(index_path, settings.alternate_hosts)

    # Write crates config in case it has change since last start
    await write_crates_configs(anyio.Path(settings.index))

    crates_config = await read_crates_config(index_path)
    log.info("Started with crates config %s", crates_config)
    for host in settings.alternate_hosts:
        alternate_crates_config = await read_crates_config(
            alternate_index_path(index_path, host)
        )
        log.info(
            "Started alternate host with creates config %s", alternate_crates_config
        )

    log.info(
        "Will update crates index on the following cron schedule: %s",
        settings.index_update_schedule,
    )
    update_crates_index_job = acron.Job(
        name="Update crates index",
        schedule=settings.index_update_schedule,
        func=functools.partial(
            update_crates_index, alternate_hosts=settings.alternate_hosts
        ),
        data=index_path,
    )
    log.info(
        "Will cleanup crates cache on the following cron schedule: %s",
        settings.cleanup_cache_schedule,
    )
    cleanup_cache_job = acron.Job(
        name="Cleanup crates cache",
        schedule=settings.cleanup_cache_schedule,
        func=cleanup_cache,
        data=CleanupCacheData(
            cache_dir=index_path,
            max_days_unused=settings.max_days_unused,
        ),
    )
    jobs: set[acron.Job] = {update_crates_index_job, cleanup_cache_job}
    asyncio.create_task(acron.run(jobs))


async def _resolve_index_path(host: str | None) -> anyio.Path:
    index_path = anyio.Path(settings.index)
    if host and host in settings.alternate_hosts:
        index_path = alternate_index_path(index_path, host)
        log.info("Using alternate index path %s", index_path)
    return await index_path.resolve()


@app.get("/crates.io-index/info/refs")
async def get_index_refs(request: Request):
    """See https://git-scm.com/docs/http-protocol"""
    index_path = await _resolve_index_path(request.headers.get("host"))

    async def stream_pack_local_index():
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
    index_path = await _resolve_index_path(request.headers.get("host"))

    async def stream_pack_local_index():
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
    _ = await request.body()

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
