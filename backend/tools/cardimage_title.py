"""채택한 틀 위에 제목 `BLOSSOM <이름>` 을 얹어 실제 카드와 나란히 비교하는 실험 도구 (#496, docs/cardimage/).

그리는 로직·상수(잰 값)는 `daengs_cardimage.title` 로 옮겼다 — 여기는 CLI 로 그 함수를
부르고 원본과 나란히 놓는 머리띠 비교표를 만드는 것만 남는다. 값의 원본과 실험 근거는 그 모듈의
docstring 과 worklog 09-14 를 보라.

글꼴: **Noto Serif KR 가변(Black) 하나로 영문·한글 모두** — `cardimage/fonts/NotoSerifKR.ttf` (OFL, 배포 가능,
사용자 선택 09-14). 나중에 다른 글꼴(무료·구매)로 바꾸려면 그 파일을 같은 자리에 두거나 `--font-kr` 로 주면 된다.
영문만 다른 글꼴로 그리는 글자 단위 분기(옛 `split_runs`/`--font-latin`)는 서비스로 옮기며 없앴다 — 사용자가
KR Black 하나로 통일하기로 했다(09-14).

실행 (backend/ 에서):
  uv run python tools/cardimage_title.py "BLOSSOM NEO" "BLOSSOM 네오" "BLOSSOM neeeeeeo"
  uv run python tools/cardimage_title.py --month 9 "CHUSEOK 네오"     # 9월 틀 + 9월 제목판 기하
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from daengs_cardimage import catalog
from daengs_cardimage.title import draw_title, title_text

ROOT = Path(__file__).resolve().parents[2]
CARDIMAGE = ROOT / "cardimage"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("titles", nargs="+", help='얹을 제목들. 예: "BLOSSOM 네오"')
    ap.add_argument("--month", type=int, default=4, help="틀·원본·제목판 기하를 이 달의 catalog 값으로 (기본 4)")
    ap.add_argument("--template", type=Path, default=None, help="기본: 그 달의 cardimage/<stem>_template.webp")
    ap.add_argument("--font-kr", type=Path, default=CARDIMAGE / "fonts" / "NotoSerifKR.ttf", help="Noto Serif KR 가변 글꼴 (기본: 저장소의 것)")
    ap.add_argument("--out", type=Path, default=CARDIMAGE / "out" / "_title_test")
    ap.add_argument("--compare", type=Path, default=None, help="머리띠 비교표에 넣을 원본 (기본: 그 달의 cardimage/<stem>.webp)")
    ap.add_argument("--keep-case", action="store_true", help="영문 이름을 대문자로 바꾸지 않는다 (기본은 카드 양식대로 대문자)")
    args = ap.parse_args()
    if not args.keep_case:
        args.titles = [title_text("", t) for t in args.titles]  # 공백 정리 + 대문자화 (한글은 upper() 에 영향 없음)
    meta = catalog.get(args.month)
    template = args.template or catalog.template_path(args.month, CARDIMAGE)
    compare = args.compare or CARDIMAGE / f"{meta.stem}.webp"

    card = Image.open(template).convert("RGB")
    args.out.mkdir(parents=True, exist_ok=True)
    rendered: list[Image.Image] = []
    for text in args.titles:
        im = draw_title(card, text, args.font_kr, plate=meta.plate)
        safe = "".join(c if c.isalnum() else "_" for c in text)
        p = args.out / f"{safe}.png"
        im.save(p)
        rendered.append(im)
        print(p)

    # 머리띠만 2배로 잘라 원본과 나란히
    box = (230, 50, 960, 165)
    rows = [Image.open(compare).convert("RGB")] + rendered
    sheet = Image.new("RGB", (1460, 240 * len(rows)), (255, 0, 255))
    for i, im in enumerate(rows):
        sheet.paste(im.crop(box).resize((1460, 230)), (0, i * 240))
    sheet.save(args.out / "_header_compare.png")
    print(args.out / "_header_compare.png")


if __name__ == "__main__":
    main()
