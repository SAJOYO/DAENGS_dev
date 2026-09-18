"""딸기·상추 카드의 "제목 글자만 지운 5:8 틀" 시안 만들기 (#592, docs/cardimage/).

원본(`cardimage/raw/` 의 딸기·상추 카드, 비율 0.706·0.800)은 달력 카드(994×1582, 5:8)보다 넓다.
틀은 제목판의 글자만 지우고(판·금속 테두리·홀로는 유지, 오른쪽 위 배지는 글자 그대로) 5:8 로
만든다. 비율 차이는 **그림 창이 위아래로 길어지는 것**으로 메우고, 카드·머리띠·주인공·아래판은
늘이거나 누르지 않는다.

1회차 (`--round 1`, 카드마다 두 가지 = 4회 호출):
  v1  원본을 994 폭으로 줄여 994×1582 회색 캔버스 가운데에 놓고 → 2:3 검은 띠 → 한 장만 보낸다.
      결과: 모델이 회색을 그대로 두어 실패.
  v2  이미지 1 = 원본, 이미지 2 = `4_blossom_template.webp`(2:3 검은 띠) 를 **배치 참고로만** 보낸다.
      결과: 방식은 맞았으나 딸기는 카드가 2:3 전체로 그려지고, 상추는 별 개수가 바뀌었다.
  출력은 엔진의 `fit_to_card`(고정 폭 가운데 자르기)로 994×1582 를 만들었다.
  (원본은 2:3 보다 넓어 `pad_to_2_3` 이 띠를 붙이지 않는다 — v2 의 이미지 1 은 원본 그대로 간다.)

2회차 (`--round 2`, 카드마다 v2r2 한 번 = 2회 호출):
  v2 방식 + 별 구성 고정 + "이미지 2 의 카드와 같은 폭, 좌우 검은 여백 유지" 강화.
  출력은 고정 폭으로 자르지 않고 `detect_card_bbox` 로 실제 카드 경계(검은 여백 대비)를 찾아 자른 뒤
  994×1582 로 줄인다. 잘라낸 비율이 0.628 에서 3% 넘게 벗어나면 늘리지 않고 `NOT_ADOPTABLE` 로 남긴다.
  `--recrop lettuce_v2` 는 호출 없이 1회차 raw 에 같은 자르기를 적용한다.

채택 (사용자 09-18) — 둘 다 2회차 v2r2:
  딸기  `strawberry_v2r2_squeezed.png` → `cardimage/strawberry_template.webp`.
        카드 상자가 1691×2526(비율 0.669, +6.5%)이라 `NOT_ADOPTABLE` 이었는데, 그 상자를 가로로만
        ×0.939 눌러(비율 5:8) 994×1582 로 만든 판을 사용자가 골랐다. 이 누르기는 이 도구가 하지 않는다.
  상추  `lettuce_v2r2.png`(상자 1590×2528, +0.1%) → `cardimage/lettuce_template.webp`.
  두 장 모두 WebP q92(lossy, 달 틀과 같은 방식)로 옮겼다. 오른쪽 위 배지(`NEO-S0824`·`NEO-0824`)는
  달 틀처럼 `26<월>` 로 바꾸지 않고 **원본 글자 그대로 둔다** (사용자 결정).
  Nano Banana 2 호출은 1회차 4 + 2회차 2 = 6회, 약 $0.60 (2026-09-18).

결과는 `cardimage/out/templates_fruit/` — 이름별 `.png`, 모델 원본 `_raw2k.png`, 입력 `_inputN.png`,
프롬프트·모델·시각·잘라낸 상자 `.json`, 비교표 `_sheet.png`(1회차)·`_sheet_r2.png`(2회차).
이미 결과가 있는 조합은 다시 부르지 않는다.

⚠ 호출마다 돈이 나갑니다 — Nano Banana 2 2K 한 장 약 $0.10. 1회차 4회 ≈ $0.40, 2회차 2회 ≈ $0.20.
   돌리기 전에 사용자 승인을 받습니다. `--max-calls` 를 넘으면 멈춥니다.

실행 (backend/ 에서):
  PYTHONUTF8=1 uv run --no-sync python tools/cardimage_fruit_templates.py --round 2 --max-calls 2
  PYTHONUTF8=1 uv run --no-sync python tools/cardimage_fruit_templates.py --recrop lettuce_v2     # API 없음
  PYTHONUTF8=1 uv run --no-sync python tools/cardimage_fruit_templates.py --round 2 --sheet-only  # API 없음
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import traceback
from pathlib import Path

from cardimage_try import CARDIMAGE, pad_to_2_3, png_bytes, read_env_key
from PIL import Image, ImageDraw, ImageFont

from daengs_cardimage.engine import CARD_SIZE, fit_to_card

OUT = CARDIMAGE / "out" / "templates_fruit"
MODEL = "gemini-3.1-flash-image"
SIZE = "2K"
FILLER = (128, 128, 128)
LAYOUT_REF = CARDIMAGE / "4_blossom_template.webp"
CARD_RATIO = CARD_SIZE[0] / CARD_SIZE[1]
RATIO_TOLERANCE = 0.03

CARDS: dict[str, dict[str, str]] = {
    "strawberry": {
        "file": "raw/strawberry_neo.png",
        "title": "STRAWBERRY NEO",
        "badge": "NEO-S0824",
        "subtitle": "FRUIT DOG",
        "subject": "the red strawberry with the dog's face showing through its round hole, hanging from the green leaf parachute with its thin golden cords",
        "scenery": "the starry pastel sky, the pink clouds, the sparkles and the golden seed trail",
        "panel": '"CALYX GLIDE", "Leaf canopy open. Landing optional.", "AIRTIME", "855", the five stars, and "Tiny seeds. Grand entrance."',
        # 원본 확대 확인(09-18): 초록 3 · 옅은 회녹색 1 · 은회색(살짝 분홍빛) 1
        "stars": "exactly 5 stars, in this order from left to right: 3 green stars, then 1 pale grey-green star, then 1 silver-grey star",
    },
    "lettuce": {
        "file": "raw/Lettuce_neo.png",
        "title": "LETTUCE NEO",
        "badge": "NEO-0824",
        "subtitle": "VEGGIE DOG",
        "subject": "the lettuce figure with the dog's face showing through the leaves, standing on its lettuce-stem legs, and the lettuce leaves floating around it",
        "scenery": "the rainbow holographic light rays, the sparkles and the glossy floor",
        "panel": '"LEAF PARADE", "Loose leaves strut in a fresh breeze.", "FRESH FLUTTER", "800", the five stars, and "Loose leaves. Loud smile."',
        # 원본 확대 확인(09-18): 초록 3 · 은회색 2
        "stars": "exactly 5 stars, in this order from left to right: 3 green stars, then 2 silver-grey stars",
    },
}

KEEP = """Keep everything else exactly as it is in image 1, with the same look, size and proportions: the badge at the top right with the exact text "{badge}" (same text, font, letter size, color, shape and width — do not rename, shorten or resize it), the green ribbon with the text "{subtitle}", the round leaf icon under the badge, the circular portrait at the top left with the dog's face, {subject}, the holographic border, and the bottom panel with all its text and icons: {panel}. Do not add, remove or alter any text. Do not add new objects and do not duplicate existing ones."""

TITLE_ERASE = """Title plate at the top: erase only the letters of the title "{title}". Keep the title plate itself exactly as it is — its dark near-black background band, its metallic edges and its holographic sheen must remain. The result is an empty dark plate with no text. Do not make the plate wider or narrower."""

PROMPT_V1 = """Image 1 is a collectible trading card placed in the middle of a taller canvas. Above and below the card there is flat gray filler, and on the far left and right there are black bars.

Make exactly two changes:

1. """ + TITLE_ERASE + """

2. Turn it into one complete, taller card that fills the whole area between the black bars from the very top edge to the very bottom edge, with no gray left anywhere. The holographic border runs around the new outer edge. Get the extra height only by making the central illustration window taller: extend {scenery} upward and downward, so more of the scene shows above and below the subject. The whole header (circular portrait, title plate, badge, ribbon, leaf icon) sits at the new top of the card and the bottom panel sits at the new bottom, each keeping exactly its current size and shape. Do not stretch or squash anything — the header, the subject, the dog's face and the bottom panel keep their current proportions and scale.

""" + KEEP + """ Keep the black bars on the left and right. Output only the edited card."""

PROMPT_V2 = """Image 1 is a collectible trading card. Image 2 is a different card, given ONLY as a layout reference — do not copy any of its content, colors, texts, illustration, icons or dog.

Redraw image 1 as a taller card:

- Match image 2's overall proportions: the card is tall and narrow and sits centered with black bars on the left and right, exactly like image 2.
- Match the vertical position and the size of image 2's header at the top and of image 2's bottom panel at the bottom.
- Get the extra height only by making image 1's central illustration window taller: extend {scenery} upward and downward, so more of the scene shows above and below the subject. Do not stretch or squash anything — the header, the subject, the dog's face and the bottom panel keep their proportions.
- """ + TITLE_ERASE + """

""" + KEEP + """ Output only the edited card."""

PROMPT_V2R2 = """Image 1 is a collectible trading card. Image 2 is a different card, given ONLY as a layout reference — do not copy any of its content, colors, texts, illustration, icons, stars or dog.

Redraw image 1 as a taller card:

- Card width and margins: the card in your output must be exactly as wide as the card in image 2, and sit centered with the same empty solid black margins on the left and on the right as image 2. The card must NOT extend to the left or right edges of the image — leave those black margins empty. Only the height of the card fills the image from top to bottom, like image 2.
- Match the vertical position and the size of image 2's header at the top and of image 2's bottom panel at the bottom.
- Get the extra height only by making image 1's central illustration window taller: extend {scenery} upward and downward, so more of the scene shows above and below the subject. Do not stretch or squash anything — the header, the subject, the dog's face and the bottom panel keep their proportions.
- """ + TITLE_ERASE + """
- Star rating in the bottom panel: keep exactly the stars of image 1 — {stars}. Do not change the number or the colors of the stars.

""" + KEEP + """ Output only the edited card."""

VARIANTS = {"v1": PROMPT_V1, "v2": PROMPT_V2, "v2r2": PROMPT_V2R2}
ROUNDS = {1: ("v1", "v2"), 2: ("v2r2",)}


def call_model(prompt: str, images: list[Image.Image], api_key: str) -> tuple[Image.Image, str]:
    """이미지 여러 장 + 프롬프트 → 2:3 2K 이미지. (`cardimage_try.gemini_edit` 은 두 장 고정이라 따로 둔다.)"""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=240_000))
    cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio="2:3", image_size=SIZE),
    )
    contents: list[object] = [prompt]
    contents += [types.Part.from_bytes(data=png_bytes(im), mime_type="image/png") for im in images]
    resp = client.models.generate_content(model=MODEL, contents=contents, config=cfg)
    texts: list[str] = []
    for part in resp.candidates[0].content.parts:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            return Image.open(io.BytesIO(part.inline_data.data)), " ".join(texts)
        if getattr(part, "text", None):
            texts.append(part.text)
    raise RuntimeError(f"이미지가 안 왔습니다. 모델 텍스트: {' '.join(texts)!r}")


def inputs_for(stem: str, variant: str) -> tuple[str, list[Image.Image]]:
    c = CARDS[stem]
    orig = Image.open(CARDIMAGE / c["file"]).convert("RGB")
    fields = {k: v for k, v in c.items() if k != "file"}
    prompt = VARIANTS[variant].format(**fields)
    if variant == "v1":
        w, h = CARD_SIZE
        small = orig.resize((w, round(orig.height * w / orig.width)), Image.LANCZOS)
        canvas = Image.new("RGB", CARD_SIZE, FILLER)
        canvas.paste(small, (0, (h - small.height) // 2))
        return prompt, [pad_to_2_3(canvas)[0]]
    ref = Image.open(LAYOUT_REF).convert("RGB")
    return prompt, [pad_to_2_3(orig)[0], pad_to_2_3(ref)[0]]


def detect_card_bbox(img: Image.Image, dark: int = 40, pad_share: float = 0.9) -> tuple[int, int, int, int]:
    """검은 여백 대비 카드의 실제 상자 (left, top, right, bottom) — right·bottom 은 배타.

    열(행)마다 '어두운 픽셀 비율'을 보고, 가장자리에서 안쪽으로 가며 그 비율이 `pad_share` 미만이 되는
    첫 열(행)을 카드 경계로 본다. 어두운 제목판은 카드 높이의 몇 % 뿐이라 열 비율을 90% 까지 못 올린다.
    둥근 모서리의 검은 부분은 한 줄 전체로 보면 소수라 역시 경계를 밀어내지 않는다.
    """
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    step = 4

    def col_share(x: int, y0: int, y1: int) -> float:
        ys = range(y0, y1, step)
        return sum(1 for y in ys if px[x, y] < dark) / len(ys)

    def row_share(y: int, x0: int, x1: int) -> float:
        xs = range(x0, x1, step)
        return sum(1 for x in xs if px[x, y] < dark) / len(xs)

    left = next((x for x in range(w) if col_share(x, 0, h) < pad_share), 0)
    right = next((x for x in range(w - 1, -1, -1) if col_share(x, 0, h) < pad_share), w - 1) + 1
    top = next((y for y in range(h) if row_share(y, left, right) < pad_share), 0)
    bottom = next((y for y in range(h - 1, -1, -1) if row_share(y, left, right) < pad_share), h - 1) + 1
    return left, top, right, bottom


def crop_to_card(gen: Image.Image) -> tuple[Image.Image, dict[str, object]]:
    """상자대로 자른다. 비율이 허용 범위면 994×1582 로 줄이고, 아니면 늘리지 않고 높이만 맞춘다."""
    box = detect_card_bbox(gen)
    crop = gen.crop(box)
    ratio = crop.width / crop.height
    off = ratio / CARD_RATIO - 1
    adoptable = abs(off) <= RATIO_TOLERANCE
    if adoptable:
        final = crop.resize(CARD_SIZE, Image.LANCZOS)
    else:
        final = crop.resize((round(crop.width * CARD_SIZE[1] / crop.height), CARD_SIZE[1]), Image.LANCZOS)
    info = {"bbox": box, "bbox_size": crop.size, "ratio": round(ratio, 4), "ratio_off_pct": round(off * 100, 2),
            "adoptable_ratio": adoptable, "final_size": final.size}
    return final, info


def save_cropped(name: str, gen: Image.Image, meta: dict[str, object]) -> Path:
    final, info = crop_to_card(gen)
    meta.update(crop=info)
    p = OUT / (f"{name}.png" if info["adoptable_ratio"] else f"{name}_NOT_ADOPTABLE.png")
    final.save(p)
    print(f"  상자 {info['bbox']} 크기 {info['bbox_size']} 비율 {info['ratio']} ({info['ratio_off_pct']:+}%) → {p.name}")
    return p


def run_one(stem: str, variant: str, api_key: str) -> bool:
    prompt, images = inputs_for(stem, variant)
    name = f"{stem}_{variant}"
    meta: dict[str, object] = {
        "model": MODEL, "size": SIZE, "aspect": "2:3", "stem": stem, "variant": variant,
        "source": CARDS[stem]["file"], "sent_sizes": [im.size for im in images], "prompt": prompt,
        "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    for i, im in enumerate(images, 1):
        im.save(OUT / f"{name}_input{i}.png")
    try:
        gen, note = call_model(prompt, images, api_key)
    except Exception as e:  # noqa: BLE001 — 원인을 JSON 에 남기고 다음 조합으로 간다
        meta.update(error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc(),
                    finished_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        (OUT / f"{name}_error.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"실패: {name} — {type(e).__name__}: {str(e)[:300]}")
        return False
    gen = gen.convert("RGB")
    gen.save(OUT / f"{name}_raw2k.png")
    meta.update(model_text=note, generated_size=gen.size,
                finished_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    if variant == "v2r2":
        save_cropped(name, gen, meta)
    else:
        padded, pad = pad_to_2_3(Image.new("RGB", CARD_SIZE))
        final = fit_to_card(gen, pad=pad, card_size=CARD_SIZE, padded_width=padded.width)
        final.save(OUT / f"{name}.png")
        meta.update(final_size=final.size)
    (OUT / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {OUT / name}  (생성 {gen.size})")
    return True


def recrop(name: str) -> None:
    """호출 없이 기존 raw 에 상자 자르기를 적용해 `<name>_recrop.png` 로 남긴다."""
    gen = Image.open(OUT / f"{name}_raw2k.png").convert("RGB")
    meta: dict[str, object] = {"source_raw": f"{name}_raw2k.png",
                               "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    save_cropped(f"{name}_recrop", gen, meta)
    (OUT / f"{name}_recrop.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def first_existing(*names: str) -> Path | None:
    return next((OUT / n for n in names if (OUT / n).exists()), None)


def build_sheet(round_no: int) -> Path:
    row_h, gap, label_h = 1000, 16, 34
    font = ImageFont.truetype("C:/Windows/Fonts/malgunbd.ttf", 22)
    rows: list[list[tuple[str, Path | None]]] = []
    for stem, c in CARDS.items():
        cells: list[tuple[str, Path | None]] = [(f"{stem} 원본", CARDIMAGE / c["file"])]
        if round_no == 1:
            cells += [(f"{stem} {v}", first_existing(f"{stem}_{v}.png")) for v in ("v1", "v2")]
        else:
            cells.append((f"{stem} 1회차 v2", first_existing(f"{stem}_v2.png")))
            p = first_existing(f"{stem}_v2r2.png", f"{stem}_v2r2_NOT_ADOPTABLE.png")
            cells.append((f"{stem} 2회차" + (" (비율 불가)" if p and "NOT" in p.name else ""), p))
            rc = first_existing(f"{stem}_v2_recrop.png", f"{stem}_v2_recrop_NOT_ADOPTABLE.png")
            if rc:
                cells.append((f"{stem} 1회차 v2 다시 자름", rc))
        rows.append(cells)
    card_w = round(CARD_SIZE[0] * row_h / CARD_SIZE[1])
    scaled = []
    for cells in rows:
        out_cells = []
        for lbl, p in cells:
            im = Image.open(p).convert("RGB") if p else None
            out_cells.append((lbl, im.resize((round(im.width * row_h / im.height), row_h), Image.LANCZOS) if im else None))
        scaled.append(out_cells)
    width = max(sum((im.width if im else card_w) for _, im in cells) + gap * (len(cells) + 1) for cells in scaled)
    sheet = Image.new("RGB", (width, (row_h + label_h + gap) * len(scaled) + gap), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    y = gap
    for cells in scaled:
        x = gap
        for lbl, im in cells:
            d.text((x, y), lbl, font=font, fill=(255, 255, 255))
            if im:
                sheet.paste(im, (x, y + label_h))
                x += im.width + gap
            else:
                d.text((x, y + label_h + 20), "(없음)", font=font, fill=(255, 120, 120))
                x += card_w + gap
        y += row_h + label_h + gap
    if sheet.width > 2400:
        sheet = sheet.resize((2400, round(sheet.height * 2400 / sheet.width)), Image.LANCZOS)
    p = OUT / ("_sheet.png" if round_no == 1 else "_sheet_r2.png")
    sheet.save(p)
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--round", type=int, choices=[1, 2], default=2)
    ap.add_argument("--only", nargs="*", default=None, help="stem:variant 들만 (예: strawberry:v2r2)")
    ap.add_argument("--max-calls", type=int, default=2, help="이 실행에서 부를 수 있는 최대 호출 수")
    ap.add_argument("--sheet-only", action="store_true", help="API 없이 비교표만 다시 만든다")
    ap.add_argument("--recrop", nargs="*", default=None, help="API 없이 기존 raw 를 상자 자르기 (예: lettuce_v2)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.recrop is not None:
        for name in args.recrop:
            recrop(name)
        return
    if not args.sheet_only:
        jobs = [(s, v) for s in CARDS for v in ROUNDS[args.round]]
        if args.only:
            jobs = [j for j in jobs if f"{j[0]}:{j[1]}" in args.only]
        jobs = [j for j in jobs if not (OUT / f"{j[0]}_{j[1]}_raw2k.png").exists()]
        api_key = read_env_key()
        calls = 0
        for stem, variant in jobs:
            if calls >= args.max_calls:
                sys.exit(f"호출 상한 {args.max_calls} 에 닿아 멈춥니다. 남은 조합: {jobs[calls:]}")
            calls += 1
            print(f"== {stem} {variant} (호출 {calls})")
            run_one(stem, variant, api_key)
        print(f"이번 실행 호출 수: {calls}")
    print(build_sheet(args.round))


if __name__ == "__main__":
    main()
