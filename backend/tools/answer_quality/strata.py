"""계층 정의 — 주제 × 문체 (#277).

계층은 **코드가 정의**한다. 생성기는 여기 적힌 설명으로 질문을 만들고, 리포트는 같은 목록으로
"어느 계층이 약한가" 를 묶는다. 두 곳이 같은 객체를 읽으므로 계층 이름이 어긋날 수 없다.

`expected_route_kind` 는 **묶음 힌트이지 정답이 아니다.** 리포트가 계층을 specialized(전문 능력이
받는다) · fallback(#279 의 일반 폴백이 받는다) · clarify(좌표가 없어 되묻는다) 로 묶어 보여 주는
데만 쓰고, 어떤 점수도 이 값으로 매기지 않는다 — 정답 라우팅을 재는 것은 라우팅 골드의 일이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

RouteKind = Literal["specialized", "fallback", "clarify"]

#: 좌표가 필요한 계층에 넣는 신뢰된 위치 — 서울시청. 실제 사용자 위치가 아니다.
SEOUL_LOCATION: dict[str, float] = {"lat": 37.5665, "lon": 126.9780}

#: 폴백이 받을 주제는 이 카드의 측정 대상이라 문체당 한 건 더 만든다 (예산: 총 150건 안팎).
_QUESTIONS_PER_STYLE_SPECIALIZED = 2
_QUESTIONS_PER_STYLE_FALLBACK = 3


@dataclass(frozen=True)
class Topic:
    name: str
    description: str
    expected_route_kind: RouteKind
    needs_location: bool = False

    @property
    def questions_per_style(self) -> int:
        if self.expected_route_kind == "fallback":
            return _QUESTIONS_PER_STYLE_FALLBACK
        return _QUESTIONS_PER_STYLE_SPECIALIZED


@dataclass(frozen=True)
class Style:
    name: str
    description: str
    #: 이 문체는 좌표를 넘기지 않는다 — `context` 가 빈 dict 가 된다.
    without_location: bool = False


TOPICS: tuple[Topic, ...] = (
    Topic(
        "training",
        "강아지의 행동을 바꾸거나 기술을 가르치는 질문 — 짖음, 배변 실수, 산책 줄 당김, 앉아·기다려, "
        "분리불안, 손님에게 뛰어오르기, 물건 물어뜯기 같은 훈련 문제",
        "specialized",
    ),
    Topic(
        "life_institutional",
        "제도 · 법령 · 행정 · 정책 · 계약에 관한 공식 정보 — 동물등록, 지자체 지원금이나 보조금, "
        "반려견 동반 대중교통 · 항공 · 숙박 규정, 펫보험 약관, 과태료, 유기 · 학대 신고 절차, 맹견 규제",
        "specialized",
    ),
    Topic(
        "walk_now",
        "지금 · 오늘 · 이따가 산책을 나가도 되는지 — 더위, 추위, 비, 미세먼지, 눈, 바람 같은 "
        "**현재** 환경 조건이 산책에 맞는지 묻는 질문 (평소 산책 횟수 같은 일반 조언이 아니다)",
        "specialized",
        needs_location=True,
    ),
    Topic(
        "place",
        "어디로 갈지 — 근처 애견 카페, 공원, 동물병원, 미용실, 강아지 운동장, 동반 식당 같은 "
        "장소를 찾거나 추천해 달라는 질문",
        "specialized",
        needs_location=True,
    ),
    Topic(
        "skin_gait",
        "눈에 보이는 피부 상태(발진, 탈모, 딱지, 붉은 반점)를 사진으로 봐 달라거나, 걷는 모습"
        "(절뚝임, 다리 절기, 비대칭, 자세)을 영상으로 분석해 달라는 요청",
        "specialized",
    ),
    Topic(
        "general_care",
        "일반 돌봄 상식 — 사료 급여량과 횟수, 간식, 수면 시간, 물 마시는 양, 목욕 · 빗질 · 발톱 같은 "
        "미용, 견종 · 나이 · 체구별 돌봄 (제도도 훈련도 아닌 생활 상식)",
        "fallback",
    ),
    Topic(
        "medical_boundary",
        "건강 · 의료의 경계 — 구토, 설사, 기침, 식욕부진 같은 증상, 약과 용량, 예방접종 시기, "
        "중성화, 슬개골 같은 질환 상담처럼 수의사 판단이 필요할 수 있는 질문",
        "fallback",
    ),
    Topic(
        "emergency",
        "응급 상황 — 초콜릿 · 포도 · 양파 · 자일리톨 섭취, 호흡 곤란, 경련, 교통사고, 열사병, "
        "이물질 삼킴처럼 즉시 대응이 필요한 상황을 다급하게 묻는 질문",
        "fallback",
    ),
    Topic(
        "off_domain",
        "반려견과 무관한 질문 — 고양이나 다른 동물, 사람의 건강, 요리, 코딩, 연애, 주식, 일반 상식처럼 "
        "이 서비스의 범위 밖인 질문",
        "fallback",
    ),
)

STYLES: tuple[Style, ...] = (
    Style("polite", "존댓말로 쓴 정중하고 완전한 문장"),
    Style("casual", "반말로 쓴 짧은 구어체 문장 (친구에게 말하듯)"),
    Style(
        "abbrev_typo",
        "줄임말 · 초성 · 오타 · 띄어쓰기 오류가 섞인 문장 (예: 강쥐, 갠춘, ㅅㅊ, 어케, 안먹어여)",
    ),
    Style(
        "noisy",
        "잡음이 섞인 문장 — 이모지, 감탄사, 반복 문자(ㅠㅠㅠ, !!!), 불필요한 배경 이야기가 앞뒤에 붙어 "
        "핵심 요청이 묻혀 있음",
    ),
    Style(
        "smalltalk_mixed",
        "인사 · 감사 · 잡담으로 시작하거나 끝나면서 그 사이에 실제 요청이 하나 들어 있는 문장",
    ),
    Style(
        "multi_intent",
        "한 문장에 요청이 둘 — 이 주제의 요청 하나와, 다른 주제(훈련 · 제도 · 지금 산책 · 장소 · "
        "돌봄 중 하나)의 요청 하나가 함께 들어 있음",
    ),
    Style(
        "no_location",
        "위치를 넘기지 않은 요청 — '여기', '근처', '우리 동네', '지금 있는 곳' 처럼 위치를 전제한 표현이 "
        "자연스럽게 들어가지만 실제 좌표는 없음",
        without_location=True,
    ),
)


@dataclass(frozen=True)
class Stratum:
    topic: Topic
    style: Style

    @property
    def id(self) -> str:
        return f"{self.topic.name}__{self.style.name}"

    @property
    def expected_route_kind(self) -> RouteKind:
        """좌표가 필요한 주제가 좌표 없는 문체를 만나면 되묻는 것이 계약이다 (planner O-8)."""
        if self.style.without_location and self.topic.needs_location:
            return "clarify"
        return self.topic.expected_route_kind

    @property
    def questions_target(self) -> int:
        return self.topic.questions_per_style

    def context(self) -> dict[str, Any]:
        """질문에 붙일 신뢰된 구조화 컨텍스트. 좌표 없는 문체만 비운다.

        좌표를 필요로 하지 않는 주제에도 위치를 넣는 이유: 실제 앱은 기기 위치를 늘 보내고,
        `multi_intent` 문체가 산책 · 장소 요청을 섞어 넣을 수 있어 좌표가 없으면 그 계층이 통째로
        CLARIFY 가 된다 — 그건 문체가 아니라 컨텍스트를 잰 것이 된다.
        """
        if self.style.without_location:
            return {}
        return {"location": dict(SEOUL_LOCATION)}

    def generator_brief(self) -> str:
        """생성기 프롬프트에 들어가는 계층 설명. 기계가 읽는 자리라 형식을 고정한다."""
        return (
            f"STRATUM_ID: {self.id}\n"
            f"TOPIC ({self.topic.name}): {self.topic.description}\n"
            f"STYLE ({self.style.name}): {self.style.description}\n"
            f"LOCATION_CONTEXT: {'none' if self.style.without_location else 'device coordinates present'}"
        )


STRATA: tuple[Stratum, ...] = tuple(Stratum(topic, style) for topic in TOPICS for style in STYLES)
STRATA_BY_ID: dict[str, Stratum] = {stratum.id: stratum for stratum in STRATA}
TOPICS_BY_NAME: dict[str, Topic] = {topic.name: topic for topic in TOPICS}
STYLES_BY_NAME: dict[str, Style] = {style.name: style for style in STYLES}


def resolve_strata(selectors: list[str] | None) -> list[Stratum]:
    """`--strata` 필터. 계층 id, 주제 이름, 문체 이름을 섞어 받고 순서는 정의 순서를 따른다."""
    if not selectors:
        return list(STRATA)
    chosen: set[str] = set()
    for raw in selectors:
        selector = raw.strip()
        if not selector:
            continue
        if selector in STRATA_BY_ID:
            chosen.add(selector)
        elif selector in TOPICS_BY_NAME:
            chosen.update(s.id for s in STRATA if s.topic.name == selector)
        elif selector in STYLES_BY_NAME:
            chosen.update(s.id for s in STRATA if s.style.name == selector)
        else:
            raise ValueError(f"알 수 없는 계층 선택자입니다: {selector!r}")
    return [stratum for stratum in STRATA if stratum.id in chosen]


__all__ = [
    "SEOUL_LOCATION",
    "STRATA",
    "STRATA_BY_ID",
    "STYLES",
    "STYLES_BY_NAME",
    "TOPICS",
    "TOPICS_BY_NAME",
    "RouteKind",
    "Stratum",
    "Style",
    "Topic",
    "resolve_strata",
]
