"""도감 카드에서 제목·배지 글자만 지운 "글자 없는 틀" 만들기 — 카드마다 한 번 (#496, docs/cardimage/).

두 단계 (4월에서 검증된 순서 — 한 번에 다 시키면 도형 배치만 실패한다, worklog 09-14):
  blank  원본 카드에서 제목판의 글자만 지우고(검은 띠는 유지) 배지 `NEO-APR25` 를 `26APR` 로 바꾼다
  badge  blank 의 2K 출력(검은 띠 제거)을 입력으로, 배지 글자를 부제 글자 높이로 줄이고 배지를 좁힌 뒤
         제목판을 그만큼 오른쪽으로 늘린다 (이름 칸 확보)
강아지·아바타·부제·아래판 문구·테두리는 그대로. 결과는 `cardimage/out/` 에 저장하고, 사람이 확인한 뒤
`cardimage/N_<이름>_template.webp` 로 옮겨 커밋한다 (`cardimage_make_templates.py` 가 12장을 이어서 돌린다).

이렇게 만든 틀 위에서 사용자 카드는 ① 강아지 교체(AI) ② 제목 글자 얹기(Pillow, `cardimage_title.py`) 순서다.

실행 (backend/ 에서):
  uv run --with pillow python tools/cardimage_blank_title.py --step blank --card ../cardimage/7_beach.webp \
      --title "BEACH NEO" --badge "NEO-JUL24" --new-badge 26JUL --subtitle "JULY SPECIAL"
  uv run --with pillow python tools/cardimage_blank_title.py --step badge --card <blank 의 2K 잘라낸 png> ...

⚠ 호출마다 돈이 나갑니다 — 2K 한 장 약 $0.10.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from PIL import Image

from cardimage_try import CARDIMAGE, ROOT, gemini_edit, pad_to_2_3, read_env_key

PROMPT_BLANK = """Image 1 is a collectible trading card. Image 2 is the same card again (ignore it).

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

CARD_SIZE = (994, 1582)


def crop_bars_from_raw(raw: Image.Image, src_w: int, src_h: int) -> Image.Image:
    """pad_to_2_3 로 붙인 좌우 검은 띠를 모델 출력(raw)에서 잘라낸다."""
    target_w = round(src_h * 2 / 3)
    pad = max(0, (target_w - src_w) // 2)
    padded_w = src_w + 2 * pad
    s = raw.width / padded_w
    return raw.crop((round(pad * s), 0, round((pad + src_w) * s), raw.height))


def make(card_path: Path, step: str, *, title: str, badge: str, new_badge: str, subtitle: str,
         model: str = "gemini-3.1-flash-image", size: str = "2K", out: Path = CARDIMAGE / "out",
         tag: str | None = None) -> tuple[Path, Path]:
    """한 단계 실행. (최종 994×1582 png 경로, 검은 띠를 잘라낸 2K raw png 경로) 를 돌려준다."""
    card = Image.open(card_path).convert("RGB")
    out.mkdir(parents=True, exist_ok=True)
    tmpl = PROMPT_BLANK if step == "blank" else PROMPT_BADGE
    prompt = tmpl.format(title=title, badge=badge, new_badge=new_badge, subtitle=subtitle)
    source, pad = pad_to_2_3(card)
    api_key = read_env_key()

    gen, note = gemini_edit(model, prompt, source, source, size, api_key)
    final = gen.convert("RGB").resize(source.size, Image.LANCZOS).crop((pad, 0, pad + card.width, card.height))
    if final.size != CARD_SIZE:
        final = final.resize(CARD_SIZE, Image.LANCZOS)
    raw2k = crop_bars_from_raw(gen.convert("RGB"), card.width, card.height)

    stamp = dt.datetime.now().strftime("%m%d_%H%M%S")
    name = f"{stamp}_{tag or card_path.stem}_{step}_{size}"
    final_p, raw_p = out / f"{name}.png", out / f"{name}_raw2k.png"
    final.save(final_p)
    raw2k.save(raw_p)
    (out / f"{name}.json").write_text(
        json.dumps({"model": model, "size": size, "step": step, "card": str(card_path), "title": title,
                    "badge": badge, "new_badge": new_badge, "subtitle": subtitle, "prompt": prompt,
                    "model_text": note, "generated_size": gen.size, "raw2k_size": raw2k.size},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return final_p, raw_p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--card", type=Path, default=CARDIMAGE / "4_blossom.webp")
    ap.add_argument("--step", choices=["blank", "badge"], default="blank")
    ap.add_argument("--title", default="BLOSSOM NEO")
    ap.add_argument("--badge", default="NEO-APR25", help="지금 배지에 적힌 코드")
    ap.add_argument("--new-badge", default="26APR", help="바꿔 넣을 코드")
    ap.add_argument("--subtitle", default="APRIL SPECIAL")
    ap.add_argument("--model", default="gemini-3.1-flash-image")
    ap.add_argument("--size", default="2K")
    ap.add_argument("--out", type=Path, default=CARDIMAGE / "out")
    args = ap.parse_args()
    final_p, raw_p = make(args.card.resolve(), args.step, title=args.title, badge=args.badge, new_badge=args.new_badge,
                          subtitle=args.subtitle, model=args.model, size=args.size, out=args.out)
    print(f"저장: {final_p}\nraw2k: {raw_p}")


if __name__ == "__main__":
    main()
