from daengs_journey.providers.base import Facilities, RouteResult


def dog_time_factor() -> float:
    """DAENGS_geo's no-profile factor for smell and bathroom stops."""

    return 1.2


def walk_advice(
    route: RouteResult,
    max_minutes: int | None,
    factor: float = 1.0,
) -> tuple[str, list[str]]:
    """Only rules that DAENGS_geo applies without a dog profile."""

    reasons: list[str] = []
    level = 0
    minutes = route.duration_s * factor / 60
    facilities = route.facilities or Facilities()
    if max_minutes is not None and minutes > max_minutes:
        level = 2
        reasons.append(f"{int(minutes)}분 > 제한 {max_minutes}분")
    if facilities.crosswalk >= 6:
        level = max(level, 1)
        reasons.append(f"횡단보도 {facilities.crosswalk}회")
    return ("ok", "caution", "avoid")[level], reasons
