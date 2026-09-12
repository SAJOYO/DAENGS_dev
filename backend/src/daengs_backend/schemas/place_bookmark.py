"""Private bookmark envelopes. Place validates search/fact semantics across HTTP."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BookmarkKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["kcisa", "kto", "public:mois:animal_hospital", "public:mois:animal_pharmacy"]
    ref: str = Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")


class BookmarkItem(BaseModel):
    key: BookmarkKey
    name: str
    created_at: datetime


class BookmarkList(BaseModel):
    contract_version: Literal["place-bookmarks-v1"] = "place-bookmarks-v1"
    total_count: int
    limit: int
    items: list[BookmarkItem]


class BookmarkSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("filters")
    @classmethod
    def bounded_filters(cls, value):
        if len(json.dumps(value)) > 32000:
            raise ValueError("filters too large")
        return value


class BookmarkSearchResult(BookmarkList):
    filters: dict[str, Any]
    distance_available: bool
    hits: list[dict[str, Any]]
    missing_keys: list[BookmarkKey]


class BookmarkInterpret(BookmarkSearch):
    query: str = Field(min_length=1, max_length=1000)
    search_policy: Literal["v1"] | None = None
    candidate_pools: Literal["v1"] | None = None


class BookmarkInterpretResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["search", "clarify", "explain", "return_search", "search_places"]
    message: str
    filters: dict[str, Any] | None = None
    search_filters: dict[str, Any] | None = None
    search_pool: Literal["all_places", "unbookmarked", "new_candidates"] = "all_places"
