"""Consumer-owned schema for the place-search discovery response.

The backend and place-search run independently.  These models intentionally describe only the
fields the assistant boundary consumes and ignore provider material, raw source records, search
plans, and policy traces.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _InternalModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _PlaceRef(_InternalModel):
    source: str = Field(min_length=1, max_length=120)
    ref: str = Field(min_length=1, max_length=300)


class _Link(_InternalModel):
    state: str = Field(min_length=1, max_length=40)


class _Provenance(_InternalModel):
    source: _PlaceRef
    source_role: str = Field(min_length=1, max_length=40)
    value_origin: str = Field(min_length=1, max_length=40)
    link: _Link | None = None


class _PresentationFact(_InternalModel):
    fact_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=80)
    display_text: str = Field(min_length=1, max_length=500)
    source_state: str = Field(min_length=1, max_length=40)
    evaluation_state: str = Field(min_length=1, max_length=40)
    severity: str = Field(min_length=1, max_length=40)
    provenance: _Provenance


class _PresentationNotice(_InternalModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=500)
    severity: str = Field(min_length=1, max_length=40)


class _WhyMatched(_InternalModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=500)


class _Presentation(_InternalModel):
    place_key: _PlaceRef
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=500)
    kind_id: str = Field(min_length=1, max_length=80)
    kind_label: str = Field(min_length=1, max_length=80)
    distance_m: int = Field(ge=0)
    address: str | None = Field(None, max_length=500)
    core_items: list[_PresentationFact] = Field(default_factory=list, max_length=30)
    promoted_items: list[_PresentationFact] = Field(default_factory=list, max_length=30)
    detail_items: list[_PresentationFact] = Field(default_factory=list, max_length=30)
    notices: list[_PresentationNotice] = Field(default_factory=list, max_length=30)
    why_matched: list[_WhyMatched] = Field(default_factory=list, max_length=30)


class _SearchPlace(_InternalModel):
    key: _PlaceRef
    lat: float = Field(ge=32, le=40)
    lng: float = Field(ge=123, le=133)


class _SearchHit(_InternalModel):
    place: _SearchPlace


class _SearchGroup(_InternalModel):
    results: list[_SearchHit] = Field(default_factory=list)


class _Search(_InternalModel):
    groups: list[_SearchGroup] = Field(default_factory=list)


class _LensResult(_InternalModel):
    lens_id: str = Field(min_length=1, max_length=160)
    display_label: str = Field(min_length=1, max_length=80)
    support_note: str = Field(min_length=1, max_length=300)
    search: _Search
    presentations: list[_Presentation] = Field(default_factory=list)

    @model_validator(mode="after")
    def presentations_match_hits(self) -> _LensResult:
        hits = [hit for group in self.search.groups for hit in group.results]
        if len(hits) != len(self.presentations):
            raise ValueError("Place presentations do not match search hits")
        if any(item.place_key != hit.place.key for item, hit in zip(self.presentations, hits)):
            raise ValueError("Place presentation identity does not match its search hit")
        return self


class _TargetLens(_InternalModel):
    lens_id: str = Field(min_length=1, max_length=160)
    display_label: str = Field(min_length=1, max_length=80)
    mapping_scope: str = Field(min_length=1, max_length=40)
    availability: str = Field(min_length=1, max_length=40)
    support_note: str = Field(min_length=1, max_length=300)


class _FacetOption(_InternalModel):
    option_id: str = Field(min_length=1, max_length=120)
    display_label: str = Field(min_length=1, max_length=80)
    availability: str = Field(min_length=1, max_length=40)
    support_note: str = Field(min_length=1, max_length=300)


class _SignalLens(_InternalModel):
    lens_id: str = Field(min_length=1, max_length=160)
    display_label: str = Field(min_length=1, max_length=80)
    availability: str = Field(min_length=1, max_length=40)
    required: bool
    support_note: str = Field(min_length=1, max_length=300)
    options: list[_FacetOption] = Field(default_factory=list, max_length=10)


class _LensOutcome(_InternalModel):
    target_lenses: list[_TargetLens] = Field(default_factory=list, max_length=15)
    signal_lenses: list[_SignalLens] = Field(default_factory=list, max_length=20)


class _PlannerIssue(_InternalModel):
    code: str = Field(min_length=1, max_length=160)
    blocking: bool = False


class _Planning(_InternalModel):
    contract_version: Literal["place-discovery-planning-v1"]
    status: Literal["ready", "needs_clarification", "unsupported"]
    source_disposition: str | None
    resolution: str | None
    lenses: _LensOutcome
    issues: list[_PlannerIssue] = Field(default_factory=list)


class _DiscoveryNotice(_InternalModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=500)
    lens_id: str | None = Field(None, max_length=160)


class _DiscoveryResponse(_InternalModel):
    contract_version: Literal["place-discovery-v1"]
    planning: _Planning
    lens_results: list[_LensResult] = Field(default_factory=list, max_length=3)
    notices: list[_DiscoveryNotice] = Field(default_factory=list, max_length=30)

__all__ = ["_DiscoveryResponse", "_Presentation", "_SearchPlace"]
