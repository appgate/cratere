from typing import List, Any, Optional
from pydantic import BaseModel

__all__ = [
    "CrateLinksModel",
    "CrateShowModel",
    "CrateSearchMetadataModel",
    "CrateSearchModel",
]


class CrateLinksModel(BaseModel):
    owner_team: str
    owner_user: str
    owners: str
    reverse_dependencies: str
    version_downloads: str
    versions: str


class CrateShowModel(BaseModel):
    badges: List
    categories: Any
    created_at: str
    description: str
    documentation: Optional[str]
    downloads: int
    exact_match: bool
    homepage: Optional[str]
    id: str
    keywords: Any
    links: CrateLinksModel
    max_stable_version: Optional[str]
    max_version: str
    name: str
    newest_version: str
    recent_downloads: int
    repository: Optional[str]
    updated_at: str
    versions: Any


class CrateSearchMetadataModel(BaseModel):
    next_page: str
    prev_page: Any
    total: int


class CrateSearchModel(BaseModel):
    """
    API result of cargo search.

    See https://doc.rust-lang.org/cargo/commands/cargo-search.html
    """

    crates: List[CrateShowModel]
    meta: CrateSearchMetadataModel
