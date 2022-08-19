import anyio
from typing import Final
from pathlib import Path
import zipfile

import httpx

CRATES_IO_INDEX_URL: Final = (
    "https://github.com/rust-lang/crates.io-index/archive/refs/heads/master.zip"
)


async def download_index():
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
                    print(f"Downloading {int(content_length) // 1024**2} MiB to {zip_path} ...")
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


async def run():
    await download_index()


def main():
    anyio.run(run)
