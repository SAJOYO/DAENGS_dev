"""달 → 틀 파일·카드명·장면 설명. `cardimage/headers.json` 의 제목에서 ` NEO` 를 뗀 것이 카드명이다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class MonthNotOpenError(Exception):
    """틀은 있지만 설정(DAENGS_CARDIMAGE_MONTHS)으로 잠긴 달. 라우터가 404 로 바꾼다."""


@dataclass(frozen=True)
class MonthCard:
    month: int
    stem: str        # cardimage/<stem>_template.webp
    card_name: str   # 제목 앞부분. 뒤에 " <이름>" 이 붙는다
    badge: str       # 틀에 구워진 배지 (참고용, 그리지 않는다)
    scene: str       # 프롬프트에 넣는 무대 묘사. "" 이면 프롬프트를 못 만들어 열 수 없다


# scene 은 실험(worklog 09-13~14)에서 검증된 4월만 채워져 있다. 다른 달을 열 때는 그 달의
# 무대(소품·매트·배경)를 같은 식으로 적고 실험으로 검증한 뒤 DAENGS_CARDIMAGE_MONTHS 에 넣는다.
_CARDS: dict[int, MonthCard] = {
    1: MonthCard(1, "1_new_year", "NEW YEAR", "26JAN", ""),
    2: MonthCard(2, "2_love", "LOVE", "26FEB", ""),
    3: MonthCard(3, "3_first_day", "FIRST DAY", "26MAR", ""),
    4: MonthCard(
        4, "4_blossom", "BLOSSOM", "26APR",
        "the pose (sitting on the picnic blanket looking up at a falling petal), "
        "the green-and-white checked picnic blanket (keep this exact color and pattern), the wicker basket",
    ),
    5: MonthCard(5, "5_home_team", "HOME TEAM", "26MAY", ""),
    6: MonthCard(6, "6_pool", "POOL", "26JUN", ""),
    7: MonthCard(7, "7_beach", "BEACH", "26JUL", ""),
    8: MonthCard(8, "8_rain", "RAIN", "26AUG", ""),
    9: MonthCard(9, "9_harvest_moon", "HARVEST MOON", "26SEP", ""),
    10: MonthCard(10, "10_ghost", "GHOST", "26OCT", ""),
    11: MonthCard(11, "11_thanks", "THANKS", "26NOV", ""),
    12: MonthCard(12, "12_santa", "SANTA", "26DEC", ""),
}


def get(month: int) -> MonthCard:
    return _CARDS[month]


def require_open(month: int, open_months: frozenset[int]) -> MonthCard:
    card = _CARDS.get(month)
    if card is None or month not in open_months or not card.scene:
        raise MonthNotOpenError(f"month {month} is not open")
    return card


def template_path(month: int, base: Path) -> Path:
    return base / f"{get(month).stem}_template.webp"


def font_path(base: Path) -> Path:
    return base / "fonts" / "NotoSerifKR.ttf"
