"""강아지 교체 엔진. 기본은 Nano Banana 2 (`gemini-3.1-flash-image`). 프롬프트·2:3 패딩·잘라내기는
실험 스크립트 backend/tools/cardimage_try.py 에서 검증된 그대로다 (worklog 09-13~14, 16장 「됨」)."""

from __future__ import annotations

import io
from typing import Protocol

from PIL import Image

CARD_SIZE = (994, 1582)


class EngineError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class CardImageEngine(Protocol):
    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        """틀 PNG + 사진 JPEG → 강아지가 바뀐 카드 PNG (994×1582)."""
        ...


def build_prompt(*, scene: str, badge: str, subtitle: str) -> str:
    return (
        "Image 1 is a collectible trading card. Image 2 is a photo of a real dog.\n\n"
        "Edit image 1 so that the dog in the main illustration is replaced by the dog from image 2 — same breed, "
        "same fur color, fur length and texture, same ear shape and color, same muzzle length, same eye color and "
        "facial markings. Also replace the small circular portrait in the top-left badge with the face of the same dog "
        "from image 2. Do not carry over any accessories from image 2 — no collar, no leash, no harness, no clothing; "
        "the dog wears nothing, exactly like the dog in image 1.\n\n"
        f"Everything else must stay pixel-identical: {scene}, the background, the holographic border, the empty dark "
        f'title plate at the top (leave it empty — do not write anything on it), the badge "{badge}", the text '
        f'"{subtitle}", the bottom panel with all its text, stars and icons. Do not add, remove or alter any text. '
        "Output only the edited card."
    )


def pad_to_2_3(card: Image.Image) -> tuple[Image.Image, int]:
    """Gemini 비율 선택지에 카드 비율(≈0.63)이 없어 좌우 검은 띠로 2:3 을 만든다. 찌그러뜨리지 않는다."""
    target_w = round(card.height * 2 / 3)
    pad = max(0, (target_w - card.width) // 2)
    padded = Image.new("RGB", (card.width + pad * 2, card.height), (0, 0, 0))
    padded.paste(card, (pad, 0))
    return padded, pad


def fit_to_card(gen: Image.Image, *, pad: int, card_size: tuple[int, int], padded_width: int) -> Image.Image:
    w, h = card_size
    g = gen.convert("RGB").resize((padded_width, h), Image.LANCZOS)
    return g.crop((pad, 0, pad + w, h))


class GeminiCardImageEngine:
    def __init__(self, *, api_key: str, model: str, size: str, timeout_ms: int) -> None:
        self._api_key, self._model, self._size, self._timeout_ms = api_key.strip(), model, size, timeout_ms

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        if not self._api_key:
            raise EngineError("no_key", "DAENGS_CARDIMAGE_GEMINI_API_KEY 가 비어 있습니다")
        from google import genai
        from google.genai import types

        card = Image.open(io.BytesIO(template_png)).convert("RGB")
        padded, pad = pad_to_2_3(card)
        src = io.BytesIO()
        padded.save(src, "PNG")
        client = genai.Client(api_key=self._api_key, http_options=types.HttpOptions(timeout=self._timeout_ms))
        try:
            resp = client.models.generate_content(
                model=self._model,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=src.getvalue(), mime_type="image/png"),
                    types.Part.from_bytes(data=photo_jpeg, mime_type="image/jpeg"),
                ],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="2:3", image_size=self._size),
                ),
            )
        except Exception as exc:  # SDK 예외 계층이 넓다 — 코드 하나로 모은다
            raise EngineError("upstream", f"이미지 모델 호출 실패: {exc}") from exc
        for part in resp.candidates[0].content.parts:
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                gen = Image.open(io.BytesIO(data))
                out = io.BytesIO()
                fit_to_card(gen, pad=pad, card_size=CARD_SIZE, padded_width=padded.width).save(out, "PNG")
                return out.getvalue()
        raise EngineError("no_image", "모델이 이미지를 돌려주지 않았습니다")
