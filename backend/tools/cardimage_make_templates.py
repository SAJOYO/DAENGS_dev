"""카드 12장의 글자 없는 틀을 두 단계로 이어서 만든다 (#496, docs/cardimage/).

`cardimage/headers.json`(카드별 제목·배지·부제, `cardimage_read_headers.py` 산출)을 읽어 카드마다
  blank → (2K 출력의 검은 띠 제거) → badge
를 돌리고, 결과를 `cardimage/out/templates/<stem>_blank.png`, `<stem>_badge.png` 로 모은다.
비교표 `cardimage/out/templates/_sheet.png` 에 원본·blank·badge 머리띠를 나란히 놓는다.
사람이 확인한 뒤 채택된 것을 `cardimage/<stem>_template.webp` 로 옮겨 커밋한다.

배지 코드는 `26` + 월 약자 (사용자 09-14: 이름 없이, 연도 고정).
카드마다 2K 두 장 ≈ $0.20. 4월(`4_blossom`)은 이미 채택된 틀이 있어 기본으로 건너뛴다.

실행 (backend/ 에서):
  uv run --with pillow python tools/cardimage_make_templates.py            # 4월 제외 11장
  uv run --with pillow python tools/cardimage_make_templates.py --only 7_beach 9_harvest_moon
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from cardimage_blank_title import make
from cardimage_try import CARDIMAGE
from PIL import Image, ImageDraw, ImageFont

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
OUT = CARDIMAGE / "out" / "templates"


def month_of(stem: str) -> int:
    return int(re.match(r"(\d+)_", stem).group(1))


def build_sheet(rows: list[tuple[str, list[Path]]]) -> Path:
    box = (0, 40, 994, 240)
    scale = 0.8
    w, h = round(994 * scale), round(200 * scale)
    lbl = ImageFont.truetype("C:/Windows/Fonts/malgunbd.ttf", 16)
    sheet = Image.new("RGB", (w * 3 + 20, (h + 24) * len(rows)), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for i, (stem, paths) in enumerate(rows):
        y = i * (h + 24)
        d.text((6, y + 2), f"{stem}   원본 / blank / badge", font=lbl, fill=(255, 255, 255))
        for j, p in enumerate(paths):
            if p and p.exists():
                sheet.paste(Image.open(p).convert("RGB").crop(box).resize((w, h)), (j * (w + 10), y + 22))
    p = OUT / "_sheet.png"
    sheet.save(p)
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None, help="이 stem 들만 (예: 7_beach)")
    ap.add_argument("--skip", nargs="*", default=["4_blossom"], help="건너뛸 stem (기본: 채택된 4월)")
    ap.add_argument("--sheet-only", action="store_true", help="API 없이 비교표만 다시 만든다")
    args = ap.parse_args()

    headers = json.loads((CARDIMAGE / "headers.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, list[Path]]] = []
    for fname, h in sorted(headers.items(), key=lambda kv: month_of(Path(kv[0]).stem)):
        stem = Path(fname).stem
        if args.only and stem not in args.only:
            continue
        if not args.only and stem in args.skip:
            continue
        new_badge = f"26{MONTHS[month_of(stem) - 1]}"
        blank_p, badge_p = OUT / f"{stem}_blank.png", OUT / f"{stem}_badge.png"
        if not args.sheet_only:
            print(f"== {stem}: {h['title']} / {h['badge']} → {new_badge} / {h['subtitle']}")
            f1, raw1 = make(CARDIMAGE / fname, "blank", title=h["title"], badge=h["badge"], new_badge=new_badge,
                            subtitle=h["subtitle"], out=OUT, tag=stem)
            Image.open(f1).save(blank_p)
            f2, raw2 = make(raw1, "badge", title=h["title"], badge=h["badge"], new_badge=new_badge,
                            subtitle=h["subtitle"], out=OUT, tag=stem)
            Image.open(f2).save(badge_p)
            Image.open(raw2).save(OUT / f"{stem}_badge_raw2k.png")
            print(f"   blank → {blank_p.name}, badge → {badge_p.name}")
        rows.append((stem, [CARDIMAGE / fname, blank_p, badge_p]))
    print(build_sheet(rows))


if __name__ == "__main__":
    main()
