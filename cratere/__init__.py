import json
import fcntl
import subprocess
from pathlib import Path

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, FileResponse
import httpx
from pydantic import BaseModel, Field


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


async def download_crates_index():
    """The crates index is just a git repo, clone it!"""
    index_path = Path("crates.io-index-master")
    if index_path.exists():
        print("Index already downloaded")
        return

    print("Downloading crates.io index using git clone ...")
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
    print("Done")


def read_crates_config() -> CratesConfigModel:
    """See https://doc.rust-lang.org/cargo/reference/registries.html#index-format"""
    index_path = Path("crates.io-index-master")
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
    index_path = Path("crates.io-index-master")
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
    index_path = anyio.Path("crates.io-index-master")
    # TODO: Read these from env variables
    crates_config_model = CratesConfigModel(
        dl="http://172.17.0.1:8000/api/v1/crates", api="http://172.17.0.1:8000"
    )
    if crates_config_model == read_crates_config():
        return

    print("Writing crates config", crates_config_model)
    config_path = index_path / "config.json"
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
    await download_crates_index()
    await write_crates_config()
    crates_config = read_crates_config()
    print("Started with crates config", crates_config)


@app.get("/crates.io-index/info/refs")
async def get_index_refs(request: Request):
    """See https://git-scm.com/docs/http-protocol"""

    async def stream_pack_local_index():
        index_path = Path("crates.io-index-master")
        async with await anyio.open_process(
            ["git", "upload-pack", "--http-backend-info-refs", str(index_path)]
        ) as process:
            yield b"001e# service=git-upload-pack\n0000"
            async for chunk in BufferedByteReceiveStream(process.stdout):
                yield chunk

    return StreamingResponse(
        stream_pack_local_index(),
        headers={"content-type": "application/x-git-upload-pack-advertisement"},
    )


@app.post("/crates.io-index/git-upload-pack")
async def post_index_upload_pack(request: Request):
    """See https://git-scm.com/docs/http-protocol"""
    body = await request.body()

    async def stream_pack_local_index():
        index_path = Path("crates.io-index-master")
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

    storage = anyio.Path("storage")
    await storage.mkdir(exist_ok=True)

    cached_file_path = storage / name / version
    if await cached_file_path.exists():
        print(f"Serving {cached_file_path} from cache!")
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
                async with await anyio.open_file(part_path, "wb") as f:
                    # Lock partial file to avoid concurrency issues
                    await anyio.to_thread.run_sync(fcntl.lockf, f, fcntl.LOCK_EX)
                    async for chunk in r.aiter_bytes():
                        yield chunk
                        # Write to cache too!
                        await f.write(chunk)
                await part_path.rename(cached_file_path)

    return StreamingResponse(stream_remote_download())


def main():
    anyio.run(run)
