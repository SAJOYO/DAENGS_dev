"""도감 카드에서 제목·배지 글자만 지운 "글자 없는 틀" 만들기 — 카드마다 한 번 (#496, docs/cardimage/).

제목판은 `BLOSSOM NEO` **글자만** 지우고 검은 배경 띠는 남깁니다. 오른쪽 위 배지는 `NEO-APR25` 를
`26APR` 로 바꾸되 글자 크기는 그대로, 짧아진 만큼 배지를 줄이고 제목판을 그만큼 오른쪽으로 늘립니다
(이름 칸 확보 — 사용자 09-14). 강아지·아바타·`APRIL SPECIAL`·아래판 문구·테두리는 그대로.
결과는 `cardimage/out/` 에 저장하고, 사람이 확인한 뒤 `cardimage/4_blossom_template.webp` 로 옮겨 커밋합니다.

이렇게 만든 틀 위에서 사용자 카드는 ① 강아지 교체(AI) ② 제목·배지 글자 얹기(Pillow, AI 없음)
순서로 만듭니다 — 이름·한글은 모델에게 쓰게 하지 않습니다 (worklog 09-14).

실행 (backend/ 에서):
  uv run --with pillow python tools/cardimage_blank_title.py            # 4월 카드, 2K 한 장
  uv run --with pillow python tools/cardimage_blank_title.py --card ../cardimage/7_beach.webp \
      --title "BEACH NEO" --badge "NEO-JUL24"

⚠ 호출마다 돈이 나갑니다 — 2K 한 장 약 $0.10.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from PIL import Image

from cardimage_try import CARDIMAGE, ROOT, gemini_edit, pad_to_2_3, read_env_key

PROMPT_TMPL = """Image 1 is a collectible trading card. Image 2 is the same card again (ignore it).

Make exactly two edits to the top header of image 1:

1. Title plate: erase only the letters of the title "{title}". Keep the title plate itself exactly as it is — its dark, near-black gradient background band, its metallic edges and its holographic sheen must remain. The result is an empty dark plate with no text.

2. Badge at the top right: replace the code "{badge}" with the code "{new_badge}", using the same font, the same letter size, the same color and the same style. Because the new code is shorter, shrink the badge horizontally so it fits the new code with the same margins as before, and extend the dark title plate to the right to fill the space that the badge gave up. The boundary between the plate and the badge keeps the same slanted shape.

Change absolutely nothing else. The dog, the circular portrait at the top left, the text "{subtitle}", the entire illustration, the bottom panel with all its text, stars and icons, and the holographic border must stay pixel-identical. Do not add any other text or marks. Output only the edited card."""


PROMPT_BADGE = """Image 1 is a collectible trading card whose title plate is already empty. Image 2 is the same card again (ignore it).

Make exactly one edit to the header at the top of image 1: fix the badge at the top right that reads "{new_badge}".

- Its letters are currently too large. Make the letters of "{new_badge}" the same height as the letters of "{subtitle}" on the purple strip just below — same font, same weight, same color and style as now, only smaller.
- Shrink the badge horizontally so that it wraps the smaller text with the same small margins it has now.
- Move the slanted boundary between the dark title plate and the badge to the right by the amount the badge got narrower, and extend the dark title plate to fill that space, so the empty dark plate becomes wider. Keep the slant angle.

Change absolutely nothing else. The dog, the circular portrait at the top left, the text "{subtitle}", the entire illustration, the bottom panel with all its text, stars and icons, and the holographic border must stay pixel-identical. Do not add any text to the title plate. Output only the edited card."""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--card", type=Path, default=CARDIMAGE / "4_blossom.webp")
    ap.add_argument("--title", default="BLOSSOM NEO")
    ap.add_argument("--badge", default="NEO-APR25", help="지금 배지에 적힌 코드")
    ap.add_argument("--new-badge", default="26APR", help="바꿔 넣을 코드 (사용자 09-14: 이름 없이 월만, 크기는 그대로)")
    ap.add_argument("--subtitle", default="APRIL SPECIAL")
    ap.add_argument("--model", default="gemini-3.1-flash-image")
    ap.add_argument("--size", default="2K")
    ap.add_argument("-n", type=int, default=1)
    ap.add_argument("--out", type=Path, default=CARDIMAGE / "out")
    ap.add_argument("--step", choices=["blank", "badge"], default="blank",
                    help="blank = 원본 카드에서 제목 지우고 배지 바꾸기 / badge = 이미 비운 판에서 배지 글자 줄이고 제목판 늘리기")
    ap.add_argument("--final-size", type=int, nargs=2, default=(994, 1582), metavar=("W", "H"),
                    help="최종 저장 크기. 입력이 2K raw 잘라낸 것이어도 결과는 카드 크기로 맞춘다")
    args = ap.parse_args()

    card_path = args.card.resolve()
    card = Image.open(card_path).convert("RGB")
    args.out.mkdir(parents=True, exist_ok=True)
    tmpl = PROMPT_TMPL if args.step == "blank" else PROMPT_BADGE
    prompt = tmpl.format(title=args.title, badge=args.badge, new_badge=args.new_badge, subtitle=args.subtitle)
    source, pad = pad_to_2_3(card)
    api_key = read_env_key()

    stamp = dt.datetime.now().strftime("%m%d_%H%M%S")
    for i in range(args.n):
        # gemini_edit 는 (원본, 사진) 두 장을 기대한다 — 여기서는 사진 자리에 카드를 한 번 더 넣는다.
        gen, note = gemini_edit(args.model, prompt, source, source, args.size, api_key)
        final = gen.convert("RGB").resize(source.size, Image.LANCZOS).crop((pad, 0, pad + card.width, card.height))
        if tuple(final.size) != tuple(args.final_size):
            final = final.resize(tuple(args.final_size), Image.LANCZOS)
        name = f"{stamp}_{card_path.stem}_{args.step}_{args.size}_{i + 1}"
        final.save(args.out / f"{name}.png")
        gen.save(args.out / f"{name}_raw.png")
        (args.out / f"{name}.json").write_text(
            json.dumps(
                {"model": args.model, "size": args.size, "card": str(card_path.relative_to(ROOT)),
                 "title": args.title, "badge": args.badge, "new_badge": args.new_badge, "prompt": prompt, "model_text": note,
                 "generated_size": gen.size},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"저장: {args.out / name}.png  (생성 {gen.size})")


if __name__ == "__main__":
    main()
