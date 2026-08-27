"""세션 진행에 따른 속도·정지 곡선. 순수함수 — `Segment` 열만 본다.

**왜 남기나**: `Segment` 는 `compute_facts` 안에서만 살고 finish 트랜잭션이 끝나면 사라진다.
원좌표도 같은 트랜잭션에서 지워지므로 그 세션에 대해 다시 만들 방법이 없다. 그런데
`WalkFacts` 에 남는 속도 정보는 세션 평균(`avg_speed_mps`) 하나뿐이라, **평균이 같고 모양이
다른 두 산책이 구분되지 않는다** — 1.3 m/s 로 시작해 0.5 로 끝난 산책과 내내 0.9 로 걸은
산책이 같은 값이 된다.

"후반이 전반보다 느려졌나"는 다른 산책 기록이 하나도 없어도 답할 수 있는 유일한 축이다
(세션 내부 비교라 baseline 이 필요 없다). 그걸 물으려면 곡선이 있어야 한다.

**좌표는 담지 않는다.** 구간 번호·시간·거리뿐이라 어디를 걸었는지 나오지 않는다 — 같은
곡선이 서울에서 나오든 부산에서 나오든 값이 같다. 결정 #57 의 무좌표 층이라 보관 정책이
아니라 유용성만 보면 되는 자리다.

버킷 수와 경계 배분은 실기기 반복 측정 전의 **잠정값**이다. `facts.py` 문턱값과 같은 성격이라
`CURVE_VERSION` 으로 결과에 남긴다 — 바뀌면 이전 곡선과 섞지 않는다.
"""

from dataclasses import dataclass
from datetime import datetime

from app.features.walk.facts import Segment

CURVE_VERSION = 1
BUCKETS = 10


@dataclass(frozen=True)
class CurveBucket:
    """진행 구간 하나. 전부 스칼라 — 좌표 없음."""

    index: int                 # 0..BUCKETS-1. 0 = 산책 시작 구간
    moving_s: float            # 이동으로 분류된 시간
    moving_m: float            # 그 시간 동안의 거리
    still_s: float             # 정지 후보 시간. WalkFacts.stop_s 와 다르다 (10초 문턱 없음)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "moving_s": round(self.moving_s, 1),
            "moving_m": round(self.moving_m, 1),
            "still_s": round(self.still_s, 1),
        }


def compute_curve(
    started_at: datetime, ended_at: datetime, segments: list[Segment]
) -> list[CurveBucket]:
    """세션을 벽시계 시간으로 `BUCKETS` 등분하고 각 구간의 이동·정지를 모은다.

    **왜 거리가 아니라 시간으로 자르나**: 지침은 시간 현상이고 정지는 거리를 안 만든다.
    거리로 자르면 긴 정지가 통째로 한 버킷에 들어가 그 구간이 "느린 이동"으로 보인다.

    구간 경계를 걸치는 segment 는 중간 시각이 속한 버킷에 통째로 넣는다. 쪼개서 배분하지
    않는 것은 잠정 선택이다 — 점 간격(수 초)이 버킷 폭(수십 초 이상)보다 훨씬 작아 치우침이
    작고, 정확히 하려면 경계마다 보간해야 해서 값이 오는 곳이 늘어난다.

    항상 `BUCKETS` 개를 돌려준다. 비어 있는 구간도 0 으로 채운 자리가 있어야 두 산책의
    같은 구간을 바로 비교할 수 있다.
    """
    buckets = [{"moving_s": 0.0, "moving_m": 0.0, "still_s": 0.0} for _ in range(BUCKETS)]
    span = (ended_at - started_at).total_seconds()

    for seg in segments:
        if span <= 0:
            index = 0
        else:
            mid = (seg.a.at.timestamp() + seg.b.at.timestamp()) / 2
            ratio = (mid - started_at.timestamp()) / span
            index = min(BUCKETS - 1, max(0, int(ratio * BUCKETS)))
        if seg.moving:
            buckets[index]["moving_s"] += seg.dt
            buckets[index]["moving_m"] += seg.dist
        else:
            buckets[index]["still_s"] += seg.dt

    return [CurveBucket(index=i, **b) for i, b in enumerate(buckets)]
