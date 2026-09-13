"""도감 카드 한 장에 사용자 강아지 넣어 보기 — 0단계 타당성 실험 (#496, docs/cardimage/).

`cardimage/4_blossom.webp`(4월 카드) + `cardimage/test/*.jpg`(강아지 사진 한 장) 을 이미지 편집
모델에 **통째로** 보내 강아지(본체와 왼쪽 위 아바타)만 바뀐 카드를 받아 `cardimage/out/` 에
저장합니다. 결과 옆에 같은 이름의 `.json` 으로 프롬프트·모델·크기를 남기고, 모델이 준 그대로는
`_raw.png` 로 따로 둡니다.

카드 비율(994×1582 ≈ 0.63)이 Gemini 선택지(9:16·2:3·3:4…)에 없어서, 좌우에 검은 띠를 붙여
2:3 으로 보내고 결과에서 같은 띠를 잘라냅니다. 찌그러뜨리지 않습니다.

그림 영역만 잘라 보내고 틀은 원본으로 덮던 "art 모드"는 2026-09-14 에 **폐기했습니다**
(합성 이음새가 어색함 — worklog 09-14). 다시 만들지 마세요.

실행 (backend/ 에서, Pillow 는 venv 에 없으니 --with):
  uv run --with pillow python tools/cardimage_try.py                         # 1K 한 장
  uv run --with pillow python tools/cardimage_try.py --size 2K -n 2 --photo-max 1600
  uv run --with pillow python tools/cardimage_try.py --photo ../cardimage/test/<파일>.jpg

키는 `DAENGS_CARDIMAGE_GEMINI_API_KEY` 를 먼저 보고, 없으면 `GEMINI_API_KEY` 로 떨어집니다
(backend/.env). 카드 생성용 프로젝트를 따로 팠으면 앞의 이름으로 넣으세요.

⚠ 한 번 부를 때마다 돈이 나갑니다 — 1K 한 장 약 $0.067, 2K 약 $0.10 (2026-09 가격표).
   돌리기 전에 순서·비용을 사용자에게 설명하고 승인을 받습니다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]  # 저장소 루트 (backend/tools/ → ../..)
CARDIMAGE = ROOT / "cardimage"

# 무대 고정(매트 색·소품 금지)은 사용자 결정 09-13 — "무대가 흔들리면 카드 정체성 훼손".
# 변주는 강아지 쪽(표정·털 결)에만 둔다.
PROMPT = """Image 1 is a collectible trading card. Image 2 is a photo of a real dog.

Edit image 1 so that the dog in the main illustration is replaced by the dog from image 2 — same breed, same fur color, fur length and texture, same ear shape and color, same muzzle length, same eye color and facial markings. Also replace the small circular portrait in the top-left badge with the face of the same dog from image 2. Do not carry over any accessories from image 2 — no collar, no leash, no harness, no clothing; the dog wears nothing, exactly like the dog in image 1.

Everything else must stay pixel-identical: the pose (sitting on the picnic blanket looking up at a falling petal), the green-and-white checked picnic blanket (keep this exact color and pattern), the wicker basket, the background, the holographic border, every piece of text ("BLOSSOM NEO", "APRIL SPECIAL", "NEO-APR25", "PETAL PAUSE", "One petal. Perfect timing.", "SPRING", "920", "Bloomed right on schedule."), the stars and all icons. Do not add, remove or alter any text. Output only the edited card."""


def load_photo(path: Path, max_side: int) -> Image.Image:
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if max_side > 0:
        im.thumbnail((max_side, max_side))
    return im


def png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def pad_to_2_3(card: Image.Image) -> tuple[Image.Image, int]:
    """좌우 검은 띠로 2:3 을 만든다. 돌려주는 pad 만큼 결과에서 잘라낸다."""
    target_w = round(card.height * 2 / 3)
    pad = max(0, (target_w - card.width) // 2)
    padded = Image.new("RGB", (card.width + pad * 2, card.height), (0, 0, 0))
    padded.paste(card, (pad, 0))
    return padded, pad


def gemini_edit(model: str, prompt: str, source: Image.Image, photo: Image.Image, size: str, api_key: str) -> tuple[Image.Image, str]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=180_000))
    cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio="2:3", image_size=size),
    )
    resp = client.models.generate_content(
        model=model,
        contents=[
            prompt,
            types.Part.from_bytes(data=png_bytes(source), mime_type="image/png"),
            types.Part.from_bytes(data=png_bytes(photo), mime_type="image/png"),
        ],
        config=cfg,
    )
    text_parts: list[str] = []
    for part in resp.candidates[0].content.parts:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            return Image.open(io.BytesIO(part.inline_data.data)), " ".join(text_parts)
        if getattr(part, "text", None):
            text_parts.append(part.text)
    raise RuntimeError(f"이미지가 안 왔습니다. 모델 텍스트: {' '.join(text_parts)!r}")


def read_env_key() -> str:
    # backend/.env 를 가볍게 읽는다 — pydantic settings 를 끌어오지 않기 위해
    env = ROOT / "backend" / ".env"
    vals: dict[str, str] = {}
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    for name in ("DAENGS_CARDIMAGE_GEMINI_API_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(name) or vals.get(name)
        if v:
            return v
    sys.exit("Gemini 키가 없습니다 — backend/.env 에 DAENGS_CARDIMAGE_GEMINI_API_KEY 또는 GEMINI_API_KEY")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--card", type=Path, default=CARDIMAGE / "4_blossom.webp")
    ap.add_argument("--photo", type=Path, default=None, help="기본: cardimage/test/ 의 첫 jpg")
    ap.add_argument("--model", default="gemini-3.1-flash-image")
    ap.add_argument("--size", default="1K", help="0.5K / 1K / 2K")
    ap.add_argument("-n", type=int, default=1, help="같은 조건으로 몇 장")
    ap.add_argument("--photo-max", type=int, default=0, help="사진 긴 변 상한. 기본 0 = 원본 그대로(사용자가 폰 원본을 넣는 가정). 앱이 프로필 사진을 보내는 크기와 맞추려면 1600")
    ap.add_argument("--out", type=Path, default=CARDIMAGE / "out")
    args = ap.parse_args()

    photo_path = (args.photo or sorted((CARDIMAGE / "test").glob("*.jpg"))[0]).resolve()
    card_path = args.card.resolve()
    card = Image.open(card_path).convert("RGB")
    photo = load_photo(photo_path, args.photo_max)
    args.out.mkdir(parents=True, exist_ok=True)

    api_key = read_env_key()
    source, pad = pad_to_2_3(card)

    stamp = dt.datetime.now().strftime("%m%d_%H%M%S")
    for i in range(args.n):
        gen, note = gemini_edit(args.model, PROMPT, source, photo, args.size, api_key)
        final = gen.convert("RGB").resize(source.size, Image.LANCZOS).crop((pad, 0, pad + card.width, card.height))
        name = f"{stamp}_{args.model}_full_{args.size}_{i + 1}"
        final.save(args.out / f"{name}.png")
        gen.save(args.out / f"{name}_raw.png")
        (args.out / f"{name}.json").write_text(
            json.dumps(
                {
                    "model": args.model, "mode": "full", "size": args.size, "aspect": "2:3",
                    "card": str(card_path.relative_to(ROOT)), "photo": str(photo_path.relative_to(ROOT)),
                    "photo_sent_size": photo.size, "source_sent_size": source.size,
                    "generated_size": gen.size, "prompt": PROMPT, "model_text": note,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"저장: {args.out / name}.png  (생성 {gen.size}, 모델 메모: {note[:80]!r})")


if __name__ == "__main__":
    main()
