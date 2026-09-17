"""카드(달 1~12 + 종류) → 틀 파일·카드명·장면 설명. `cardimage/headers.json` 의 제목에서 ` NEO` 를 뗀 것이 카드명이다.

달은 정수, 달이 아닌 카드(딸기·상추, 콘솔 전용 #592)는 문자열로 가리킨다 — `CardSelector` 와 `resolve`.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path

from daengs_cardimage.title import APRIL_PLATE, Plate

log = logging.getLogger(__name__)


class MonthNotOpenError(Exception):
    """틀은 있지만 설정(DAENGS_CARDIMAGE_MONTHS)으로 잠긴 달. 라우터가 404 로 바꾼다."""


#: 아직 실험으로 검증되지 않은 달이 fallback 으로 쓰는 seed 목록 (#572 Task 3a, R5).
#: 검증된 목록이 없다고 생성을 막지 않는다 — 12달이 이미 열려 있어서 막으면 10/12 달이 500 이 된다.
DEFAULT_SEEDS: tuple[int, ...] = (1, 2, 3, 4, 5, 6)


#: 업로드 화면에 띄우는 사진 안내 (#572 Task 6). `GET /app/ai-cards` 응답의 `photo_guidance` 로 나가
#: `SAJOYO/DAENGS_APP` 과 이 저장소의 관리자 콘솔(`frontend/app/components/cardimage-inspect.tsx`)이
#: 같은 문구를 쓴다 — 콘솔 쪽 상수는 이 값과 글자까지 맞춰야 한다.
#: 근거: 정면 사진 72장의 판정기 닮음 평균이 4.11/5 였고(`docs/cardimage/compare-2026-09-16-months-seeds.md`),
#: 엎드린 옆모습 사진은 #557 E2 에서 4장을 뽑아도 쓸 만한 장이 0장이었다
#: (`docs/cardimage/compare-2026-09-16-klein-e2-e3.md`). 사진이 보증하는 것은 아니다 — 이와 별개로
#: 4월은 seed 와 무관하게 사용자 사진이 아닌 참조 카드 강아지로 나오는 미해결 결함이 있다(같은 문서).
PHOTO_GUIDANCE = "얼굴이 정면으로 보이고 앉아 있는 사진이 가장 잘 나와요. 엎드려 있거나 옆을 보는 사진은 닮지 않게 나올 수 있어요."


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
    #: 이 달 틀에서 제목·아래 패널 글씨가 안 깨진다고 실험으로 확인된 seed (#557 E1·E2, #572 Task 3).
    #: 글씨 깨짐은 사진이 아니라 (틀, seed, 크기) 로 정해지므로 여기서 뽑으면 깨진 장이 안 나온다.
    #: 비어 있으면(`()`) 아직 실험으로 확인되지 않았다는 뜻이다 — `pick_seeds` 가 DEFAULT_SEEDS 로 대신한다.
    seeds: tuple[int, ...] = ()
    #: 옷이 본문 강아지의 얼굴까지 덮는 틀(10월 유령 천). 프롬프트가 사진 강아지를 배지와 발로만 옮긴다(`engine.build_prompt`).
    face_hidden: bool = False
    #: 본문에 강아지 **얼굴만** 보이는 틀(딸기·상추). 10월 `face_hidden` 의 반대다 — `engine.build_prompt` 가 앞부분을 가른다.
    face_only: bool = False
    #: 달이 아닌 카드의 키("strawberry"·"lettuce"). 달 카드는 빈 문자열이고 `month` 로 식별한다.
    kind: str = ""


# scene 은 실험(worklog 09-13~14)에서 검증된 달만 채워져 있다. 다른 달을 열 때는 그 달의
# 무대(소품·매트·배경)와 의상을 같은 식으로 적고, 제목판(세로 중심·오른쪽 경계)을 재고, 실험으로 검증한 뒤
# DAENGS_CARDIMAGE_MONTHS 에 넣는다.
#
# ⚠ 4월과 같은 판 범위(y 52~145)인 달은 center_y 를 99(APRIL_PLATE 값)로 맞춘다.
#   `plate_probe.measure()` 는 정수 나눗셈이라 (52+145)//2 = 98 을 내놓지만, 99 는
#   2026-09-14 에 바로 이 기하로 사람이 눈으로 보고 고른 값이다(두 번 교정 끝). 1px 차이라
#   `measure()` 의 결과가 "틀렸다"는 뜻이 아니다 — 그 도구는 참고용이고, 카드에 실제로
#   나가는 값은 이 상수다. `measure()` 자체는 고치지 않는다.
_CARDS: dict[int, MonthCard] = {
    1: MonthCard(
        1, "1_new_year", "SEBAE", "26JAN",   # 원본 제목은 NEW YEAR — 앞말이 판을 거의 채워 사용자가 SEBAE 로 (09-18)
        "the pose (kneeling upright with both front paws stacked together on the tasseled cushion in a bowing "
        "posture), the hanging red-and-blue lantern by the hanok pillar (keep this exact color), the "
        "snow-covered hanok roof tiles and stone wall, the bare persimmon branch with snow and red persimmons, "
        "the magpie perched on the branch, the sunrise over the snowy mountains",
        "JANUARY SPECIAL",
        JANUARY_OUTFIT,
        Plate(center_y=99, edge=((55, 749), (142, 678)), top_y=52),
        seeds=(1, 2, 3, 4, 6),  # 09-16 12달×seed 실험, 눈으로 확인 (task-3b-visual-report.md)
    ),
    2: MonthCard(
        2, "2_love", "LOVE", "26FEB",
        "the pose (leaping mid-air with both front paws raised, mouth open in a joyful bark), the open red "
        "heart-shaped gift box with the pink satin ribbon bow (keep this exact color), the swirling red rose "
        "petals and glowing pink hearts, the red roses banked along the bottom, the candlelit red curtain "
        "backdrop",
        "FEBRUARY SPECIAL",
        NO_OUTFIT,
        Plate(center_y=99, edge=((55, 775), (142, 703)), top_y=52),
        seeds=(1, 6),  # 09-16 12달×seed 실험, 눈으로 확인 — 나머지는 부제 배너 자체가 통째로 안 나온다
    ),
    3: MonthCard(
        3, "3_first_day", "SCHOOL", "26MAR",   # 원본 제목은 FIRST DAY — 같은 이유로 SCHOOL (09-18)
        "the pose (running mid-stride toward the camera with the red pencil held in its mouth), the forsythia "
        "blossoms and loose lined notebook pages flying past, the wrought-iron school gate with the carved "
        'stone pillar reading "학교" (keep this text), the round clock tower, the fallen yellow petals on the '
        "pavement",
        "MARCH SPECIAL",
        MARCH_OUTFIT,
        Plate(center_y=99, edge=((55, 776), (142, 706)), top_y=52),
        seeds=(1, 3),  # 09-16 12달×seed 실험, 눈으로 확인 (task-3b-visual-report.md)
    ),
    4: MonthCard(
        4, "4_blossom", "BLOSSOM", "26APR",
        "the pose (sitting on the picnic blanket looking up at a falling petal), "
        "the green-and-white checked picnic blanket (keep this exact color and pattern), the wicker basket",
        "APRIL SPECIAL",
        # 09-16 12달×seed 실험으로 (3, 4) → (2, 3) 로 바뀜: 이번 사진에서는 seed 4 가 부제
        # "APRIIAL"(SPECIAL 통째로 소실)로 깨졌고 seed 2 는 깨끗했다. #557 의 (3, 4) 는 다른
        # 사진(정면 `_03`)에서 확인된 값이라 재현되지 않았다 — seed 는 (틀, 크기) 뿐 아니라
        # 사진에도 영향을 받는다는 뜻이라, 이 목록은 "깨짐을 줄이는 필터"이지 "보증"이 아니다.
        # ⚠ 별개의 미해결 결함: FLUX.2-klein-4B 에서는 seed 와 무관하게 6장 전부 강아지가 사용자
        # 사진이 아니라 원본 참조 카드의 주인공 「네오」(크림색 곱슬 푸들)로 나온다.
        # DAENGS_CARDGEN_URL 을 켜서 FLUX.2-klein-4B 를 쓰기 전에 반드시 확인해야 한다 —
        # 지금 운영(Nano Banana 2)은 이 결함의 영향을 받지 않는다.
        seeds=(2, 3),
    ),
    5: MonthCard(
        5, "5_home_team", "HOME", "26MAY",   # 원본 제목은 HOME TEAM — 앞말만으로 판을 넘겨 HOME (09-18)
        "the pose (sitting upright with one front paw resting on the open page of the photo album), the vase of "
        "red-and-pink carnations with the gold paw-charm pendant on a pink ribbon (keep this exact color), the "
        "plaid-cushioned sofa with the heart-stitched pillow and framed paw-print art, the scattered polaroid "
        "photos and red ribbon on the lace doily, the small gold trinket box",
        "MAY SPECIAL",
        NO_OUTFIT,
        Plate(center_y=99, edge=((55, 748), (142, 677)), top_y=52),
        seeds=(1, 3, 4),  # 09-16 12달×seed 실험, 눈으로 확인 (task-3b-visual-report.md)
    ),
    6: MonthCard(
        6, "6_pool", "POOL", "26JUN",
        "the pose (sitting atop the giant inflatable rubber duck float with both front paws raised mid-splash), "
        "the giant yellow rubber duck pool float (keep this exact color), the splashing turquoise pool water, "
        "the poolside black fence and lounge chairs with the blue-striped cushions, the pink flowers, the "
        "striped beach ball floating nearby",
        "JUNE SPECIAL",
        JUNE_OUTFIT,
        Plate(center_y=99, edge=((55, 745), (142, 677)), top_y=52),
        seeds=(1, 2, 3, 5, 6),  # 09-16 12달×seed 실험, 눈으로 확인 (task-3b-visual-report.md)
    ),
    7: MonthCard(
        7, "7_beach", "BEACH", "26JUL",
        "the pose (lying back on the striped beach towel with front paws relaxed), the red-and-white striped "
        "beach umbrella, the blue-and-white striped towel (keep this exact color and pattern), the coconut "
        "drink with the straw and pink paper umbrella, the starfish and seashell in the sand, the turquoise "
        "ocean and distant island",
        "JULY SPECIAL",
        JULY_OUTFIT,
        Plate(center_y=99, edge=((55, 774), (142, 702)), top_y=52),
        seeds=(1, 2, 3, 4, 6),  # 09-16 12달×seed 실험, 눈으로 확인 (task-3b-visual-report.md)
    ),
    8: MonthCard(
        8, "8_rain", "RAIN", "26AUG",
        "the pose (running mid-stride through the puddle, splashing water on both sides), the rain-soaked night "
        "street with the glowing shopfront windows and blurred neon signs, the lightning bolt across the dark "
        "sky, the purple hydrangea blossoms and green leaves in the foreground (keep this exact color), the "
        "reflections in the puddle",
        "AUGUST SPECIAL",
        AUGUST_OUTFIT,
        Plate(center_y=99, edge=((55, 804), (142, 733)), top_y=52),
        seeds=(1, 2, 3, 4, 5, 6),  # 09-16 12달×seed 실험, 눈으로 확인 — 6장 전부 깨끗했다
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
        # 09-16 12달×seed 실험으로 (1, 4) → (1, 2, 3, 4, 5) 로 바뀜: 이번 사진에서는 seed 6 이
        # 캡션 "surprise"→"surprie" 로 깨졌고 나머지 다섯은 깨끗했다. #557 의 (1, 4) 는 다른
        # 사진에서 확인된 값이라 그대로 재현되지는 않았다 — 이 목록은 깨짐을 줄이는 필터일 뿐,
        # 사진이 바뀌면 같은 seed 도 결과가 달라질 수 있다는 뜻이다(4월도 같은 이유로 바뀜).
        seeds=(1, 2, 3, 4, 5),
    ),
    10: MonthCard(
        10, "10_ghost", "GHOST", "26OCT",
        "the pose (floating mid-air draped in the ghost sheet, one paw peeking out to hold the jack-o'-lantern "
        "bucket, hind paws peeking from under the hem), the glowing carved jack-o'-lanterns lining the path, "
        "the wrought-iron cemetery fence and bare gnarled tree, the bats and crescent moon, the haunted house "
        "with lit windows against the starry purple sky",
        "OCTOBER SPECIAL",
        OCTOBER_OUTFIT,
        Plate(center_y=99, edge=((55, 784), (142, 713)), top_y=52),
        seeds=(2, 4, 5, 6),  # 09-16 12달×seed 실험, 눈으로 확인 (task-3b-visual-report.md)
        # 09-16 격자(페키니즈 사진, 옛 프롬프트)에서 seed 2·3·4 는 천 위에 실제 얼굴이 합성됐다 — 공통 앞부분의 얼굴 요구 탓이라 face_hidden 앞부분으로 바꿨다.
        # 09-18 새 프롬프트 재확인(FLUX.2-klein-4B, 치와와 사진, seed 1~6): 6장 모두 천 유지·얼굴 합성 0,
        # 글씨는 seed 1·3 깨짐 · 2·4·5·6 정상 → 목록 그대로 둔다. 표본 6장·사진 1장이라 잠정.
        face_hidden=True,
    ),
    11: MonthCard(
        11, "11_thanks", "THANKS", "26NOV",
        "the pose (driving the tractor with both front paws on the steering wheel), the red vintage tractor "
        "pulling the wooden trailer loaded with pumpkins, corn husks, apples and gourds under the plaid "
        # 풍향계 실루엣은 부채꼴로 펼친 꽁지깃·볏(wattle)·칠면조 머리 모양이다 — 수탉이 아니라
        # 칠면조다(리뷰 09-16, 확대 확인). 추수감사절 카드의 핵심 소품이라 잘못 쓰면 안 된다.
        "blanket, the red maple leaves scattered on the ground, the red barn with the turkey weathervane, the "
        "wooden fence and the sunset over the hills",
        "NOVEMBER SPECIAL",
        NOVEMBER_OUTFIT,
        Plate(center_y=99, edge=((55, 771), (142, 701)), top_y=52),
        seeds=(1, 2, 3, 6),  # 09-16 12달×seed 실험, 눈으로 확인 (task-3b-visual-report.md)
    ),
    12: MonthCard(
        12, "12_santa", "SANTA", "26DEC",
        "the pose (sitting in the golden sleigh, both front paws gripping the reins), the ornate red-and-gold "
        "sleigh piled with wrapped gifts and the teddy bear, the pine-and-holly garland with the red bow and "
        "gold bell at the front of the sleigh, the full moon, the snow-covered pine trees, the snowy village "
        "with lit windows and the Christmas tree below",
        "DECEMBER SPECIAL",
        DECEMBER_OUTFIT,
        Plate(center_y=99, edge=((55, 771), (142, 712)), top_y=52),
        seeds=(1, 3, 5, 6),  # 09-16 12달×seed 실험, 눈으로 확인 (task-3b-visual-report.md)
    ),
}


#: 딸기 틀의 제목판 — 09-18 실측(판 y 59~152). `top_y` 는 측정값 59 가 아니라 62 다:
#: 윗선을 다시 재는 네 열의 중앙값이 62 라, 59 로 두면 제목이 3px 내려간다(사용자 결정 09-18).
STRAWBERRY_PLATE = Plate(center_y=105, edge=((62, 749), (149, 676)), top_y=62)

#: 상추 틀의 제목판 — 09-18 실측(판 y 47~141). 달 카드보다 넓다.
LETTUCE_PLATE = Plate(center_y=94, edge=((50, 790), (138, 717)), top_y=47)

#: 달이 아닌 카드(콘솔 전용, #592). 앱 경로는 이것을 쓰지 않는다.
#: `outfit` 은 비워 둔다 — 몸이 없어서 입힐 곳이 없고, 소품 금지 문장은 `face_only` 앞부분이 직접 갖는다
#: (설계 ②). 여기에 `NO_OUTFIT` 을 넣으면 같은 문장이 프롬프트에 두 번 들어간다.
_KIND_CARDS: dict[str, MonthCard] = {
    "strawberry": MonthCard(
        0, "strawberry", "BERRY", "NEO-S0824",
        "the giant leaf parachute with its golden rigging lines, the heart-shaped strawberry body with its seeds "
        "and the round hole in its middle, the pink and golden motion streaks, the floating golden seeds, the "
        "pastel blue-violet starry sky and the pink clouds",
        "FRUIT DOG",
        "",
        STRAWBERRY_PLATE,
        face_only=True,
        kind="strawberry",
    ),
    "lettuce": MonthCard(
        0, "lettuce", "LETTUCE", "NEO-0824",
        "the ruffled lettuce leaves with water droplets that form the body, the two crossed lettuce stems below, "
        "the loose leaves floating around, the radiating rainbow holographic rays and the soft reflective floor",
        "VEGGIE DOG",
        "",
        LETTUCE_PLATE,
        face_only=True,
        kind="lettuce",
    ),
}

KINDS: tuple[str, ...] = tuple(_KIND_CARDS)

#: 카드 하나를 가리키는 값 — 1~12 는 달, 문자열은 종류(`KINDS`)다.
CardSelector = int | str


def get(month: int) -> MonthCard:
    return _CARDS[month]


def resolve(selector: CardSelector) -> MonthCard:
    """달 정수 또는 종류 문자열로 카드 정의를 찾는다. 없으면 `MonthNotOpenError`.

    잠금(`DAENGS_CARDIMAGE_MONTHS`)은 보지 않는다 — 그건 앱 경로의 `require_open` 몫이다."""
    if isinstance(selector, bool):  # bool 은 int 의 하위형이다 — 달로 오인하지 않게 먼저 막는다
        raise MonthNotOpenError(f"card {selector!r} is not a card")
    if isinstance(selector, int):
        card = _CARDS.get(selector)
    else:
        card = _KIND_CARDS.get(selector)
    if card is None:
        raise MonthNotOpenError(f"card {selector!r} is not a card")
    return card


def card_key(selector: CardSelector) -> str:
    """저장·로그에 쓰는 문자열 키 — 달은 `"4"`, 종류는 `"strawberry"`."""
    return str(selector)


def pick_seeds(selector: CardSelector, count: int, rng: random.Random) -> list[int]:
    """그 카드의 검증된 seed 에서 `count` 개를 겹치지 않게 뽑는다. 목록이 모자라면 되풀이한다.

    아직 실험으로 확인되지 않은 카드(`seeds == ()`)는 예외를 내지 않는다 — 12달이 이미 열려 있어서
    막으면 검증이 끝나지 않은 열 달이 전부 500 이 된다(#572 Task 3a, controller ruling R5). 대신
    DEFAULT_SEEDS 로 대신하고, 나중에 실험 결과를 보고 이 로그를 찾을 수 있게 warning 을 남긴다.
    """
    pool = list(resolve(selector).seeds)
    if not pool:
        log.warning("cardimage card %s has no verified seeds — using unverified defaults %s", selector, DEFAULT_SEEDS)
        pool = list(DEFAULT_SEEDS)
    picked: list[int] = []
    while len(picked) < count:
        rng.shuffle(pool)
        picked.extend(pool[: count - len(picked)])
    return picked


def require_open(month: int, open_months: frozenset[int]) -> MonthCard:
    card = _CARDS.get(month)
    if card is None or month not in open_months or not card.scene:
        raise MonthNotOpenError(f"month {month} is not open")
    return card


def template_path(selector: CardSelector, base: Path) -> Path:
    return base / f"{resolve(selector).stem}_template.webp"


def font_path(base: Path) -> Path:
    return base / "fonts" / "NotoSerifKR.ttf"
