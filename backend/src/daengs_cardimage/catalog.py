"""달 → 틀 파일·카드명·장면 설명. `cardimage/headers.json` 의 제목에서 ` NEO` 를 뗀 것이 카드명이다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from daengs_cardimage.title import APRIL_PLATE, Plate


class MonthNotOpenError(Exception):
    """틀은 있지만 설정(DAENGS_CARDIMAGE_MONTHS)으로 잠긴 달. 라우터가 404 로 바꾼다."""


#: 9월 틀의 검은 제목판 — 4월보다 11px 위에 있다(y 40~135, 4월은 53~145) 그리고 약 40px 좁다.
#: 09-14 실측: 세로는 x=270~640 다섯 열 모두 중심 87.5~88.0, 오른쪽 경계 y65→x750 · y100→731 · y135→709.
SEPTEMBER_PLATE = Plate(center_y=88, edge=((65, 750), (135, 709)), top_y=40)

#: 4월처럼 강아지가 아무것도 안 입는 달의 의상 문장. 사진의 목줄·리드줄이 카드로 옮겨 오는 것을 막는다.
NO_OUTFIT = (
    "Do not carry over any accessories from image 2 — no collar, no leash, no harness, no clothing; "
    "the dog wears nothing, exactly like the dog in image 1."
)

#: 1월 틀의 강아지는 한복을 입고 방석에 앉아 세배하는 자세다.
JANUARY_OUTFIT = (
    "The dog must wear exactly the same hanbok as the dog in image 1 (navy-blue jacket with gold floral pattern, "
    "white cuffs, coral-red tassel ornament, and the flowing crimson-red skirt beneath). Do not carry over any "
    "accessories from image 2 — no collar, no leash, no harness."
)

#: 3월 틀의 강아지는 노란 책가방을 메고 있다.
MARCH_OUTFIT = (
    "The dog must wear exactly the same yellow backpack with black straps and the black paw-print front pocket as "
    "the dog in image 1, with the same paw-print name tag hanging from the strap. Do not carry over any "
    "accessories from image 2 — no collar, no leash, no harness."
)

#: 6월 틀의 강아지는 파란 구명조끼를 입고 있다.
JUNE_OUTFIT = (
    "The dog must wear exactly the same blue life vest with black buckles as the dog in image 1. Do not carry "
    "over any accessories from image 2 — no collar, no leash, no harness."
)

#: 7월 틀의 강아지는 선글라스와 하와이안 수영복을 입고 있다.
JULY_OUTFIT = (
    "The dog must wear exactly the same gold-rimmed round sunglasses and the same teal swim trunks with the "
    "red-and-yellow hibiscus pattern and white drawstring as the dog in image 1. Do not carry over any "
    "accessories from image 2 — no collar, no leash, no harness."
)

#: 8월 틀의 강아지는 노란 우비와 장화를 신고 있다.
AUGUST_OUTFIT = (
    "The dog must wear exactly the same yellow hooded raincoat and the same yellow paw-print rain boots as the "
    "dog in image 1. Do not carry over any accessories from image 2 — no collar, no leash, no harness."
)

#: 10월 틀의 강아지는 눈·입 세 개를 오려 붙인 흰 천을 덮어쓴 유령 분장이다.
OCTOBER_OUTFIT = (
    "The dog must wear exactly the same white ghost-sheet costume (draped over the whole body, with the same "
    "three black cut-out eyes and mouth) as the dog in image 1, with only the paws peeking out, holding the same "
    "jack-o'-lantern candy bucket. Do not carry over any accessories from image 2 — no collar, no leash, no "
    "harness."
)

#: 11월 틀의 강아지는 밀짚모자·빨간 손수건·멜빵청바지 차림으로 트랙터를 몬다.
NOVEMBER_OUTFIT = (
    "The dog must wear exactly the same straw hat, red bandana and denim overalls as the dog in image 1. Do not "
    "carry over any accessories from image 2 — no collar, no leash, no harness."
)

#: 12월 틀의 강아지는 산타 옷을 입고 있다 — 9월 한복과 같은 처리.
DECEMBER_OUTFIT = (
    "The dog must wear exactly the same santa outfit as the dog in image 1. Do not carry over any accessories "
    "from image 2 — no collar, no leash, no harness."
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
    1: MonthCard(
        1, "1_new_year", "NEW YEAR", "26JAN",
        "the pose (kneeling upright with both front paws stacked together on the tasseled cushion in a bowing "
        "posture), the hanging red-and-blue lantern by the hanok pillar (keep this exact color), the "
        "snow-covered hanok roof tiles and stone wall, the bare persimmon branch with snow and red persimmons, "
        "the magpie perched on the branch, the sunrise over the snowy mountains",
        "JANUARY SPECIAL",
        JANUARY_OUTFIT,
        Plate(center_y=98, edge=((55, 749), (142, 678)), top_y=52),
    ),
    2: MonthCard(
        2, "2_love", "LOVE", "26FEB",
        "the pose (leaping mid-air with both front paws raised, mouth open in a joyful bark), the open red "
        "heart-shaped gift box with the pink satin ribbon bow (keep this exact color), the swirling red rose "
        "petals and glowing pink hearts, the red roses banked along the bottom, the candlelit red curtain "
        "backdrop",
        "FEBRUARY SPECIAL",
        NO_OUTFIT,
        Plate(center_y=98, edge=((55, 775), (142, 703)), top_y=52),
    ),
    3: MonthCard(
        3, "3_first_day", "FIRST DAY", "26MAR",
        "the pose (running mid-stride toward the camera with the red pencil held in its mouth), the forsythia "
        "blossoms and loose lined notebook pages flying past, the wrought-iron school gate with the carved "
        'stone pillar reading "학교" (keep this text), the round clock tower, the fallen yellow petals on the '
        "pavement",
        "MARCH SPECIAL",
        MARCH_OUTFIT,
        Plate(center_y=98, edge=((55, 776), (142, 706)), top_y=52),
    ),
    4: MonthCard(
        4, "4_blossom", "BLOSSOM", "26APR",
        "the pose (sitting on the picnic blanket looking up at a falling petal), "
        "the green-and-white checked picnic blanket (keep this exact color and pattern), the wicker basket",
        "APRIL SPECIAL",
    ),
    5: MonthCard(
        5, "5_home_team", "HOME TEAM", "26MAY",
        "the pose (sitting upright with one front paw resting on the open page of the photo album), the vase of "
        "red-and-pink carnations with the gold paw-charm pendant on a pink ribbon (keep this exact color), the "
        "plaid-cushioned sofa with the heart-stitched pillow and framed paw-print art, the scattered polaroid "
        "photos and red ribbon on the lace doily, the small gold trinket box",
        "MAY SPECIAL",
        NO_OUTFIT,
        Plate(center_y=98, edge=((55, 748), (142, 677)), top_y=52),
    ),
    6: MonthCard(
        6, "6_pool", "POOL", "26JUN",
        "the pose (sitting atop the giant inflatable rubber duck float with both front paws raised mid-splash), "
        "the giant yellow rubber duck pool float (keep this exact color), the splashing turquoise pool water, "
        "the poolside black fence and lounge chairs with the blue-striped cushions, the pink flowers, the "
        "striped beach ball floating nearby",
        "JUNE SPECIAL",
        JUNE_OUTFIT,
        Plate(center_y=98, edge=((55, 745), (142, 677)), top_y=52),
    ),
    7: MonthCard(
        7, "7_beach", "BEACH", "26JUL",
        "the pose (lying back on the striped beach towel with front paws relaxed), the red-and-white striped "
        "beach umbrella, the blue-and-white striped towel (keep this exact color and pattern), the coconut "
        "drink with the straw and pink paper umbrella, the starfish and seashell in the sand, the turquoise "
        "ocean and distant island",
        "JULY SPECIAL",
        JULY_OUTFIT,
        Plate(center_y=98, edge=((55, 774), (142, 702)), top_y=52),
    ),
    8: MonthCard(
        8, "8_rain", "RAIN", "26AUG",
        "the pose (running mid-stride through the puddle, splashing water on both sides), the rain-soaked night "
        "street with the glowing shopfront windows and blurred neon signs, the lightning bolt across the dark "
        "sky, the purple hydrangea blossoms and green leaves in the foreground (keep this exact color), the "
        "reflections in the puddle",
        "AUGUST SPECIAL",
        AUGUST_OUTFIT,
        Plate(center_y=98, edge=((55, 804), (142, 733)), top_y=52),
    ),
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
    10: MonthCard(
        10, "10_ghost", "GHOST", "26OCT",
        "the pose (floating mid-air draped in the ghost sheet, one paw peeking out to hold the jack-o'-lantern "
        "bucket, hind paws peeking from under the hem), the glowing carved jack-o'-lanterns lining the path, "
        "the wrought-iron cemetery fence and bare gnarled tree, the bats and crescent moon, the haunted house "
        "with lit windows against the starry purple sky",
        "OCTOBER SPECIAL",
        OCTOBER_OUTFIT,
        Plate(center_y=98, edge=((55, 784), (142, 713)), top_y=52),
    ),
    11: MonthCard(
        11, "11_thanks", "THANKS", "26NOV",
        "the pose (driving the tractor with both front paws on the steering wheel), the red vintage tractor "
        "pulling the wooden trailer loaded with pumpkins, corn husks, apples and gourds under the plaid "
        "blanket, the red maple leaves scattered on the ground, the red barn with the rooster weathervane, the "
        "wooden fence and the sunset over the hills",
        "NOVEMBER SPECIAL",
        NOVEMBER_OUTFIT,
        Plate(center_y=98, edge=((55, 771), (142, 701)), top_y=52),
    ),
    12: MonthCard(
        12, "12_santa", "SANTA", "26DEC",
        "the pose (sitting in the golden sleigh, both front paws gripping the reins), the ornate red-and-gold "
        "sleigh piled with wrapped gifts and the teddy bear, the pine-and-holly garland with the red bow and "
        "gold bell at the front of the sleigh, the full moon, the snow-covered pine trees, the snowy village "
        "with lit windows and the Christmas tree below",
        "DECEMBER SPECIAL",
        DECEMBER_OUTFIT,
        Plate(center_y=98, edge=((55, 771), (142, 712)), top_y=52),
    ),
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
