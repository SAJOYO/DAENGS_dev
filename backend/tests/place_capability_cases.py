"""Representative internal Place discovery payloads shared by boundary tests."""

from __future__ import annotations


def _fact(index: int, *, borrowed: bool = False, large: bool = False) -> dict:
    source = "kcisa" if borrowed else "kto"
    return {
        "fact_id": f"amenities.fact-{index}",
        "label": f"정보 {index}",
        "display_text": ("상세 정보 " + "가" * 480) if large else "반려견 용품을 구매할 수 있어요.",
        "source_state": "known",
        "evaluation_state": "not_evaluated",
        "severity": "info",
        "provenance": {
            "source": {"source": source, "ref": f"source-{index}"},
            "source_role": "supporting" if borrowed else "primary",
            "value_origin": "borrowed" if borrowed else "own",
            "link": {"state": "candidate"} if borrowed else None,
        },
    }


def _candidate(ref: str, *, large: bool = False) -> tuple[dict, dict]:
    key = {"source": "kto", "ref": ref}
    hit = {"place": {"key": key, "lat": 37.556, "lng": 126.923}}
    presentation = {
        "place_key": key,
        "title": ("테스트 장소 " + "나" * 280) if large else f"테스트 장소 {ref}",
        "summary": ("요약 " + "다" * 490) if large else "가까운 반려동물 동반 장소예요.",
        "kind_id": "pet_shop",
        "kind_label": "펫샵",
        "distance_m": 420,
        "address": ("서울 " + "라" * 490) if large else "서울 마포구",
        "core_items": [_fact(90, large=large)],
        "promoted_items": [_fact(index, borrowed=index == 1, large=large) for index in range(5)],
        "detail_items": [_fact(index + 10, large=large) for index in range(5)],
        "notices": [
            {"code": f"candidate.notice-{index}", "message": "확인이 필요한 정보예요.", "severity": "warning"}
            for index in range(4)
        ],
        "why_matched": [
            {"code": f"matched.reason-{index}", "message": "요청한 용품 정보가 있어요."}
            for index in range(4)
        ],
        # These representative internal-only fields must disappear at the adapter.
        "source_evidence": [{"raw": "must-not-cross"}],
        "policy_receipt": {"internal_trace": "must-not-cross"},
    }
    return hit, presentation


def discovery_payload(
    *,
    status: str = "ready",
    mapping_scope: str = "direct",
    group_sizes: tuple[int, ...] = (1,),
    deferred: bool = False,
    refinement: bool = False,
    issue: str | None = None,
    large: bool = False,
) -> dict:
    targets = []
    lens_results = []
    for lens_index, size in enumerate(group_sizes, start=1):
        lens_id = f"target:{lens_index}"
        targets.append(
            {
                "lens_id": lens_id,
                "display_label": f"#방향{lens_index}",
                "mapping_scope": mapping_scope,
                "availability": "executable",
                "support_note": "가능한 장소 유형을 기준으로 찾았어요.",
            }
        )
        pairs = [_candidate(f"K{lens_index}-{index}", large=large) for index in range(size)]
        lens_results.append(
            {
                "lens_id": lens_id,
                "display_label": f"#방향{lens_index}",
                "support_note": "가능한 장소 유형을 기준으로 찾았어요.",
                "search": {"groups": [{"results": [hit for hit, _ in pairs]}]},
                "presentations": [presentation for _, presentation in pairs],
            }
        )
    signals = []
    if deferred:
        signals.append(
            {
                "lens_id": "signal:quiet",
                "display_label": "#조용한 분위기",
                "availability": "deferred",
                "required": False,
                "support_note": "조용함을 판정할 장소 근거가 아직 없어요.",
                "options": [],
            }
        )
    if refinement:
        signals.append(
            {
                "lens_id": "signal:price",
                "display_label": "#비용 기준",
                "availability": "needs_selection",
                "required": False,
                "support_note": "어떤 비용을 뜻하는지 골라주세요.",
                "options": [
                    {
                        "option_id": "cost.travel_distance",
                        "display_label": "이동 거리",
                        "availability": "proxy",
                        "support_note": "가까운 곳을 우선해요.",
                    }
                ],
            }
        )
    return {
        "contract_version": "place-discovery-v1",
        "planning": {
            "contract_version": "place-discovery-planning-v1",
            "status": status,
            "source_disposition": "proposed" if status == "ready" else "abstained",
            "resolution": "exploratory" if status == "ready" else None,
            "lenses": {"target_lenses": targets, "signal_lenses": signals},
            "issues": [{"code": issue, "blocking": False}] if issue else [],
        },
        "result_policy": {"raw": "must-not-cross"},
        "lens_results": lens_results if status == "ready" else [],
        "notices": [],
    }

__all__ = ["discovery_payload"]
