"""비교 조건 폴더들을 한 장의 격자로 모은다 — 열 = 조건, 행 = 카드 이름 (#557).

    uv run python tools/cardgen_grid.py \
        --col 기준=../cardimage/out/_cardgen/task8-klein --col 문구=../cardimage/out/_cardgen/e1-panel \
        --col 2048=../cardimage/out/_cardgen/e1-2048 --col 둘다=../cardimage/out/_cardgen/e1-both \
        --names KakaoTalk_20260913_220514335_4_s1,KakaoTalk_20260913_220514335_03_4_s1 \
        --out ../cardimage/out/_cardgen/e1_grid_4.png

없는 파일 칸은 회색으로 둔다. 판정은 이 격자를 눈으로 본다(검수 점수만 믿지 않는다 — roadmap 2번 공통 규칙).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CARD_W, CARD_H = 994, 1582
LABEL_W = 220
HEADER_H = 60
MISSING = (128, 128, 128)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("malgun.ttf", "NotoSansKR-Regular.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def build_grid(columns: list[tuple[str, Path]], names: list[str], *, thumb_width: int = 360) -> Image.Image:
    thumb_h = round(CARD_H * thumb_width / CARD_W)
    grid = Image.new("RGB", (LABEL_W + thumb_width * len(columns), HEADER_H + thumb_h * len(names)), (255, 255, 255))
    draw = ImageDraw.Draw(grid)
    font = _font(28)
    small = _font(16)
    for c, (label, _) in enumerate(columns):
        draw.text((LABEL_W + c * thumb_width + 10, 14), label, fill=(0, 0, 0), font=font)
    for r, name in enumerate(names):
        top = HEADER_H + r * thumb_h
        draw.multiline_text((8, top + 10), name.replace("_", "\n"), fill=(0, 0, 0), font=small)
        for c, (_, folder) in enumerate(columns):
            path = folder / f"{name}.png"
            if path.exists():
                cell = Image.open(path).convert("RGB").resize((thumb_width, thumb_h), Image.LANCZOS)
            else:
                cell = Image.new("RGB", (thumb_width, thumb_h), MISSING)
            grid.paste(cell, (LABEL_W + c * thumb_width, top))
    return grid


def _col(text: str) -> tuple[str, Path]:
    label, sep, folder = text.partition("=")
    if not sep or not label or not folder:
        raise argparse.ArgumentTypeError(f"--col 은 라벨=폴더 형식입니다: {text!r}")
    return label, Path(folder)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--col", type=_col, action="append", required=True, help="라벨=폴더 (여러 번)")
    parser.add_argument("--names", required=True, help="쉼표로 구분한 카드 이름 (확장자 없이)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--thumb-width", type=int, default=360)
    args = parser.parse_args(argv)

    grid = build_grid(args.col, [n for n in args.names.split(",") if n], thumb_width=args.thumb_width)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    grid.save(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
