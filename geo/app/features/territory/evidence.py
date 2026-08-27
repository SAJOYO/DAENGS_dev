"""근거와 고르기 — 파이프라인의 Evidence · Judgment 두 단.

갈래는 [evidence-layer](../../../docs/explorations/walk/evidence-layer.md).

    Instruments   방문률·추세          experience.py
    Evidence      "저녁엔 R12 +29%p"   이 파일 — gather()
    Judgment      지금 말할 가치 있나   이 파일 — rank()
    Surface       문장 / 지도          화면

## 근거는 값만 내지 않는다

원칙 2 다. 묶는 순간 정보가 사라지는 게 통계 장치의 본질이라, 모든 근거가 **무엇을 묶었는지 ·
어떤 조건인지 · 분자/분모 · 비교 기준 · 표본량**을 달고 다녀야 한다. 안 그러면
"양재천을 선호합니다" 가 `12/15` 인지 `120/300` 인지, 최근 7 일인지 1 년인지 아무도 모른다.

그래서 `Evidence` 는 `cohort` 와 `baseline` 을 **`VisitRate` 통째로** 들고 있다. 비율만
꺼내 담지 않는다.

## 고르기도 장치다

원칙 5 다. 알잘딱의 본체는 읽기가 아니라 **고르기**고, 선택이 블랙박스면 신뢰가 제일 필요한
층에서 불투명해진다. 그래서 `rank()` 는 **후보 전부**를 점수·이유와 함께 돌려준다.
탈락한 것도 왜 탈락했는지를 달고 나온다.

그러면 화면이 "왜 이 말을 했지" 만이 아니라 **"왜 저 말은 안 했지"** 까지 펼칠 수 있다.

## 규칙은 읽으면 바로 보이게

추천 "엔진" 이 아니다. 이번에 검증하는 것은 순위 알고리즘이 아니라 **이런 근거가 도착하는
것이 값어치 있나** 이므로, 점수식은 두 줄이면 된다. 정교하게 만들면 나중에 그 정교함 자체를
설명해야 하고(`layers.normalized_distance` 의 이유와 같다), 무엇보다 **알고리즘을 검증하는
실험으로 오해**된다.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.features.territory.experience import (
    CHIPS,
    TREND_MIN_WALKS,
    UNEXPLORED_CUT,
    Experience,
    RegionStats,
)
from app.features.territory.layers import VisitRate

EVIDENCE_VERSION = 1

# 이보다 작은 차이는 말하지 않는다. **근거 있는 문턱이 아니라 화면용 잠정값**이다 —
# 실사용 데이터가 0 이라 정할 근거가 없다(#57 이 보관 일수를 안 정한 것과 같은 이유).
# 숨기지 않고 결과에 실어 보낸다.
MIN_DELTA = 0.15

# 미개척은 크기를 **잴 수가 없다** — 견줄 baseline 이 없다. 대신 낮은 바닥값을 준다.
UNEXPLORED_FLOOR = 0.10

# 변화가 아니라 **상태**인 근거는 깎는다. 이유는 취향이 아니라 구조다 —
# **상태는 매일 다시 참이라 매일 다시 울린다.** "저녁엔 북쪽 가시죠" 는 1 년 내내 참이라
# 저녁마다 같은 말을 하게 되고, 그건 설계로 만든 스팸이다. 변화는 한 번 울리고 끝난다.
# 이 구별이 없으면 큰 상태 하나가 진짜 뉴스를 영구히 덮는다 (실제로 그렇게 나왔다 —
# 북쪽의 저녁 편향 0.667 이 남쪽이 뚝 끊긴 사실 0.600 을 이겼다).
NEWS_KINDS = frozenset({"visit_drop", "visit_rise"})
STATE_WEIGHT = 0.5

# 지금 조건과 안 맞는 근거는 깎는다. 0 이 아닌 이유는 **아주 큰 변화는 조건이 달라도 말할
# 가치가 있기** 때문이다 — 아침 얘기라고 저녁에 무조건 버리면 그것도 규칙이 사실을 지우는 것.
OFF_CONTEXT_WEIGHT = 0.6

CHIP_LABEL = dict(CHIPS)


@dataclass(frozen=True)
class Evidence:
    """근거 하나. **사람과 AI 가 같이 읽는 계약**이라 표현이 아니라 사실만 담는다.

    `kind` 가 무엇을 견줬는지를 정한다.

        condition_bias   이 조건에서의 방문률 대 전체 방문률
        visit_drop       최근 30 일 대 그 앞 30 일 (내려간 쪽)
        visit_rise       최근 30 일 대 그 앞 30 일 (올라간 쪽)
        unexplored       전체 방문률이 문턱 아래 — 견줄 것이 없다
    """

    kind: str
    region_id: str
    region_version: int
    name: str
    cohort: VisitRate               # 이 조건에서 — 분자·분모가 통째로 온다
    cohort_label: str
    baseline: VisitRate | None      # 무엇과 견줬나. unexplored 는 없다
    baseline_label: str | None
    delta: float | None
    trustworthy: bool
    version: int = EVIDENCE_VERSION

    @property
    def magnitude(self) -> float:
        """얼마나 두드러지나. 순위의 재료지 그 자체가 뜻을 갖는 값은 아니다."""
        if self.kind == "unexplored":
            return UNEXPLORED_FLOOR
        return abs(self.delta) if self.delta is not None else 0.0


@dataclass(frozen=True)
class Ranked:
    """근거 하나에 대한 판정. **탈락한 것도 이유를 달고 나온다.**"""

    evidence: Evidence
    score: float
    reasons: dict = field(default_factory=dict)
    dropped: str | None = None      # None 이면 말할 수 있는 후보다

    @property
    def sayable(self) -> bool:
        return self.dropped is None


def _complement(overall: VisitRate, cohort: VisitRate) -> VisitRate:
    """조건에 **안 걸린** 나머지. `전체 − 조건` 이다.

    "저녁 편향" 이 실제로 견주는 것은 `저녁` 대 `전체` 가 아니라 **`저녁` 대 `저녁 외`** 다.
    전체 안에 저녁이 들어 있어서, 전체와 견주면 저녁이 스스로를 희석한 값과 견주게 된다.

        저녁 5/5 · 그 외 0/1   →  전체와 견주면 +16.7%p 로 작아 보이고
                                  나머지와 견주면 +100%p 인데 **반대쪽 표본이 1 회**다

    빼기로 구할 수 있는 이유는 모든 칩이 전체의 부분집합이라서다 — `all` 은 조건이 없고
    나머지 칩은 전부 그것을 좁힌다.
    """
    return VisitRate(
        region_id=overall.region_id, region_version=overall.region_version,
        visited=overall.visited - cohort.visited,
        selected=overall.selected - cohort.selected,
        total=overall.total, min_peak=overall.min_peak,
    )


def gather(stats: list[RegionStats], context_chip: str) -> list[Evidence]:
    """영역 통계 → 근거 후보 전부. **여기서 고르지 않는다.**

    한 영역이 근거를 여럿 낼 수 있다 — "저녁에 유난히 간다" 와 "최근 뜸해졌다" 는 같은
    곳의 서로 다른 사실이고, 둘 다 참일 수 있다. 고르는 것은 `rank()` 몫이다.
    """
    found: list[Evidence] = []
    for stat in stats:
        overall = stat.by_chip["all"]

        # 1. 조건 편향 — 이 조건에서 **유난히 자주** 가는가.
        #
        #    아래로 벌어진 것(이 조건엔 잘 안 간다)은 근거로 안 만든다. 테스트를 짜다가
        #    걸린 자리다 — 북쪽은 저녁 +0.27 인데 아침 **−0.40** 이라, |차이| 로 재면
        #    "아침엔 여기 안 가시죠" 가 "저녁엔 여기 자주 가시죠" 를 이겨 버렸다.
        #    수학은 맞지만 **"오늘 어디 갈까" 에 답하는 표면에서 쓸 말이 아니다.**
        #    아래로 벌어진 사실이 필요해지면 그때 다른 kind 로 만든다.
        cohort = stat.by_chip.get(context_chip)
        rest = _complement(overall, cohort) if cohort is not None else None
        if (cohort is not None and rest is not None
                and cohort.rate is not None and rest.rate is not None
                and cohort.rate > rest.rate):
            found.append(Evidence(
                kind="condition_bias", region_id=stat.region_id,
                region_version=stat.region_version, name=stat.name,
                cohort=cohort, cohort_label=CHIP_LABEL.get(context_chip, context_chip),
                baseline=rest, baseline_label=f"{CHIP_LABEL.get(context_chip, context_chip)} 외",
                delta=cohort.rate - rest.rate,
                # **양쪽을 다 본다.** 한쪽만 보면 `저녁 5/5` 대 `그 외 0/1` 이 통과한다 —
                # "저녁이 유난하다" 를 떠받치는 반대쪽 표본이 1 회인데도. 추세는 처음부터
                # 두 창을 다 봤는데 여기만 안 보고 있었다.
                trustworthy=(cohort.selected >= TREND_MIN_WALKS
                             and rest.selected >= TREND_MIN_WALKS)))

        # 2. 추세 — 최근이 그 앞과 다른가
        delta = stat.trend.delta
        if delta is not None and delta != 0.0:
            found.append(Evidence(
                kind="visit_drop" if delta < 0 else "visit_rise",
                region_id=stat.region_id, region_version=stat.region_version,
                name=stat.name,
                cohort=stat.trend.recent, cohort_label=CHIP_LABEL["recent"],
                baseline=stat.trend.previous, baseline_label="그 앞 30일",
                delta=delta, trustworthy=stat.trend.trustworthy))

        # 3. 미개척 — 견줄 것이 없다. `selected > 0` 이 없으면 **기록이 0 인 사람에게
        #    "여긴 안 가보셨네요" 라고 말하게 된다.** 안 간 게 아니라 우리가 모르는 것이다.
        if overall.selected > 0 and overall.rate is not None and overall.rate <= UNEXPLORED_CUT:
            found.append(Evidence(
                kind="unexplored", region_id=stat.region_id,
                region_version=stat.region_version, name=stat.name,
                cohort=overall, cohort_label=CHIP_LABEL["all"],
                baseline=None, baseline_label=None, delta=None,
                trustworthy=overall.selected >= TREND_MIN_WALKS))
    return found


def rank(candidates: list[Evidence], context_chip: str) -> list[Ranked]:
    """후보 **전부**를 점수와 이유를 달아 정렬한다. 탈락도 이유를 단다.

    점수식은 두 줄이다.

        관련도 = 지금 조건의 근거면 1.0, 아니면 0.6
        뉴스   = 변화면 1.0, 상태(조건 편향·미개척)면 0.5
        점수   = 크기 × 관련도 × 뉴스

    관문 둘 — 표본이 얇으면 안 말하고, 차이가 작으면 안 말한다. **관문은 점수가 아니다.**
    표본이 얇은 것을 낮은 점수로 깎으면 큰 변화가 얇은 표본을 이겨 버린다. `1/2 → 2/2` 로
    "두 배 늘었어요" 라고 말하는 푸시가 정확히 그렇게 나온다.
    """
    ranked: list[Ranked] = []
    for item in candidates:
        on_context = (item.kind == "condition_bias"
                      and item.cohort_label == CHIP_LABEL.get(context_chip, context_chip))
        relevance = 1.0 if on_context else OFF_CONTEXT_WEIGHT
        is_news = item.kind in NEWS_KINDS
        score = item.magnitude * relevance * (1.0 if is_news else STATE_WEIGHT)

        dropped = None
        if not item.trustworthy:
            dropped = "표본 부족"
        elif item.kind != "unexplored" and item.magnitude < MIN_DELTA:
            dropped = "차이가 작다"

        ranked.append(Ranked(
            evidence=item, score=score, dropped=dropped,
            reasons={"magnitude": round(item.magnitude, 4),
                     "relevance": relevance,
                     "on_context": on_context,
                     "is_news": is_news,
                     "state_weight": 1.0 if is_news else STATE_WEIGHT,
                     "min_delta": MIN_DELTA}))

    # 말할 수 있는 것 먼저, 그 안에서 점수 높은 순. 같으면 이름순 — 결정론을 위해서다.
    ranked.sort(key=lambda r: (r.dropped is not None, -r.score, r.evidence.region_id,
                               r.evidence.kind))
    return ranked


def choose(ranked: list[Ranked]) -> Ranked | None:
    """지금 말할 것 하나. 없으면 **아무 말도 안 한다.**

    말할 게 없을 때 억지로 하나 고르지 않는다. 알잘딱 표면에서 그건 조용함이 아니라
    **틀린 푸시**가 되고, 틀린 푸시는 알림을 끄게 만든다.
    """
    return next((row for row in ranked if row.sayable), None)


@dataclass(frozen=True)
class Briefing:
    """지금 말할 것 하나 + 왜 그것인지 + 안 고른 것들. 이게 그대로 JSON 이 된다."""

    now: datetime
    context: dict[str, str]
    chosen: Ranked | None
    candidates: list[Ranked]
    thresholds: dict[str, float]
    version: int = EVIDENCE_VERSION


def brief(scene: Experience) -> Briefing:
    """장면 → 브리핑 하나. 화면과 에이전트가 이것만 읽는다.

    `Experience` 를 통째로 받는 이유는 `now` 와 `context` 를 두 곳에서 만들지 않으려고다 —
    같은 사실을 두 군데서 조립하면 갈라진다 (`layers` 가 canvas 를 한 곳에서만 만드는 이유).
    """
    context_chip = scene.context["chip"]
    ranked = rank(gather(scene.regions, context_chip), context_chip)
    return Briefing(
        now=scene.now, context=scene.context, chosen=choose(ranked), candidates=ranked,
        thresholds={"min_delta": MIN_DELTA, "unexplored_floor": UNEXPLORED_FLOOR,
                    "off_context_weight": OFF_CONTEXT_WEIGHT,
                    "trend_min_walks": TREND_MIN_WALKS, "unexplored_cut": UNEXPLORED_CUT},
    )


# ---- 문장은 여기서 끝난다 ---------------------------------------------------------------


TEMPLATES = {
    "condition_bias": "{name}은(는) {cohort_label} 산책에서 유난히 자주 가시네요.",
    "visit_drop": "{name} 쪽은 요즘 꽤 뜸했어요. 오늘 가볼까요?",
    "visit_rise": "{name} 쪽 산책이 부쩍 늘었네요.",
    "unexplored": "{name}은(는) 아직 거의 안 가보신 곳이에요.",
}


def sentence(row: Ranked) -> str:
    """템플릿 한 줄. **말하면 안 되는 근거로는 문장을 만들지 않는다.**

    탈락한 근거에도 완성된 문장을 붙여 두면, 소비자가 `sayable` 검사를 한 번 빠뜨리는
    순간 그게 그대로 거짓 푸시가 된다. Judgment 가 "이건 말하면 안 돼" 라고 정해 놨는데
    Surface 가 이미 말을 만들어 둔 셈이라, 경계를 우회할 수 있는 상태 자체를 없앤다.

    **LLM 은 아직 안 붙인다.** 문장이 매끈하면 정보가 별로여도 좋아 보이는 착시가 생긴다.
    지금 재려는 것은 문장력이 아니라 **이런 근거가 도착하는 경험 자체의 값어치**라, 못생긴
    템플릿이 오히려 실험 도구로 옳다. 값어치가 확인되면 그때 응답 에이전트가 맡는다 (#53).
    """
    if not row.sayable:
        raise ValueError(
            f"말하지 않기로 한 근거로 문장을 만들려 했다 ({row.dropped}): "
            f"{row.evidence.kind} · {row.evidence.name}")
    item = row.evidence
    return TEMPLATES[item.kind].format(name=item.name, cohort_label=item.cohort_label)
