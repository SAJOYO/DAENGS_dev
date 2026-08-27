"""encounter 기하값 → 판정. 규칙표 v1 — 상수는 전부 실기기 반복 측정 전의 잠정값.

게임으로 치면 저장층이 히트박스 관측(원별 체류·정지 겹침)이고, 여기가 상태 분류다.
상수가 바뀌면 JUDGMENT_VERSION 을 올린다 — 과거 세션의 encounter 사실은 그대로이므로
언제든 새 규칙으로 다시 판정할 수 있다. (원좌표는 없지만 밴드 3개가 저장돼 있어서
반지름 선택도 사후에 가능하다 — 그게 밴드를 전부 저장한 이유다.)

판정은 "사용자가 봤다"가 아니다. 측정 품질이 허락하는 최대치는 이것뿐이다:
  passed          원을 스치듯 통과했다 (체류 짧음, 정지 없음)
  lingered        원 안에 머물렀다 (체류 김 또는 원 안 정지)
  visited_guess   작은 원 안에서 충분히 멈췄다 — 그래도 추정이다. 출입구를 모른다
  unjudgeable     legacy 집계행이거나 그 구간 GPS 오차가 판정 반지름보다 크다
"""

from typing import Literal

from app.features.walk.models import ENCOUNTER_OCCURRENCE_VERSION, FacilityEncounter

JUDGMENT_VERSION = 3

# ---- 잠정 상수. 실측(같은 길 반복 보행) 후 확정한다 ----
BAND_M: Literal[10, 15, 20] = 15       # 기본 판정 반지름
LINGER_MIN_DWELL_S = 10                # 이상 머물면 '머묾'
VISIT_MIN_STOP_S = 30                  # 10m 원 안 정지가 이상이면 '방문 추정'

Judgment = Literal["passed", "lingered", "visited_guess", "unjudgeable"]


def _dwell(e: FacilityEncounter, band: int) -> int:
    return {10: e.dwell_s_10m, 15: e.dwell_s_15m, 20: e.dwell_s_20m}[band]


def _stop_overlap(e: FacilityEncounter, band: int) -> bool:
    return {10: e.stop_overlap_10m, 15: e.stop_overlap_15m, 20: e.stop_overlap_20m}[band]


def judge(e: FacilityEncounter, band: int = BAND_M) -> Judgment:
    """encounter 하나의 판정. 순수함수 — 같은 사실, 같은 상수, 같은 판정."""
    if e.occurrence_version < ENCOUNTER_OCCURRENCE_VERSION:
        # v1은 시설별 세션 합계라 왕복·반복 통과가 한 행에 섞였다. 읽기 호환은 하되
        # 하나의 occurrence인 척 판정하면 안 된다. 원좌표 purge 뒤라 분할 backfill도 불가하다.
        return "unjudgeable"
    if e.accuracy_p50_m is not None and e.accuracy_p50_m > band:
        return "unjudgeable"           # 오차 25m 로 15m 원을 판정하는 건 소음이다
    if e.stop_s_10m >= VISIT_MIN_STOP_S:
        return "visited_guess"
    if _dwell(e, band) >= LINGER_MIN_DWELL_S or _stop_overlap(e, band):
        return "lingered"
    return "passed"
