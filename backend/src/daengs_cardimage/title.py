"""틀 위에 `<카드명> <이름>` 을 Pillow 로 얹는다. 값의 원본과 실험 근거는 backend/tools/cardimage_title.py 와 worklog 09-14.

이름·한글을 모델에 쓰게 하지 않는 이유: 한글이 깨지고, 글꼴이 사용자마다 달라지고, 길이를 못 맞춘다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

LEFT_X = 252  # 원본 제목은 268 에서 시작했지만 사용자가 16px 왼쪽을 골랐다 (09-14, `_left_x_variants.png` 3번)
CAP_HEIGHT = 48
RIGHT_MARGIN = 14
MIN_CAP = 20
#: 여기까지 줄인 뒤에는 높이 대신 장평(가로 축소)을 쓴다. 순서는 사용자 결정(09-18):
#: 48→26 축소 → 장평 100%→70% → 26→20 축소(장평 70% 유지) → 그래도 넘치면 **그대로 그린다**.
CONDENSE_CAP = 26
CONDENSE_MIN = 0.70      # 장평 하한. 이보다 좁히면 글자가 뭉개진다
CONDENSE_CANVAS = 3      # 장평용 캔버스 배율 — 카드 폭에 그리면 줄이기 전에 오른쪽이 잘린다 (09-18 실측)

Edge = tuple[tuple[int, int], tuple[int, int]]


@dataclass(frozen=True)
class Plate:
    """한 달 틀의 검은 제목판 기하. 달마다 판의 높이·위치·오른쪽 경계가 달라서(09-14 실측) 틀별로 잰다.

    - `center_y`: 틀에서 잰 판의 세로 중심. 대문자 띠의 잉크 중심을 여기에 맞춘다 (사용자 09-14: 판 중심 정렬).
    - `edge`: 배지와 맞닿는 기울어진 오른쪽 경계 — (y, x) 두 점. 글자가 **잉크 맨 아래 높이**에서 이 선
      (여백 14)을 넘으면 줄인다 (`_fits`).
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


def _size_for_cap(path: Path, cap: float) -> float:
    """대문자 높이 `cap` 을 내는 글꼴 크기. Pillow 12.3 은 소수 크기를 받으므로 반올림하지 않는다 —
    1px 계단으로 고르면 판에 들어가는 가장 큰 크기를 넘겨 버린다."""
    _, t, _, b = _font(path, 100).getbbox("H")
    return max(8.0, 100 * cap / (b - t))


@dataclass
class _Run:
    text: str
    font: ImageFont.FreeTypeFont
    x: int
    y: int
    ratio: float = 1.0   # 장평(가로 배율). 1.0 이면 그대로 그린다


def _plate_top(card: Image.Image, x: int, expect: int) -> int | None:
    """열 x 에서 검은 판의 윗선 y. `expect` 주변 ±PLATE_MAX_SHIFT 만 본다."""
    px = card.load()
    lo, hi = max(0, expect - PLATE_MAX_SHIFT), min(card.height - PLATE_MIN_RUN, expect + PLATE_MAX_SHIFT)
    for y in range(lo, hi + 1):
        if all(max(px[x, yy][:3]) < PLATE_DARK for yy in range(y, y + PLATE_MIN_RUN)):
            return y
    return None


def plate_shift(card: Image.Image, plate: Plate = APRIL_PLATE) -> int | None:
    """모델 출력의 제목판이 틀보다 몇 px 위(-)·아래(+)로 그려졌는지. **못 재면 None** — 비교 판정에서
    "딱 맞음(0)"과 "판을 못 찾음"을 가르기 위해서다. 제목을 그릴 때는 `_plate_shift` 가 None 을 0 으로 쓴다."""
    rgb = card.convert("RGB")
    tops = [t for x in PLATE_PROBE_XS if (t := _plate_top(rgb, x, plate.top_y)) is not None]
    if len(tops) < 2:
        return None
    tops.sort()
    return tops[len(tops) // 2] - plate.top_y


def _plate_shift(card: Image.Image, plate: Plate) -> int:
    """모델이 그린 판이 틀보다 얼마나 위·아래로 어긋났는지(px). 못 재면 0 (제목은 틀에서 잰 자리에 그린다)."""
    shift = plate_shift(card, plate)
    return 0 if shift is None else shift


def _fits(text: str, font: ImageFont.FreeTypeFont, plate: Plate, center_y: float,
          ratio: float = 1.0) -> tuple[bool, float, float]:
    """(들어가나, 잉크 오른끝, 그 높이의 한계) — 사선은 **잉크 맨 아래**에서 재고 여백은 RIGHT_MARGIN 하나뿐이다.

    09-14 에는 `baseline + 10` 에서 쟀는데, 그 10 은 사선이 완만한 4월에서 고른 값이라
    사선이 가파른 달(1·3·5월·딸기)에서는 필요 없이 더 깎았다 (09-18).
    잉크 중심을 판 중심에 맞추므로 잉크 맨 아래는 대문자 높이가 아니라 글자 잉크 높이로 정해진다."""
    _, t, r, b = font.getbbox(text)
    bottom = center_y + (b - t) / 2 + STROKE_W
    right = plate.left_x + (r + STROKE_W) * ratio
    limit = _plate_right_edge(bottom, plate.edge) - RIGHT_MARGIN
    return right <= limit, right, limit


def _largest(lo: float, hi: float, ok: Callable[[float], bool]) -> float:
    """`ok` 가 참인 가장 큰 값. `ok(lo)` 는 참, `ok(hi)` 는 거짓이라고 본다.

    0.01 까지 좁힌 뒤 0.1 로 반올림하되, 반올림이 경계를 넘으면 한 칸 내린다 — 0.1 에서 바로 멈추면
    옛 코드가 고르던 정수(예: 5월 `HOME TEAM 보리` 의 35)를 0.1 차이로 놓친다."""
    while hi - lo > 0.01:
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    cap = round(lo, 1)
    return cap if ok(cap) else round(lo - 0.05, 1)


def _choose(text: str, font_path: Path, plate: Plate, center_y: float) -> tuple[float, float]:
    """(대문자 높이, 장평). 순서는 사용자 결정(09-18) 그대로 — CONDENSE_CAP 주석 참고."""
    def fits(cap: float, ratio: float) -> bool:
        return _fits(text, _font(font_path, _size_for_cap(font_path, cap)), plate, center_y, ratio)[0]

    if fits(CAP_HEIGHT, 1.0):                                   # ① 그대로 들어간다
        return CAP_HEIGHT, 1.0
    if fits(CONDENSE_CAP, 1.0):
        return _largest(CONDENSE_CAP, CAP_HEIGHT, lambda c: fits(c, 1.0)), 1.0
    font = _font(font_path, _size_for_cap(font_path, CONDENSE_CAP))
    _, right, limit = _fits(text, font, plate, center_y)         # ② 26 에서도 넘치면 장평으로
    ratio = (limit - plate.left_x) / (right - plate.left_x)      # 한계는 장평과 무관하니 바로 나온다
    if ratio >= CONDENSE_MIN:
        return CONDENSE_CAP, ratio
    if fits(MIN_CAP, CONDENSE_MIN):                             # ③ 장평 70% 를 두고 20 까지 더 줄인다
        return _largest(MIN_CAP, CONDENSE_CAP, lambda c: fits(c, CONDENSE_MIN)), CONDENSE_MIN
    return MIN_CAP, CONDENSE_MIN                                # ④ 그래도 넘치면 그대로 그린다


def _layout(text: str, font_path: Path, plate: Plate, center_y: int) -> list[_Run]:
    cap, ratio = _choose(text, font_path, plate, center_y)
    f = _font(font_path, _size_for_cap(font_path, cap))
    _, t, _, b = f.getbbox(text)
    return [_Run(text, f, plate.left_x, round(center_y - (t + b) / 2), ratio)]   # 잉크 중심을 판 중심에


def _paint(layer: Image.Image, runs: list[_Run]) -> None:
    """RGBA `layer` 에 그림자·외곽선·그라데이션을 얹는다. 카드에 바로 그릴 수도, 투명 캔버스에
    글자만 그려 두고(장평) 나중에 합성할 수도 있게 나눠 뒀다."""
    W, H = layer.size
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for r in runs:
        sd.text((r.x, r.y + SHADOW_DY), r.text, font=r.font, fill=SHADOW, stroke_width=STROKE_W, stroke_fill=SHADOW)
    layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(2)))
    bd = ImageDraw.Draw(layer)
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
        layer.paste(grad, (0, 0), mask)


def _condensed_layer(runs: list[_Run], size: tuple[int, int], left_x: int) -> Image.Image:
    """장평을 먹인 글자만 있는 투명 레이어. **폭 ×CONDENSE_CANVAS 캔버스에 그린 뒤** `left_x` 를
    기준으로 가로만 LANCZOS 로 줄인다 — 카드 폭 캔버스에 그리면 줄이기 전에 오른쪽이 잘린다(09-18 실측)."""
    W, H = size
    wide = Image.new("RGBA", (W * CONDENSE_CANVAS, H), (0, 0, 0, 0))
    _paint(wide, runs)
    strip = wide.crop((left_x, 0, wide.width, H))
    strip = strip.resize((max(1, round(strip.width * runs[0].ratio)), H), Image.LANCZOS)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.paste(strip, (left_x, 0))   # 카드 밖으로 나가는 부분은 paste 가 잘라 준다
    return layer


def draw_title(card: Image.Image, text: str, font_path: Path, *, plate: Plate = APRIL_PLATE) -> Image.Image:
    """`plate` 는 그 달 틀의 제목판 기하(catalog.MonthCard.plate). 기본값은 4월.

    판 중심은 틀에서 잰 값에 **이 카드에서 다시 잰 윗선의 차이**를 더한 것이다 — 모델 출력은
    틀과 몇 px 어긋난다. 틀 자체에 얹으면 차이가 0 이라 잰 값 그대로다."""
    rgb = card.convert("RGB")
    center_y = plate.center_y + _plate_shift(rgb, plate)
    runs = _layout(text, font_path, plate, center_y)
    base = card.convert("RGBA")
    if runs[0].ratio < 1.0:
        base.alpha_composite(_condensed_layer(runs, base.size, plate.left_x))
    else:
        _paint(base, runs)
    return base.convert("RGB")
