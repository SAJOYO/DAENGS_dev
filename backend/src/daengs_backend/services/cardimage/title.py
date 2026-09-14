"""틀 위에 `<카드명> <이름>` 을 Pillow 로 얹는다. 값의 원본과 실험 근거는 backend/tools/cardimage_title.py 와 worklog 09-14.

이름·한글을 모델에 쓰게 하지 않는 이유: 한글이 깨지고, 글꼴이 사용자마다 달라지고, 길이를 못 맞춘다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

LEFT_X = 252  # 원본 제목은 268 에서 시작했지만 사용자가 16px 왼쪽을 골랐다 (09-14, `_left_x_variants.png` 3번)
CAP_HEIGHT = 48
RIGHT_MARGIN = 14
MIN_CAP = 20

Edge = tuple[tuple[int, int], tuple[int, int]]


@dataclass(frozen=True)
class Plate:
    """한 달 틀의 검은 제목판 기하. 달마다 판의 높이·위치·오른쪽 경계가 달라서(09-14 실측) 틀별로 잰다.

    - `center_y`: 틀에서 잰 판의 세로 중심. 대문자 띠의 잉크 중심을 여기에 맞춘다 (사용자 09-14: 판 중심 정렬).
    - `edge`: 배지와 맞닿는 기울어진 오른쪽 경계 — (y, x) 두 점. 글자가 이 선(여백 14)을 넘으면 축소.
    - `top_y`: 틀에서 잰 판의 윗선. 모델이 그린 카드는 판이 틀과 몇 px 어긋나므로(4·9월 출력 모두
      4~5px 위 — 09-14 실측) 출력에서 윗선을 다시 재어 그 차이만큼 `center_y` 를 옮긴다 (`_plate_shift`).
    - `left_x`: 글자 시작 x. 판 왼쪽 끝은 달이 같아(4월 226 · 9월 228) 기본값 하나로 둔다.
    """

    center_y: int
    edge: Edge
    top_y: int
    left_x: int = LEFT_X


#: 4월 틀 — 판 y 53~145, 오른쪽 경계 y65→x791 · y135→x758. 기본값.
APRIL_PLATE = Plate(center_y=99, edge=((65, 791), (135, 758)), top_y=52)

#: 판 윗선을 다시 잴 때 보는 열 — 판 왼쪽 끝(226~228)과 글자 시작(252) 사이라 글자·그림자가 없다.
PLATE_PROBE_XS = (234, 238, 242, 246)
PLATE_DARK = 70          # 이 값 미만(RGB 최대치)이면 검은 판
PLATE_MIN_RUN = 15       # 윗선 아래로 이만큼 연속으로 어두워야 판이다 (테두리 선은 2~3px)
PLATE_MAX_SHIFT = 20     # 이보다 크게 어긋나면 판을 못 찾은 것으로 보고 옮기지 않는다
KR_WEIGHT = "Black"
FILL_TOP, FILL_BOTTOM = (252, 252, 252), (200, 202, 208)
STROKE, STROKE_W = (22, 22, 30), 3
SHADOW, SHADOW_DY = (0, 0, 0, 150), 3
MAX_NAME = 40


def title_text(card_name: str, dog_name: str) -> str:
    name = " ".join(dog_name.split())[:MAX_NAME]
    return f"{card_name} {name.upper()}".strip()   # 한글은 upper() 에 영향 없음


def _plate_right_edge(y: float, edge: Edge) -> float:
    (y0, x0), (y1, x1) = edge
    t = (y - y0) / (y1 - y0)
    return x0 + t * (x1 - x0)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(path), size)
    try:
        f.set_variation_by_name(KR_WEIGHT)
    except (OSError, ValueError):
        pass
    return f


def _size_for_cap(path: Path, cap: int) -> int:
    _, t, _, b = _font(path, 100).getbbox("H")
    return max(8, round(100 * cap / (b - t)))


@dataclass
class _Run:
    text: str
    font: ImageFont.FreeTypeFont
    x: int
    y: int


def _plate_top(card: Image.Image, x: int, expect: int) -> int | None:
    """열 x 에서 검은 판의 윗선 y. `expect` 주변 ±PLATE_MAX_SHIFT 만 본다."""
    px = card.load()
    lo, hi = max(0, expect - PLATE_MAX_SHIFT), min(card.height - PLATE_MIN_RUN, expect + PLATE_MAX_SHIFT)
    for y in range(lo, hi + 1):
        if all(max(px[x, yy][:3]) < PLATE_DARK for yy in range(y, y + PLATE_MIN_RUN)):
            return y
    return None


def _plate_shift(card: Image.Image, plate: Plate) -> int:
    """모델이 그린 판이 틀보다 얼마나 위·아래로 어긋났는지(px). 못 재면 0."""
    tops = [t for x in PLATE_PROBE_XS if (t := _plate_top(card, x, plate.top_y)) is not None]
    if len(tops) < 2:
        return 0
    tops.sort()
    return tops[len(tops) // 2] - plate.top_y


def _layout(text: str, font_path: Path, plate: Plate, center_y: int) -> list[_Run]:
    cap = CAP_HEIGHT
    while True:
        f = _font(font_path, _size_for_cap(font_path, cap))
        ascent, _ = f.getmetrics()
        baseline = center_y + cap // 2
        y = baseline - ascent
        _, t, r, b = f.getbbox(text)
        right = plate.left_x + r + STROKE_W
        limit = _plate_right_edge(baseline + 10, plate.edge) - RIGHT_MARGIN
        if right <= limit or cap <= MIN_CAP:
            shift = round(center_y - (y + t + y + b) / 2)   # 잉크 중심을 판 중심에
            return [_Run(text, f, plate.left_x, y + shift)]
        cap -= 1


def draw_title(card: Image.Image, text: str, font_path: Path, *, plate: Plate = APRIL_PLATE) -> Image.Image:
    """`plate` 는 그 달 틀의 제목판 기하(catalog.MonthCard.plate). 기본값은 4월.

    판 중심은 틀에서 잰 값에 **이 카드에서 다시 잰 윗선의 차이**를 더한 것이다 — 모델 출력은
    틀과 몇 px 어긋난다. 틀 자체에 얹으면 차이가 0 이라 잰 값 그대로다."""
    rgb = card.convert("RGB")
    center_y = plate.center_y + _plate_shift(rgb, plate)
    runs = _layout(text, font_path, plate, center_y)
    base = card.convert("RGBA")
    W, H = base.size
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for r in runs:
        sd.text((r.x, r.y + SHADOW_DY), r.text, font=r.font, fill=SHADOW, stroke_width=STROKE_W, stroke_fill=SHADOW)
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(2)))
    bd = ImageDraw.Draw(base)
    for r in runs:
        bd.text((r.x, r.y), r.text, font=r.font, fill=STROKE + (255,), stroke_width=STROKE_W, stroke_fill=STROKE + (255,))
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    for r in runs:
        md.text((r.x, r.y), r.text, font=r.font, fill=255)
    bbox = mask.getbbox()
    if bbox:
        l, t, rr, b = bbox
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        for yy in range(t, b + 1):
            k = (yy - t) / max(1, (b - t))
            c = tuple(round(FILL_TOP[i] * (1 - k) + FILL_BOTTOM[i] * k) for i in range(3))
            gd.line((l, yy, rr, yy), fill=c + (255,))
        base.paste(grad, (0, 0), mask)
    return base.convert("RGB")
