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
    """Gemini 비율 선택지에 카드 비율(≈0.63)이 없어 좌우 검은 띠로 2:3 을 만든다. 찌그러뜨리지 않는다.

    `pad` 는 정수 나눗셈(`// 2`)이라 994×1582 카드에서 목표 폭 1055 를 정확히 채우지 못하고
    패딩된 폭은 1054(1055 가 아니다) 가 된다 — 반 픽셀 어긋남은 실제로 보내는 요청이
    `aspect_ratio="2:3"` 로 비율 자체를 지정하므로 결과 크기에는 영향이 없다.
    """
    target_w = round(card.height * 2 / 3)
    pad = max(0, (target_w - card.width) // 2)
    padded = Image.new("RGB", (card.width + pad * 2, card.height), (0, 0, 0))
    padded.paste(card, (pad, 0))
    return padded, pad


def fit_to_card(gen: Image.Image, *, pad: int, card_size: tuple[int, int], padded_width: int) -> Image.Image:
    """모델이 돌려준 이미지를 패딩 폭에 맞춰 리사이즈한 뒤 좌우 검은 띠를 잘라 카드 크기로 되돌린다."""
    w, h = card_size
    g = gen.convert("RGB").resize((padded_width, h), Image.LANCZOS)
    return g.crop((pad, 0, pad + w, h))


def _extract_image_bytes(resp: object) -> bytes:
    """응답에서 이미지 바이트를 꺼낸다. 안전 차단으로 `candidates` 가 비거나 `content` 가 없는
    경우까지 방어적으로 읽어, 이미지가 없을 때 항상 `EngineError("no_image", ...)` 하나로만
    실패하게 한다(IndexError·AttributeError 가 generate() 밖으로 새지 않는다). 모델이 이미지
    대신 텍스트만 준 경우 그 텍스트를 메시지에 담아 거절 사유를 알 수 있게 한다
    (`tools/cardimage_try.py` 의 `gemini_edit` 과 같은 관례).
    """
    candidates = getattr(resp, "candidates", None) or []
    content = getattr(candidates[0], "content", None) if candidates else None
    parts = getattr(content, "parts", None) or []
    texts: list[str] = []
    for part in parts:
        data = getattr(getattr(part, "inline_data", None), "data", None)
        if data:
            return data
        if getattr(part, "text", None):
            texts.append(part.text)
    detail = "모델이 이미지를 돌려주지 않았습니다"
    if texts:
        detail += f": {' '.join(texts)[:200]!r}"
    raise EngineError("no_image", detail)


class GeminiCardImageEngine:
    """Nano Banana 2(`gemini-3.1-flash-image`) 로 강아지를 교체하는 실제 엔진. `google.genai` 는
    `generate()` 안에서만 import 한다 — 이 모듈을 불러오는 것만으로 SDK 가 딸려오지 않게
    (`services/chat_summary.py` 의 `_gemini_client` 와 같은 규칙)."""

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
        image_bytes = _extract_image_bytes(resp)
        gen = Image.open(io.BytesIO(image_bytes))
        out = io.BytesIO()
        fit_to_card(gen, pad=pad, card_size=CARD_SIZE, padded_width=padded.width).save(out, "PNG")
        return out.getvalue()
