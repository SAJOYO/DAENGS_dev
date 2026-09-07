"""킁킁·배설·짖기와 자유 메모의 공개 계약."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntryLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    captured_at: datetime
    accuracy_m: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def aware(self):
        if self.captured_at.utcoffset() is None:
            raise ValueError("위치 시각에는 timezone이 필요합니다.")
        return self


class EntryContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vocabulary_version: Literal["walk-behavior-v1"] = "walk-behavior-v1"
    kind: Literal["behavior", "note"]
    behavior_code: Literal["sniffing", "excretion", "barking"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    recorded_at: datetime
    location: EntryLocation | None = None
    pet_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def valid_content(self):
        if self.recorded_at.utcoffset() is None:
            raise ValueError("기록 시각에는 timezone이 필요합니다.")
        if self.kind == "behavior":
            if self.behavior_code is None or self.location is None or self.note is not None:
                raise ValueError("행동에는 종류와 위치가 필요하며 메모는 넣지 않습니다.")
        else:
            if self.behavior_code is not None or not self.note or not self.note.strip():
                raise ValueError("메모에는 본문이 필요하며 행동 종류는 넣지 않습니다.")
            self.note = self.note.strip()
            if self.pet_id is not None:
                raise ValueError("자유 메모는 산책 전체의 기록입니다.")
        return self


class EntryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    mutation_id: uuid.UUID
    content: EntryContent


class EntryResponse(BaseModel):
    id: uuid.UUID
    revision: int
    mutation_id: uuid.UUID
    content: EntryContent | None


class EntryList(BaseModel):
    entries: list[EntryResponse]


class RecordProfileQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pet_id: uuid.UUID
    since: datetime | None = None
    until: datetime | None = None

    @model_validator(mode="after")
    def valid_period(self):
        for value in (self.since, self.until):
            if value is not None and value.utcoffset() is None:
                raise ValueError("기간에는 timezone이 필요합니다.")
        if self.since and self.until and self.since >= self.until:
            raise ValueError("조회 기간의 끝은 시작 뒤여야 합니다.")
        return self


class BehaviorCount(BaseModel):
    entry_count: int = Field(ge=0)
    walks_with_entries: int = Field(ge=0)


class RecordEvidence(EntryContent):
    entry_id: uuid.UUID
    entry_revision: int
    walk_id: uuid.UUID
    context_status: Literal["not_requested"] = "not_requested"
    context_refs: list[str] = Field(default_factory=list)


class RecordProfileResponse(BaseModel):
    profile_version: Literal["walk-record-profile-v0"] = "walk-record-profile-v0"
    vocabulary_version: Literal["walk-behavior-v1"] = "walk-behavior-v1"
    pet_id: uuid.UUID
    period: dict[str, datetime | None]
    generated_at: datetime
    source_revision: str
    walk_count: int
    unassigned_entry_count: int
    behaviors: dict[Literal["sniffing", "excretion", "barking"], BehaviorCount]
    evidence: list[RecordEvidence]
