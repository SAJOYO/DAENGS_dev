"""채택한 틀 위에 제목 `BLOSSOM <이름>` 을 Pillow 로 얹기 — AI 없음, 무료 (#496, docs/cardimage/).

이름·한글은 모델에게 쓰게 하지 않는다(사용자 결정 09-14). 글꼴 하나를 고정하고, 이름이 길면
제목판 폭에 맞춰 글자를 줄인다. 배지 `26APR` 은 틀에 구워져 있어 여기서는 건드리지 않는다.

실행 (backend/ 에서):
  uv run --with pillow python tools/cardimage_title.py "BLOSSOM NEO" "BLOSSOM 네오" "BLOSSOM neeeeeeo"
  uv run --with pillow python tools/cardimage_title.py --font "C:/Windows/Fonts/malgunbd.ttf" "BLOSSOM 몽실이"

글꼴: 기본은 윈도우 맑은고딕 Bold 다(시안용). 서비스에서는 배포 가능한 한글 글꼴(Pretendard·Noto Sans KR)
파일을 저장소에 넣고 그 경로를 쓴다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
CARDIMAGE = ROOT / "cardimage"

# 채택한 틀(994×1582)의 제목판 안쪽. 검은 띠는 x≈250~790, y≈65~150 (09-14 잰 값).
TITLE_BOX = (262, 70, 780, 148)
TITLE_MAX_PT = 64
TITLE_MIN_PT = 24
TEXT_FILL = (255, 255, 255)
STROKE_FILL = (18, 18, 28)
STROKE_W = 3


def fit_font(font_path: str, text: str, box_w: int, box_h: int) -> ImageFont.FreeTypeFont:
    """폭·높이에 들어가는 가장 큰 크기. 2pt 씩 줄인다."""
    size = TITLE_MAX_PT
    while size > TITLE_MIN_PT:
        f = ImageFont.truetype(font_path, size)
        l, t, r, b = f.getbbox(text, stroke_width=STROKE_W)
        if r - l <= box_w and b - t <= box_h:
            return f
        size -= 2
    return ImageFont.truetype(font_path, TITLE_MIN_PT)


def draw_title(card: Image.Image, text: str, font_path: str) -> tuple[Image.Image, int]:
    out = card.copy()
    d = ImageDraw.Draw(out)
    l, t, r, b = TITLE_BOX
    f = fit_font(font_path, text, r - l, b - t)
    tl, tt, tr, tb = f.getbbox(text, stroke_width=STROKE_W)
    x = l + ((r - l) - (tr - tl)) // 2 - tl
    y = t + ((b - t) - (tb - tt)) // 2 - tt
    d.text((x, y), text, font=f, fill=TEXT_FILL, stroke_width=STROKE_W, stroke_fill=STROKE_FILL)
    return out, f.size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("titles", nargs="+", help='얹을 제목들. 예: "BLOSSOM 네오"')
    ap.add_argument("--template", type=Path, default=CARDIMAGE / "4_blossom_template.webp")
    ap.add_argument("--font", default="C:/Windows/Fonts/malgunbd.ttf")
    ap.add_argument("--out", type=Path, default=CARDIMAGE / "out" / "_title_test")
    args = ap.parse_args()

    card = Image.open(args.template).convert("RGB")
    args.out.mkdir(parents=True, exist_ok=True)
    for title in args.titles:
        im, pt = draw_title(card, title, args.font)
        safe = "".join(c if c.isalnum() else "_" for c in title)
        p = args.out / f"{safe}.png"
        im.save(p)
        print(f"{p}  ({pt}pt)")


if __name__ == "__main__":
    main()
