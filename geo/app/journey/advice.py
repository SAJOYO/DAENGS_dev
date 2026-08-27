"""도보 판정 — 개 계수와 advice 규칙. companion=dog일 때만 의미 있다.

**재료가 있는 것만 판단한다.** 288경로 조사(2026-08-22)에서 계단 0/288, 육교 0/288 이었다.
그 위에 세운 판정 — 옵션 비교 · 계단 경고 · 피하기 요청 — 은 한 번도 발화할 수 없는 코드였고
결정 #66 으로 걷어냈다. 남은 것은 실측에 실제로 나오는 것들이다: 시간, 개 프로필, 기온,
지하보도(6%), 횡단보도(풍부).

개 밀도·그늘·인도 폭·경사는 재료가 없어 여기 없다.
"""

from app.profile.contract import DogProfile
from app.providers.base import Facilities, RouteResult


def dog_time_factor(profile: DogProfile | None) -> float:
    """제공사 도보 시간은 성인 4.4km/h(TMAP 실측). 개 데리고는 느리다."""
    if not profile:
        return 1.2                     # 기본: 냄새 맡기·배변
    f = 1.2
    if profile.is_senior: f += 0.3
    if profile.has_joint_issue: f += 0.2
    if profile.is_brachy: f += 0.2
    if profile.size_class == "small": f += 0.1
    if profile.activity_level == "high": f -= 0.1
    return round(min(f, 2.0), 2)


def prefers_quiet(profile: DogProfile | None) -> bool:
    return bool(profile and ({"reactive_to_dogs", "timid"} & set(profile.temperament)))


def walk_advice(r: RouteResult, profile: DogProfile | None, max_min: int | None,
                temp_c: float | None = None,
                factor: float = 1.0) -> tuple[str, list[str]]:
    why: list[str] = []
    level = 0  # 0 ok, 1 caution, 2 avoid
    minutes = r.duration_s * factor / 60
    fac = r.facilities or Facilities()

    if max_min is not None and minutes > max_min:
        level = 2; why.append(f"{int(minutes)}분 > 제한 {max_min}분")

    if profile:
        cap = 45.0
        if profile.is_senior: cap = 20.0
        if profile.has_joint_issue: cap = min(cap, 15.0)
        if profile.is_brachy: cap = min(cap, 20.0)
        if profile.size_class == "small" and profile.weight_kg < 4: cap = min(cap, 20.0)
        if minutes > cap * 1.5:
            level = 2; why.append(f"{int(minutes)}분 — {profile.name}에겐 과함(권장 ≤{int(cap)}분)")
        elif minutes > cap:
            level = max(level, 1); why.append(f"{int(minutes)}분 — 권장 {int(cap)}분 초과")
        if fac.underpass and profile.size_class == "large":
            level = max(level, 1); why.append(f"지하 통로 {fac.underpass}곳({fac.underpass_m}m) — 대형견 스트레스")
        if profile.is_brachy and temp_c is not None and temp_c >= 28:
            level = 2; why.append(f"단두종 + {temp_c:.0f}℃")
        if prefers_quiet(profile) and fac.big_crossings >= 3:
            level = max(level, 1); why.append(f"큰길 횡단 {fac.big_crossings}회 — 마주침 주의")

    if fac.crosswalk >= 6:
        level = max(level, 1); why.append(f"횡단보도 {fac.crosswalk}회")

    return ("ok", "caution", "avoid")[level], why
