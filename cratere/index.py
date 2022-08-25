import datetime
import json
import shutil

import anyio
from pydantic import BaseModel, Field

from cratere.logger import log
from cratere.settings import settings

__all__ = [
    "download_crates_index",
    "update_crates_index",
    "read_crates_config",
    "write_creates_config",
    "read_package_metadata",
]


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


async def download_crates_index(index_path: anyio.Path) -> None:
    """The crates index is just a git repo, clone it!"""
    if await index_path.exists():
        log.info("Index already downloaded")
        return

    log.info("Downloading crates.io index using git clone ...")
    # cargo doesn't like a shallow copy
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


async def update_crates_index(index_path: anyio.Path) -> None:
    # Resolve symlink to current index
    if await index_path.exists():
        assert await index_path.is_symlink(), "index path should be a symlink"

    # Use suffix based on current datetime with iso accuracy, e.g. 2022-08-19T13:40:33
    suffix = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()[:-13]
    new_index = index_path.parent / f"{index_path.name}.{suffix}"

    # Download new index
    await download_crates_index(new_index)
    await write_crates_config(anyio.Path(new_index))

    # Save previous index path for later
    previous_index = await index_path.resolve()

    # Atomically replace symlink to point to new index
    new_index_link = new_index.parent / f"{new_index}.name.lnk"
    cmd = ["ln", "-s", str(new_index), str(new_index_link)]
    await anyio.run_process(cmd, check=True)
    cmd = ["mv", "-T", str(new_index_link), str(index_path)]
    await anyio.run_process(cmd, check=True)
    log.info("Index %s updated to %s", index_path, new_index)

    # Get rid of older index directories,
    # don't delete the one we just replaced as it can still be in use.
    async for possible_index_path in anyio.Path(index_path.parent).iterdir():
        if await possible_index_path.is_symlink():
            # Don't touch symlinks
            continue

        if possible_index_path.name in (previous_index.name, new_index.name):
            # Don't touch the current and previous index directories
            continue

        log.info("Deleting old index directory %s", possible_index_path)
        await anyio.to_thread.run_sync(shutil.rmtree, possible_index_path)


async def read_crates_config(index_path: anyio.Path) -> CratesConfigModel:
    """See https://doc.rust-lang.org/cargo/reference/registries.html#index-format"""
    config_path = index_path / "config.json"
    config = json.loads(await config_path.read_bytes())
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
    if crates_config_model == await read_crates_config(index_path):
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
