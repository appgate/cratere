import json
import fcntl
import subprocess
from typing import Final
from pathlib import Path
import zipfile

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, FileResponse
import httpx
from pydantic import BaseModel, Field


app = FastAPI()


CRATES_IO_INDEX_URL: Final = (
    "https://github.com/rust-lang/crates.io-index/archive/refs/heads/master.zip"
)


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


async def download_index():
    """In theory the index is a git repo, we just use the fact that git can provide
    the current view of a git repo as a zip instead to simplify things.
    """
    index_path = Path("crates.io-index-master")
    if index_path.exists():
        print("Index already downloaded")
        return

    async with httpx.AsyncClient() as client:
        print(f"Downloading crates.io index from {CRATES_IO_INDEX_URL} ...")

        # Get redirect URL
        r = await client.get(CRATES_IO_INDEX_URL)
        assert r.status_code == 302
        location = r.headers["location"]
        print(f"Redirected to location {location}")

        # Download index zip
        async with client.stream("GET", location) as r:
            r.raise_for_status()

            # 'content-disposition': 'attachment; filename=crates.io-index-master.zip'
            content_disposition = r.headers["content-disposition"]
            filename = content_disposition.removeprefix("attachment; filename=")
            zip_path = Path(Path(filename).name)
            assert zip_path.suffix == ".zip"

            match r.headers:
                case {"content-length": content_length}:
                    print(
                        f"Downloading {int(content_length) // 1024**2} MiB to {zip_path} ..."
                    )
                case _:
                    print(f"Downloading to {zip_path} ...")

            with zip_path.open("wb") as f:
                async for chunk in r.aiter_bytes():
                    f.write(chunk)

        # Extract index zip
        dir_path = Path("index")
        print(f"Extracting {zip_path} to {dir_path} ...")
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(dir_path)
        (dir_path / index_path.name).rename(index_path)
        dir_path.rmdir()
        print(f"Done ... index now available at {index_path}")


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


@app.on_event("startup")
async def run():
    await download_index()
    regex_metadata = read_package_metadata("regex")
    uuid_metadata = read_package_metadata("uuid")

    crates_config = read_crates_config()
    print("CRATES CONFIG IS", crates_config)
    for m in regex_metadata:
        print(m)


@app.get("/crates.io-index/info/refs")
async def get_index_refs(request: Request):
    """See https://git-scm.com/docs/http-protocol"""
    # async with httpx.AsyncClient() as client:
    #    r = await client.get(f"https://github.com/rust-lang{request.url.path}", params=request.query_params)
    #    print("HEADERS", r.headers)
    #    print("CONTENT", r.content)
    # raise HTTPException(status_code=400, detail="I don't understand!!!")
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
