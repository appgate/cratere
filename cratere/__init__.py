import json
import fcntl
import logging
import subprocess
from typing import Literal
from pathlib import Path

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from fastapi import FastAPI, Request, HTTPException, Response
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


def read_crates_config() -> CratesConfigModel:
    """See https://doc.rust-lang.org/cargo/reference/registries.html#index-format"""
    config_path = settings.index / "config.json"
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


async def write_crates_config() -> None:
    """Override the default crates.io config to point to this proxy instead"""
    index_path = anyio.Path(settings.index)
    crates_config_model = CratesConfigModel(
        dl=f"{settings.scheme}://{settings.host}:{settings.port}/api/v1/crates",
        api=f"{settings.scheme}://{settings.host}:{settings.port}",
    )
    if crates_config_model == read_crates_config():
        return

    config_path = index_path / "config.json"
    log.info("Writing crates config %s to %s", crates_config_model, config_path)
    await config_path.write_text(json.dumps(crates_config_model.dict(), indent=4))
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


@app.on_event("startup")
async def run():
    await download_crates_index(settings.index)
    await write_crates_config()
    crates_config = read_crates_config()
    log.info("Started with crates config %s", crates_config)


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
                log.debug("Writing cached crate to %s", part_path)
                async with await anyio.open_file(part_path, "wb") as f:
                    # Lock partial file to avoid concurrency issues
                    await anyio.to_thread.run_sync(fcntl.lockf, f, fcntl.LOCK_EX)
                    async for chunk in r.aiter_bytes():
                        yield chunk
                        # Write to cache too!
                        await f.write(chunk)
                log.debug(
                    "Moving cached crate from %s to %s", part_path, cached_file_path
                )
                await part_path.rename(cached_file_path)

    return StreamingResponse(stream_remote_download())


def main():
    anyio.run(run)
