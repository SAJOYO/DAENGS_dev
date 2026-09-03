"""사용자가 명시적으로 고른 지원 가능한 facet을 기존 검색 lens에 적용한다."""

from daengs_place.place.intent.lenses import (
    FacetOptionAvailability,
    LensAvailability,
    LensType,
    SearchLensOutcome,
)

_COST_FACET_ID = "cost.dimension"
_DISTANCE_OPTION_ID = "cost.travel_distance"
_DISTANCE_NOTE = "비용은 실제 가격이 아니라 현재 위치와의 거리 기준으로 해석했습니다."


def resolve_search_facet(
    lenses: SearchLensOutcome,
    *,
    signal_lens_id: str,
    option_id: str,
) -> SearchLensOutcome:
    """현재 capability로 정직하게 실행할 수 있는 선택만 target lens를 연다."""

    try:
        signal = next(item for item in lenses.signal_lenses if item.lens_id == signal_lens_id)
    except StopIteration as exc:
        raise ValueError("unknown signal lens") from exc
    if signal.lens_type is not LensType.UNRESOLVED:
        raise ValueError("only unresolved facet lenses can be selected")
    try:
        option = next(item for item in signal.options if item.option_id == option_id)
    except StopIteration as exc:
        raise ValueError("option does not belong to the selected facet") from exc
    if option.availability is not FacetOptionAvailability.PROXY:
        raise ValueError("selected facet option is not executable")
    if option.option_id != _DISTANCE_OPTION_ID:
        raise ValueError("unsupported executable facet option")

    marker = ":facet:"
    if marker not in signal.lens_id or not signal.lens_id.startswith("signal:"):
        raise ValueError("facet signal lens has no stable hypothesis set key")
    hypothesis_set_key = signal.lens_id.removeprefix("signal:").split(marker, maxsplit=1)[0]
    target_prefix = f"lens:{hypothesis_set_key}:"

    targets = []
    for target in lenses.target_lenses:
        if not target.lens_id.startswith(target_prefix):
            targets.append(target)
            continue
        unresolved = tuple(
            item for item in target.unresolved_facet_ids if item != _COST_FACET_ID
        )
        availability = (
            LensAvailability.EXECUTABLE
            if not unresolved and target.candidate.result.plan is not None
            else target.availability
        )
        targets.append(
            target.model_copy(
                update={
                    "availability": availability,
                    "unresolved_facet_ids": unresolved,
                    "support_note": (
                        target.support_note
                        if _DISTANCE_NOTE in target.support_note
                        else f"{target.support_note} {_DISTANCE_NOTE}"
                    ),
                }
            )
        )

    selected_signal = signal.model_copy(
        update={
            "availability": LensAvailability.RESOLVED,
            "selected_option_id": option.option_id,
        }
    )
    signals = tuple(
        selected_signal if item.lens_id == signal_lens_id else item
        for item in lenses.signal_lenses
    )
    return SearchLensOutcome(target_lenses=tuple(targets), signal_lenses=signals)
