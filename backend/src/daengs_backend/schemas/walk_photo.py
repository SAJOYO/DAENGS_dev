"""Photo metadata only. No bytes, file paths, download URLs or image interpretation."""

import uuid
from typing import Literal

from pydantic import Field, model_validator

from daengs_walk.diary.contracts.input import DiaryContract, Instant, Point


class PhotoMetadata(DiaryContract):
    id: uuid.UUID
    captured_at: Instant
    location_captured_at: Instant
    point: Point
    accuracy_m: float = Field(ge=0)

    @model_validator(mode="after")
    def sample_time(self):
        if self.location_captured_at > self.captured_at:
            raise ValueError("photo location cannot come from after the shutter")
        return self


class PhotoManifestWrite(DiaryContract):
    format: Literal["walk-photo-metadata-v1"] = "walk-photo-metadata-v1"
    publisher_id: uuid.UUID
    revision: int = Field(ge=1, le=2_147_483_647)
    expected_revision: int = Field(ge=0, le=2_147_483_647)
    photos: tuple[PhotoMetadata, ...] = Field(max_length=200)

    @model_validator(mode="after")
    def unique(self):
        if self.revision <= self.expected_revision:
            raise ValueError("publisher revision must advance")
        if len({p.id for p in self.photos}) != len(self.photos):
            raise ValueError("duplicate photo")
        return self


class PhotoRecord(DiaryContract):
    id: uuid.UUID
    revision: int = Field(ge=1)
    content: PhotoMetadata | None

    @model_validator(mode="after")
    def identity(self):
        if self.content is not None and self.content.id != self.id:
            raise ValueError("photo record identity mismatch")
        return self


class PhotoManifestResponse(DiaryContract):
    format: Literal["walk-photo-metadata-v1"] = "walk-photo-metadata-v1"
    client_session_id: uuid.UUID
    status: Literal["complete", "not_available"]
    publisher_id: uuid.UUID | None
    revision: int = Field(ge=0)
    records: tuple[PhotoRecord, ...]
