"""표시 계획. resolver 가 만들고, 렌더가 받는다.

    render(plan.view)

검색·이동 계획은 각 실행자가 소유한다 — `geo.contract` 와 `journey.contract` (결정 #67 §3).
셋을 한 파일에 두었더니 실행자가 자기 입력을 가지러 상위 패키지를 import 해야 했고,
`Companion` 은 순환을 피하려고 `str` 로 뭉개져 있었다.

계획을 따로 두는 이유는 경계를 **구조로** 막기 위해서다. 조각을 여러 개 넘기면
(`find_places(target, derived)`) 결국 누군가 필요한 걸 하나 더 끌어다 쓰고, 그렇게
검색·경로·정렬이 각자 state·body·서버 시각·발화를 주워 먹으며 어긋났다.

**계획은 재료지 판정이 아니다.** '응급이니 차로 간다'를 여기서 확정하지 않는다 —
수단 우선순위와 억제 플래그까지만 싣고, 무엇을 고를지는 엔진이 정한다. 애초에 시설·경사는
경로를 받아봐야 아는 값이라 resolver 가 미리 판정할 수도 없다.
"""

from dataclasses import dataclass, field

from app.discovery.state import Sort


@dataclass(frozen=True)
class ViewPlan:
    """어떻게 보여줄까. 결과 집합도 경로도 안 바꾼다."""

    sort: Sort = "distance"
    pin_ids: tuple[int, ...] = field(default_factory=tuple)
    show_call_cta: bool = False     # 안전 표면 — 긴급도가 높으면 조건을 좁히는 대신 전화를 권한다
    call_reasons: tuple[str, ...] = field(default_factory=tuple)
