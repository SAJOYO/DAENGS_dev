"""채택한 틀 위에 제목 `BLOSSOM <이름>` 을 Pillow 로 얹기 — AI 없음, 무료 (#496, docs/cardimage/).

이름·한글은 모델에게 쓰게 하지 않는다(사용자 결정 09-14). 원본 카드의 제목을 픽셀로 잰 값에 맞춘다
(09-14, `4_blossom.webp` 994×1582 기준):
  - 왼쪽 정렬, 글자 시작 x = 268
  - 대문자 띠 y = 87~134 (높이 48). 기준선(baseline) = 134
  - 검은 제목판의 오른쪽 경계는 기울어져 있다: y=65 에서 x=791, y=135 에서 x=758 (배지 경계)
  - 원본 `BLOSSOM NEO` 폭 416px. 굵은 세리프, 은색 그라데이션 채움 + 어두운 외곽선 + 아래 그림자
이름이 길어 오른쪽 경계를 넘으면 대문자 높이를 1px 씩 줄인다.

글꼴: **Noto Serif KR 가변(Black) 하나로 영문·한글 모두** — `cardimage/fonts/NotoSerifKR.ttf` (OFL, 배포 가능,
사용자 선택 09-14). 나중에 다른 글꼴(무료·구매)로 바꾸려면 그 파일을 같은 자리에 두거나 `--font-kr` 로 주면 된다.
영문만 다른 글꼴로 그리고 싶으면 `--font-latin` (Noto Serif Display 등, 글자 단위로 글꼴을 바꾸되 기준선은 같다).

실행 (backend/ 에서):
  uv run --with pillow python tools/cardimage_title.py "BLOSSOM NEO" "BLOSSOM 네오" "BLOSSOM neeeeeeo"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
CARDIMAGE = ROOT / "cardimage"

# --- 원본에서 잰 제목 자리 (994×1582) ------------------------------------------
LEFT_X = 268
# 세로: **검은 제목판의 중심**에 대문자 띠의 중심을 맞춘다 — 사용자 09-14 "검은 칸 중앙과 글씨 중앙이 같게".
# 판은 y 53~145(중심 99). 대문자 48px 를 중심 99 에 두면 75~123 → 기준선 123.
# (원본 제목 띠 중심은 110.5, 배지 `26APR` 글자 중심은 106.5 였다 — 둘 다 판 중심보다 아래라 폐기.)
BASELINE_Y = 123
CAP_HEIGHT = 48
RIGHT_MARGIN = 14
_EDGE_Y0, _EDGE_X0, _EDGE_Y1, _EDGE_X1 = 65, 791, 135, 758  # 검은 판 오른쪽 경계
MIN_CAP = 20

LATIN_AXES = [900, 62]  # Noto Serif Display: [Weight, Width]
KR_WEIGHT = "Black"

FILL_TOP = (252, 252, 252)
FILL_BOTTOM = (200, 202, 208)
STROKE = (22, 22, 30)
STROKE_W = 3
SHADOW = (0, 0, 0, 150)
SHADOW_DY = 3


def plate_right_edge(y: float) -> float:
    t = (y - _EDGE_Y0) / (_EDGE_Y1 - _EDGE_Y0)
    return _EDGE_X0 + t * (_EDGE_X1 - _EDGE_X0)


def is_hangul(ch: str) -> bool:
    o = ord(ch)
    return 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F


@dataclass
class Fonts:
    kr_path: str
    latin_path: str | None = None  # 없으면 영문도 KR 글꼴로 (사용자 09-14: KR Black 통일)

    def latin(self, size: int) -> ImageFont.FreeTypeFont:
        if not self.latin_path:
            return self.kr(size)
        f = ImageFont.truetype(self.latin_path, size)
        f.set_variation_by_axes(LATIN_AXES)
        return f

    def kr(self, size: int) -> ImageFont.FreeTypeFont:
        f = ImageFont.truetype(self.kr_path, size)
        f.set_variation_by_name(KR_WEIGHT)
        return f

    def size_for_cap(self, which: str, cap: int) -> int:
        f = self.latin(100) if which == "latin" else self.kr(100)
        _, t, _, b = f.getbbox("H")
        return max(8, round(100 * cap / (b - t)))


@dataclass
class Run:
    text: str
    font: ImageFont.FreeTypeFont
    x: int
    y: int  # draw.text 의 y (기준선 - ascent)


def split_runs(text: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    for ch in text:
        kind = "kr" if is_hangul(ch) else "latin"
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + ch)
        else:
            runs.append((kind, ch))
    return runs


PLATE_CENTER_Y = 99  # 검은 제목판 y 53~145 의 중심


def layout(text: str, fonts: Fonts) -> tuple[list[Run], int]:
    """대문자 높이를 줄여 가며 오른쪽 경계 안에 들어가는 배치를 찾고, 글자 덩어리(잉크)의 세로 중심을
    판 중심에 맞춘다. (runs, cap)

    대문자 띠가 아니라 잉크 기준인 이유: 소문자만 있는 이름은 대문자 띠의 아래 절반에만 글자가 있어
    띠 기준으로 맞추면 처져 보인다 (사용자 09-14 "neeeeeeo 가 아래로 처진다").
    """
    cap = CAP_HEIGHT
    while True:
        runs: list[Run] = []
        x = LEFT_X
        ink_top, ink_bot = 10**9, -(10**9)
        for kind, s in split_runs(text):
            f = fonts.latin(fonts.size_for_cap("latin", cap)) if kind == "latin" else fonts.kr(fonts.size_for_cap("kr", cap))
            ascent, _ = f.getmetrics()
            y = BASELINE_Y - ascent
            _, t, _, b = f.getbbox(s)
            ink_top, ink_bot = min(ink_top, y + t), max(ink_bot, y + b)
            runs.append(Run(s, f, x, y))
            x += round(f.getlength(s))
        right = x + STROKE_W
        limit = plate_right_edge(BASELINE_Y + 10) - RIGHT_MARGIN
        if right <= limit or cap <= MIN_CAP:
            shift = round(PLATE_CENTER_Y - (ink_top + ink_bot) / 2)
            for r in runs:
                r.y += shift
            return runs, cap
        cap -= 1


def draw_title(card: Image.Image, text: str, fonts: Fonts) -> tuple[Image.Image, int]:
    runs, cap = layout(text, fonts)
    base = card.convert("RGBA")
    W, H = base.size

    # 1) 그림자
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for r in runs:
        sd.text((r.x, r.y + SHADOW_DY), r.text, font=r.font, fill=SHADOW, stroke_width=STROKE_W, stroke_fill=SHADOW)
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(2)))

    # 2) 외곽선
    bd = ImageDraw.Draw(base)
    for r in runs:
        bd.text((r.x, r.y), r.text, font=r.font, fill=STROKE + (255,), stroke_width=STROKE_W, stroke_fill=STROKE + (255,))

    # 3) 은색 세로 그라데이션 채움 (글자 마스크)
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
    return base.convert("RGB"), cap


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("titles", nargs="+", help='얹을 제목들. 예: "BLOSSOM 네오"')
    ap.add_argument("--template", type=Path, default=CARDIMAGE / "4_blossom_template.webp")
    ap.add_argument("--font-kr", type=Path, default=CARDIMAGE / "fonts" / "NotoSerifKR.ttf", help="Noto Serif KR 가변 글꼴 (기본: 저장소의 것)")
    ap.add_argument("--font-latin", default=None, help="영문만 다른 글꼴로 그리려면 (Noto Serif Display 등). 기본은 KR 로 통일")
    ap.add_argument("--out", type=Path, default=CARDIMAGE / "out" / "_title_test")
    ap.add_argument("--compare", type=Path, default=CARDIMAGE / "4_blossom.webp", help="머리띠 비교표에 넣을 원본")
    ap.add_argument("--keep-case", action="store_true", help="영문 이름을 대문자로 바꾸지 않는다 (기본은 카드 양식대로 대문자)")
    args = ap.parse_args()
    if not args.keep_case:
        args.titles = [t.upper() for t in args.titles]  # 한글은 upper() 에 영향 없음

    fonts = Fonts(str(args.font_kr), args.font_latin)
    card = Image.open(args.template).convert("RGB")
    args.out.mkdir(parents=True, exist_ok=True)
    rendered: list[Image.Image] = []
    for title in args.titles:
        im, cap = draw_title(card, title, fonts)
        safe = "".join(c if c.isalnum() else "_" for c in title)
        p = args.out / f"{safe}.png"
        im.save(p)
        rendered.append(im)
        print(f"{p}  (대문자 높이 {cap}px)")

    # 머리띠만 2배로 잘라 원본과 나란히
    box = (230, 50, 960, 165)
    rows = [Image.open(args.compare).convert("RGB")] + rendered
    sheet = Image.new("RGB", (1460, 240 * len(rows)), (255, 0, 255))
    for i, im in enumerate(rows):
        sheet.paste(im.crop(box).resize((1460, 230)), (0, i * 240))
    sheet.save(args.out / "_header_compare.png")
    print(args.out / "_header_compare.png")


if __name__ == "__main__":
    main()
