"""달 → 틀 파일·카드명·장면 설명. `cardimage/headers.json` 의 제목에서 ` NEO` 를 뗀 것이 카드명이다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from daengs_backend.services.cardimage.title import APRIL_PLATE, Plate


class MonthNotOpenError(Exception):
    """틀은 있지만 설정(DAENGS_CARDIMAGE_MONTHS)으로 잠긴 달. 라우터가 404 로 바꾼다."""


#: 9월 틀의 검은 제목판 — 4월보다 11px 위에 있다(y 40~135, 4월은 53~145) 그리고 약 40px 좁다.
#: 09-14 실측: 세로는 x=270~640 다섯 열 모두 중심 87.5~88.0, 오른쪽 경계 y65→x750 · y100→731 · y135→709.
SEPTEMBER_PLATE = Plate(center_y=88, edge=((65, 750), (135, 709)))

#: 4월처럼 강아지가 아무것도 안 입는 달의 의상 문장. 사진의 목줄·리드줄이 카드로 옮겨 오는 것을 막는다.
NO_OUTFIT = (
    "Do not carry over any accessories from image 2 — no collar, no leash, no harness, no clothing; "
    "the dog wears nothing, exactly like the dog in image 1."
)


@dataclass(frozen=True)
class MonthCard:
    month: int
    stem: str        # cardimage/<stem>_template.webp
    card_name: str   # 제목 앞부분. 뒤에 " <이름>" 이 붙는다
    badge: str       # 틀에 구워진 배지 (참고용, 그리지 않는다)
    scene: str       # 프롬프트에 넣는 무대 묘사. "" 이면 프롬프트를 못 만들어 열 수 없다
    subtitle: str = ""             # 틀에 구워진 부제. 프롬프트가 "그대로 두라"고 가리킨다
    outfit: str = NO_OUTFIT        # 의상 문장. 한복 같은 옷이 있는 달은 "이미지 1 의 옷 그대로" 로 바꾼다
    plate: Plate = APRIL_PLATE     # 제목판 기하 — 세로 중심·오른쪽 경계 (달마다 틀에서 잰다)


# scene 은 실험(worklog 09-13~14)에서 검증된 달만 채워져 있다. 다른 달을 열 때는 그 달의
# 무대(소품·매트·배경)와 의상을 같은 식으로 적고, 제목판(세로 중심·오른쪽 경계)을 재고, 실험으로 검증한 뒤
# DAENGS_CARDIMAGE_MONTHS 에 넣는다.
_CARDS: dict[int, MonthCard] = {
    1: MonthCard(1, "1_new_year", "NEW YEAR", "26JAN", "", "JANUARY SPECIAL"),
    2: MonthCard(2, "2_love", "LOVE", "26FEB", "", "FEBRUARY SPECIAL"),
    3: MonthCard(3, "3_first_day", "FIRST DAY", "26MAR", "", "MARCH SPECIAL"),
    4: MonthCard(
        4, "4_blossom", "BLOSSOM", "26APR",
        "the pose (sitting on the picnic blanket looking up at a falling petal), "
        "the green-and-white checked picnic blanket (keep this exact color and pattern), the wicker basket",
        "APRIL SPECIAL",
    ),
    5: MonthCard(5, "5_home_team", "HOME TEAM", "26MAY", "", "MAY SPECIAL"),
    6: MonthCard(6, "6_pool", "POOL", "26JUN", "", "JUNE SPECIAL"),
    7: MonthCard(7, "7_beach", "BEACH", "26JUL", "", "JULY SPECIAL"),
    8: MonthCard(8, "8_rain", "RAIN", "26AUG", "", "AUGUST SPECIAL"),
    9: MonthCard(
        9, "9_harvest_moon", "CHUSEOK", "26SEP",   # 원본 제목은 HARVEST MOON — 너무 길어 사용자가 CHUSEOK 으로 (09-14)
        "the pose (standing upright on the stone steps in front of the traditional Korean hanok, holding the tray "
        "with both front paws, smiling), the full harvest moon, the glowing paper lanterns, the autumn maple leaves, "
        "the pampas grass, the dark wooden tray of colorful songpyeon rice cakes",
        "SEPTEMBER SPECIAL",
        # 한복은 카드의 일부다 — 사진의 소품은 막되 이미지 1 의 옷·쟁반은 그대로.
        "The dog must wear exactly the same hanbok as the dog in image 1 (cream jacket with floral pattern, "
        "sage-green ribbon, coral-pink skirt with gold flowers and the tassel ornament) and hold the same tray of "
        "songpyeon. Do not carry over any accessories from image 2 — no collar, no leash, no harness.",
        SEPTEMBER_PLATE,
    ),
    10: MonthCard(10, "10_ghost", "GHOST", "26OCT", "", "OCTOBER SPECIAL"),
    11: MonthCard(11, "11_thanks", "THANKS", "26NOV", "", "NOVEMBER SPECIAL"),
    12: MonthCard(12, "12_santa", "SANTA", "26DEC", "", "DECEMBER SPECIAL"),
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
