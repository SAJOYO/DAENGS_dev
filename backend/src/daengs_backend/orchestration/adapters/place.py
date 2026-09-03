"""Place discovery HTTP translation and compact assistant projection.

`daengs_backend` and `daengs_place` are separate runtimes. This module therefore owns a
consumer-side subset of the internal JSON contract and never imports Place implementation types.
Only fields needed by the assistant/map handoff survive the projection; provider material, raw
source records, search plans, and policy traces cannot cross this boundary by construction.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    OutcomeDetail,
    PlacePayload,
)

_DISCOVERY_PATH = "/internal/place/discovery"
_DISCOVERY_RADIUS_M = 3_000
_MAX_GROUPS = 3
_MAX_CANDIDATES_PER_GROUP = 3
_MAX_TOTAL_CANDIDATES = 9
_MAX_FACTS_PER_CANDIDATE = 5
_MAX_CANDIDATE_NOTICES = 2
_MAX_WHY_MATCHED = 2
_MAX_REFINEMENTS = 3
_MAX_OPTIONS_PER_REFINEMENT = 4
_MAX_GLOBAL_NOTICES = 12
MAX_PLACE_CAPABILITY_BYTES = 48 * 1024


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


class PlaceCapabilityAdapter:
    capability = CapabilityName.PLACE

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self._client = client
        self._base_url = (
            settings.place_search_base_url if base_url is None else base_url
        ).rstrip("/")
        timeout = settings.place_discovery_timeout_ms if timeout_ms is None else timeout_ms
        if timeout <= 0:
            raise ValueError("Place discovery timeout must be positive")
        self._timeout_s = timeout / 1_000
        parsed = urlsplit(self._base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Place search base URL must be an absolute HTTP URL")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Place search base URL must not include a path, query, or fragment")

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, PlacePayload):
            return self._error(started, "invalid_payload", "장소 검색 요청이 올바르지 않습니다.")

        body = {
            "query": payload.query,
            "spatial": {
                "lat": payload.lat,
                "lng": payload.lon,
                "radius_m": _DISCOVERY_RADIUS_M,
            },
            # Profile identity and inferred profile values deliberately do not cross PR6.
            "conditions": None,
        }
        try:
            response = await self._post(body, request_id=request_id)
        except httpx.TimeoutException:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(
                    kind="place_discovery_timeout",
                    detail="장소 추천 응답 시간이 초과됐습니다.",
                ),
                elapsed_ms=_elapsed_ms(started),
            )
        except httpx.RequestError:
            return self._error(
                started,
                "place_discovery_unavailable",
                "장소 추천 기능에 연결할 수 없습니다.",
            )

        failure = _http_failure(response, started=started)
        if failure is not None:
            return failure
        try:
            discovery = _DiscoveryResponse.model_validate(response.json())
            data = project_place_capability_data(discovery)
        except (ValueError, TypeError, ValidationError, json.JSONDecodeError):
            return self._error(
                started,
                "place_discovery_invalid_response",
                "장소 추천 결과를 해석할 수 없습니다.",
            )

        candidate_count = _candidate_count(data)
        if candidate_count:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.OK,
                data=data,
                elapsed_ms=_elapsed_ms(started),
            )
        reason = (
            "place_needs_clarification"
            if discovery.planning.status == "needs_clarification"
            else "place_unsupported"
            if discovery.planning.status == "unsupported"
            else "place_no_candidates"
        )
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.ABSTAINED,
            data=data,
            abstention=OutcomeDetail(code=reason, message=data["answer"]),
            elapsed_ms=_elapsed_ms(started),
        )

    async def _post(self, body: dict[str, Any], *, request_id: str) -> httpx.Response:
        url = f"{self._base_url}{_DISCOVERY_PATH}"
        kwargs = {
            "json": body,
            "headers": {"X-Request-ID": request_id},
            "timeout": self._timeout_s,
        }
        if self._client is not None:
            return await self._client.post(url, **kwargs)
        async with httpx.AsyncClient() as client:
            return await client.post(url, **kwargs)

    def _error(self, started: float, kind: str, detail: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.ERROR,
            error=ErrorDetail(kind=kind, detail=detail),
            elapsed_ms=_elapsed_ms(started),
        )


def _http_failure(response: httpx.Response, *, started: float) -> CapabilityResult | None:
    if response.is_success:
        return None
    code = _error_code(response)
    if response.status_code == 504 or code == "place_intent_provider_timeout":
        return CapabilityResult(
            capability=CapabilityName.PLACE,
            status=CapabilityStatus.TIMEOUT,
            error=ErrorDetail(
                kind="place_discovery_timeout",
                detail="장소 추천 응답 시간이 초과됐습니다.",
            ),
            elapsed_ms=_elapsed_ms(started),
        )
    if response.status_code == 503 and code == "place_intent_not_configured":
        kind = "place_discovery_not_configured"
        detail = "장소 추천 기능이 아직 설정되지 않았습니다."
    elif response.status_code >= 500:
        kind = "place_discovery_upstream_failure"
        detail = "장소 추천 기능을 현재 사용할 수 없습니다."
    else:
        kind = "place_discovery_invalid_response"
        detail = "장소 추천 요청을 처리할 수 없습니다."
    return CapabilityResult(
        capability=CapabilityName.PLACE,
        status=CapabilityStatus.ERROR,
        error=ErrorDetail(kind=kind, detail=detail),
        elapsed_ms=_elapsed_ms(started),
    )


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("detail"), dict):
        return None
    code = payload["detail"].get("code")
    return code if isinstance(code, str) else None


def project_place_capability_data(discovery: _DiscoveryResponse) -> dict[str, Any]:
    """Project the internal discovery envelope into bounded user-facing capability data."""

    target_by_id = {item.lens_id: item for item in discovery.planning.lenses.target_lenses}
    groups: list[dict[str, Any]] = []
    total_candidates = 0
    projection_reduced = len(discovery.lens_results) > _MAX_GROUPS
    uses_fallback = False

    for lens in discovery.lens_results[:_MAX_GROUPS]:
        target = target_by_id.get(lens.lens_id)
        uses_fallback = uses_fallback or bool(
            target and target.mapping_scope == "product_fallback"
        )
        hits = [hit for group in lens.search.groups for hit in group.results]
        remaining = _MAX_TOTAL_CANDIDATES - total_candidates
        take = min(len(lens.presentations), _MAX_CANDIDATES_PER_GROUP, remaining)
        projection_reduced = projection_reduced or take < len(lens.presentations)
        candidates = [
            _project_candidate(presentation, hits[index].place)
            for index, presentation in enumerate(lens.presentations[:take])
        ]
        total_candidates += len(candidates)
        groups.append(
            {
                "lens_id": lens.lens_id,
                "label": lens.display_label,
                "support_note": lens.support_note,
                "candidates": candidates,
            }
        )
        if total_candidates >= _MAX_TOTAL_CANDIDATES:
            projection_reduced = projection_reduced or any(
                item.presentations for item in discovery.lens_results[len(groups) :]
            )
            break

    actionable_signals = [
        signal
        for signal in discovery.planning.lenses.signal_lenses
        if signal.availability == "needs_selection" and signal.options
    ]
    projection_reduced = projection_reduced or len(actionable_signals) > _MAX_REFINEMENTS or any(
        len(signal.options) > _MAX_OPTIONS_PER_REFINEMENT for signal in actionable_signals
    )
    refinements = [
        {
            "lens_id": signal.lens_id,
            "label": signal.display_label,
            "required": signal.required,
            "support_note": signal.support_note,
            "options": [
                {
                    "id": option.option_id,
                    "label": option.display_label,
                    "availability": option.availability,
                    "support_note": option.support_note,
                }
                for option in signal.options[:_MAX_OPTIONS_PER_REFINEMENT]
            ],
        }
        for signal in actionable_signals[:_MAX_REFINEMENTS]
    ]
    notices = []
    for issue in discovery.planning.issues:
        notices.append(
            {
                "code": issue.code,
                "message": _issue_message(issue.code),
                "lens_id": None,
            }
        )
    for signal in discovery.planning.lenses.signal_lenses:
        if signal.availability == "deferred":
            notices.append(
                {
                    "code": "place.signal_deferred",
                    "message": f"{signal.display_label}: {signal.support_note}",
                    "lens_id": signal.lens_id,
                }
            )
    notices.extend(
        {"code": item.code, "message": item.message, "lens_id": item.lens_id}
        for item in discovery.notices
    )
    deduped_notices = _dedupe_notices(notices)
    projection_reduced = projection_reduced or len(deduped_notices) > _MAX_GLOBAL_NOTICES
    notices = deduped_notices[:_MAX_GLOBAL_NOTICES]
    if projection_reduced:
        projection_notice = {
            "code": "place.capability_projection_applied",
            "message": "대화 화면에 맞춰 각 검색 방향의 대표 정보만 표시했어요.",
            "lens_id": None,
        }
        if len(notices) == _MAX_GLOBAL_NOTICES:
            notices[-1] = projection_notice
        else:
            notices.append(projection_notice)

    data: dict[str, Any] = {
        "contract_version": "place-capability-v1",
        "answer": "",
        "interpretation_summary": {
            "status": discovery.planning.status,
            "disposition": discovery.planning.source_disposition,
            "resolution": discovery.planning.resolution,
            "message": _interpretation_message(groups, refinements),
        },
        "groups": groups,
        "refinements": refinements,
        "notices": notices,
    }
    data["answer"] = _answer(
        data,
        status=discovery.planning.status,
        uses_fallback=uses_fallback,
    )
    _fit_byte_budget(data)
    data["answer"] = _answer(
        data,
        status=discovery.planning.status,
        uses_fallback=uses_fallback,
    )
    _fit_byte_budget(data)
    if _serialized_size(data) > MAX_PLACE_CAPABILITY_BYTES:
        raise ValueError("Place capability projection exceeds its byte budget")
    return data


def _project_candidate(item: _Presentation, place: _SearchPlace) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    ordered: Iterable[tuple[str, _PresentationFact]] = (
        *(("promoted", fact) for fact in item.promoted_items),
        *(("detail", fact) for fact in item.detail_items),
        *(("core", fact) for fact in item.core_items),
    )
    for placement, fact in ordered:
        if len(facts) >= _MAX_FACTS_PER_CANDIDATE:
            break
        source = fact.provenance.source
        facts.append(
            {
                "id": fact.fact_id,
                "label": fact.label,
                "text": fact.display_text,
                "placement": placement,
                "source_state": fact.source_state,
                "evaluation_state": fact.evaluation_state,
                "severity": fact.severity,
                "provenance": {
                    "source": source.source,
                    "ref": source.ref,
                    "role": fact.provenance.source_role,
                    "value_origin": fact.provenance.value_origin,
                    "link_state": (
                        fact.provenance.link.state if fact.provenance.link is not None else None
                    ),
                },
            }
        )
    return {
        "place_id": item.place_key.model_dump(),
        "title": item.title,
        "summary": item.summary,
        "kind": {"id": item.kind_id, "label": item.kind_label},
        "location": {
            "lat": place.lat,
            "lon": place.lng,
            "distance_m": item.distance_m,
        },
        "address": item.address,
        "facts": facts,
        "notices": [
            {"code": notice.code, "message": notice.message, "severity": notice.severity}
            for notice in item.notices[:_MAX_CANDIDATE_NOTICES]
        ],
        "why_matched": [
            {"code": reason.code, "message": reason.message}
            for reason in item.why_matched[:_MAX_WHY_MATCHED]
        ],
    }


def _fit_byte_budget(data: dict[str, Any]) -> None:
    if _serialized_size(data) <= MAX_PLACE_CAPABILITY_BYTES:
        return
    notices = data["notices"]
    if not any(item["code"] == "place.capability_byte_budget_applied" for item in notices):
        notices.append(
            {
                "code": "place.capability_byte_budget_applied",
                "message": "응답 크기 상한에 맞춰 일부 상세 정보를 줄였어요.",
                "lens_id": None,
            }
        )
    candidates = [candidate for group in data["groups"] for candidate in group["candidates"]]
    for key in ("facts", "why_matched", "notices"):
        while _serialized_size(data) > MAX_PLACE_CAPABILITY_BYTES and any(
            candidate[key] for candidate in candidates
        ):
            for candidate in reversed(candidates):
                if candidate[key]:
                    candidate[key].pop()
                    break
    while _serialized_size(data) > MAX_PLACE_CAPABILITY_BYTES and candidates:
        for group in reversed(data["groups"]):
            if group["candidates"]:
                removed = group["candidates"].pop()
                candidates.remove(removed)
                break


def _answer(data: dict[str, Any], *, status: str, uses_fallback: bool) -> str:
    count = _candidate_count(data)
    group_count = sum(bool(group["candidates"]) for group in data["groups"])
    if count:
        if uses_fallback:
            return (
                "요청을 직접 확인할 근거가 부족한 부분은 대안으로 남겼어요. "
                f"{group_count}가지 방향에서 가까운 장소 {count}곳을 찾았습니다."
            )
        return f"{group_count}가지 방향에서 가까운 장소 {count}곳을 찾았습니다."
    if data["refinements"]:
        return "바로 검색하기 어려운 기준이 있어요. 아래 선택지로 뜻을 좁혀주세요."
    if status == "unsupported":
        return "이 요청은 현재 장소 데이터로 직접 확인하기 어려워요. 장소 종류나 하고 싶은 일을 더 구체적으로 알려주세요."
    if status == "needs_clarification":
        return "장소를 찾으려면 뜻을 조금 더 구체적으로 알려주세요."
    return "해석한 방향에서는 주변 장소를 찾지 못했어요. 다른 기준이나 위치로 다시 찾아보세요."


def _interpretation_message(groups: list[dict[str, Any]], refinements: list[dict[str, Any]]) -> str:
    labels = [group["label"] for group in groups]
    if labels:
        return f"{' · '.join(labels)} 방향으로 나눠 찾았어요."
    if refinements:
        return "선택이 필요한 검색 기준을 찾았어요."
    return "실행 가능한 장소 검색 방향을 확정하지 못했어요."


def _issue_message(code: str) -> str:
    if code in {"unsupported_semantic_intent", "non_literal_target"}:
        return "요청 일부는 현재 장소 데이터로 직접 확인할 수 없어요."
    if code == "intent_proposer_invalid_output":
        return "요청 해석 결과를 안전하게 확인하지 못했어요."
    return "요청 일부는 현재 장소 검색에 직접 반영되지 않았어요."


def _dedupe_notices(notices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any]] = set()
    result = []
    for notice in notices:
        key = (notice["code"], notice.get("lens_id"))
        if key not in seen:
            seen.add(key)
            result.append(notice)
    return result


def _candidate_count(data: dict[str, Any]) -> int:
    return sum(len(group["candidates"]) for group in data["groups"])


def _serialized_size(data: dict[str, Any]) -> int:
    return len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)


__all__ = [
    "MAX_PLACE_CAPABILITY_BYTES",
    "PlaceCapabilityAdapter",
    "project_place_capability_data",
]
