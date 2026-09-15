"""강아지 교체 엔진. 기본은 Nano Banana 2 (`gemini-3.1-flash-image`). 프롬프트·2:3 패딩·잘라내기는
실험 스크립트 backend/tools/cardimage_try.py 에서 검증된 그대로다 (worklog 09-13~14, 16장 「됨」)."""

from __future__ import annotations

import base64
import io
import random
from collections.abc import Callable
from typing import Protocol

import httpx
from PIL import Image, UnidentifiedImageError

CARD_SIZE = (994, 1582)

#: GPU 서비스(D-078)에 요청하는 생성 크기. 16의 배수이면서 카드 비율(0.628)에 가장 가깝다(0.627).
#: 받은 뒤 CARD_SIZE 로 줄인다 — 가로·세로 배율 차이는 0.1% 라 눈에 안 띈다.
GEN_SIZE = (1024, 1632)


class EngineError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class CardImageEngine(Protocol):
    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        """틀 PNG + 사진 JPEG → 강아지가 바뀐 카드 PNG (994×1582)."""
        ...


def build_prompt(*, scene: str, badge: str, subtitle: str, outfit: str) -> str:
    """달마다 다른 것은 무대(scene)·배지·부제·의상(outfit)뿐이다. 의상 문장은 catalog 가 준다 —
    4월은 "아무것도 안 입는다", 9월은 "이미지 1 의 한복·쟁반 그대로"."""
    return (
        "Image 1 is a collectible trading card. Image 2 is a photo of a real dog.\n\n"
        "Edit image 1 so that the dog in the main illustration is replaced by the dog from image 2 — same breed, "
        "same fur color, fur length and texture, same ear shape and color, same muzzle length, same eye color and "
        "facial markings. Also replace the small circular portrait in the top-left badge with the face of the same dog "
        f"from image 2. {outfit}\n\n"
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


def _decode_and_fit(image_bytes: bytes, *, pad: int, padded_width: int) -> bytes:
    """모델이 돌려준 바이트를 카드 크기로 디코드·리사이즈한다. 모델이 이미지가 아닌 바이트를
    주면(안전 차단 회피 문구를 실은 텍스트가 `inline_data` 로 온 경우 등) `Image.open` 이
    `UnidentifiedImageError` 를, 잘린 데이터는 `save` 단계에서 `OSError` 를 낼 수 있다 —
    둘 다 `EngineError("no_image", ...)` 하나로 모아 라우터가 이미 아는 코드만 보게 한다."""
    try:
        gen = Image.open(io.BytesIO(image_bytes))
        out = io.BytesIO()
        fit_to_card(gen, pad=pad, card_size=CARD_SIZE, padded_width=padded_width).save(out, "PNG")
        return out.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise EngineError("no_image", f"모델 출력이 이미지가 아닙니다: {exc}") from exc


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
        return _decode_and_fit(image_bytes, pad=pad, padded_width=padded.width)


class HttpCardImageEngine:
    """GPU 카드 생성 서비스(`daengs_cardgen`, D-078)를 부르는 엔진.

    인증은 `auth(audience) -> ID 토큰` 을 주입받는다 — 이 패키지는 backend 를 import 하지 않으므로
    토큰 발급(메타데이터 서버)은 backend 가 넘긴다. `auth=None` 이면 헤더 없이 부른다
    (`gcloud run services proxy` 로 연 로컬 포트 — 비교 도구가 쓴다).
    `seed=None` 이면 호출마다 무작위. 마지막 호출의 seed·서비스 시간·모델·보낸 크기는 `last_meta` 에 남긴다.
    `gen_size` 는 서비스에 요청하는 생성 크기다(기본 `GEN_SIZE`). 결과는 크기와 상관없이 `CARD_SIZE` 로 줄인다 —
    #557 E1 이 1280×2048 을 비교한다.
    """

    def __init__(self, *, base_url: str, timeout_s: float, seed: int | None = None,
                 gen_size: tuple[int, int] = GEN_SIZE,
                 auth: Callable[[str], str] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._base = base_url.strip().rstrip("/")
        self._timeout_s, self._seed, self._auth, self._transport = timeout_s, seed, auth, transport
        self._gen_size = gen_size
        self.last_meta: dict | None = None

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        if not self._base:
            raise EngineError("no_key", "DAENGS_CARDGEN_URL 이 비어 있습니다")
        seed = self._seed if self._seed is not None else random.randrange(2**31)
        body = {
            "images_b64": [base64.b64encode(template_png).decode(), base64.b64encode(photo_jpeg).decode()],
            "prompt": prompt, "seed": seed, "width": self._gen_size[0], "height": self._gen_size[1],
        }
        try:
            headers = {"Authorization": f"Bearer {self._auth(self._base)}"} if self._auth else {}
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                resp = client.post(f"{self._base}/generate", json=body, headers=headers)
        except Exception as exc:  # 토큰 발급 실패까지 "응답을 못 받은" 것으로 모은다 (realtime_client 와 같은 판단)
            raise EngineError("upstream", f"카드 생성 서비스 호출 실패: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise EngineError("upstream", f"카드 생성 서비스가 {resp.status_code} 을 돌려줬습니다: {resp.text[:200]!r}")
        self.last_meta = {"seed": seed, "seconds": resp.headers.get("X-Cardgen-Seconds"),
                          "model": resp.headers.get("X-Cardgen-Model"),
                          "size": f"{self._gen_size[0]}x{self._gen_size[1]}"}
        return _decode_and_fit(resp.content, pad=0, padded_width=CARD_SIZE[0])
