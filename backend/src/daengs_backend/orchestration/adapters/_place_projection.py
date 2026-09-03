"""Bounded user-facing projection for Place capability results."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from daengs_backend.orchestration.adapters._place_contract import (
    _DiscoveryResponse,
    _Presentation,
    _SearchPlace,
)


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

__all__ = ["MAX_PLACE_CAPABILITY_BYTES", "project_place_capability_data"]
