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
    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str,
                 seed: int | None = None) -> bytes:
        """틀 PNG + 사진 JPEG → 강아지가 바뀐 카드 PNG (994×1582)."""
        ...


def build_prompt(*, scene: str, badge: str, subtitle: str, outfit: str, face_hidden: bool = False,
                 face_only: bool = False) -> str:
    """카드마다 다른 것은 무대(scene)·배지·부제·의상(outfit)뿐이다. 의상 문장은 catalog 가 준다 —
    4월은 "아무것도 안 입는다", 9월은 "이미지 1 의 한복·쟁반 그대로".

    앞부분(lead)은 **세 갈래가 배타**다 — 기본(본문에 강아지가 통째로 보임) · `face_hidden` · `face_only`.
    둘을 같이 주면 `face_only` 가 이긴다.

    `face_hidden` 은 옷이 본문 강아지의 얼굴까지 덮는 틀(10월 유령 천)이다. 공통 앞부분의 「귀·주둥이·눈 색까지
    사진 강아지로」 요구가 그 틀에서는 천 위에 실제 얼굴을 합성했다(09-16 FLUX.2-klein-4B seed 2·3·4) —
    그래서 사진 강아지는 배지 초상화와 옷 밖으로 나온 발로만 옮긴다.

    `face_only` 는 그 반대로, 본문에 **구멍 속 얼굴만** 보이는 틀(딸기·상추, #592)이다. 몸이 없으니 의상
    문장이 맞지 않아 소품 금지 문장을 이 앞부분이 직접 갖는다 — 그 카드들의 `outfit` 은 빈 문자열이다."""
    if face_only:
        lead = (
            "In image 1 only the dog's face is visible, framed by the hole in the produce body; the dog has no "
            "visible body, legs or paws there. Edit image 1 so that this visible face becomes the face of the dog "
            "from image 2 — same breed, same fur color, fur length and texture, same ear shape and color, same "
            "muzzle length, same eye color and facial markings, with the same happy open-mouth expression and the "
            "same head angle and size as in image 1. Keep the produce body, its hole and everything attached to it "
            "exactly as in image 1 — do not draw the dog's body, legs, paws or tail anywhere. Do not carry over any "
            "accessories from image 2 — no collar, no leash, no harness, no clothing. Also replace the small "
            "circular portrait in the top-left badge with the face of the same dog from image 2."
        )
    elif face_hidden:
        lead = (
            "Edit image 1 so that the dog hidden under the costume in the main illustration becomes the dog from "
            "image 2. In the main illustration the dog's face and head stay completely covered by the costume — "
            "do not draw the dog's face, eyes, ears or muzzle there, and keep the costume's own black cut-out eyes "
            "and mouth exactly as they are. Show the dog from image 2 in the main illustration only through the "
            "paws peeking out, with the same fur color and texture as image 2. Replace the small circular portrait "
            "in the top-left badge with the face of the same dog from image 2 — same breed, same fur color, same "
            "ear shape, same muzzle and facial markings; the badge is the only place the dog's face appears."
        )
    else:
        lead = (
            "Edit image 1 so that the dog in the main illustration is replaced by the dog from image 2 — same "
            "breed, same fur color, fur length and texture, same ear shape and color, same muzzle length, same eye "
            "color and facial markings. Also replace the small circular portrait in the top-left badge with the "
            "face of the same dog from image 2."
        )
    return (
        "Image 1 is a collectible trading card. Image 2 is a photo of a real dog.\n\n"
        # `outfit` 이 빈 카드(face_only)에서 꼬리 공백이 남지 않게 rstrip. 달 카드는 글자까지 그대로다.
        f"{f'{lead} {outfit}'.rstrip()}\n\n"
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

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str,
                 seed: int | None = None) -> bytes:
        # Nano Banana 2 는 seed 를 받지 않는다. 인자를 받되 버린다 — 프로토콜을 하나로 두기 위해서다.
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
    #557 E1 이 1280×2048 을 비교한다. `generate_batch` 는 한 요청으로 여러 장을 받는다(#557 E2).
    """

    def __init__(self, *, base_url: str, timeout_s: float, seed: int | None = None,
                 gen_size: tuple[int, int] = GEN_SIZE,
                 auth: Callable[[str], str] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._base = base_url.strip().rstrip("/")
        self._timeout_s, self._seed, self._auth, self._transport = timeout_s, seed, auth, transport
        self._gen_size = gen_size
        self.last_meta: dict | None = None

    def _post(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str, count: int,
              seed: int | None) -> tuple[int, httpx.Response]:
        if not self._base:
            raise EngineError("no_key", "DAENGS_CARDGEN_URL 이 비어 있습니다")
        seed = seed if seed is not None else random.randrange(2**31)
        body = {
            "images_b64": [base64.b64encode(template_png).decode(), base64.b64encode(photo_jpeg).decode()],
            "prompt": prompt, "seed": seed, "width": self._gen_size[0], "height": self._gen_size[1],
        }
        if count != 1:
            body["count"] = count
        try:
            headers = {"Authorization": f"Bearer {self._auth(self._base)}"} if self._auth else {}
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                resp = client.post(f"{self._base}/generate", json=body, headers=headers)
        except Exception as exc:  # 토큰 발급 실패까지 "응답을 못 받은" 것으로 모은다 (realtime_client 와 같은 판단)
            raise EngineError("upstream", f"카드 생성 서비스 호출 실패: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise EngineError("upstream", f"카드 생성 서비스가 {resp.status_code} 을 돌려줬습니다: {resp.text[:200]!r}")
        return seed, resp

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str,
                 seed: int | None = None) -> bytes:
        # 호출 인자가 있으면 그것을 쓰고, 없으면 생성자 값을 쓴다.
        effective_seed = seed if seed is not None else self._seed
        seed, resp = self._post(template_png=template_png, photo_jpeg=photo_jpeg, prompt=prompt, count=1,
                                seed=effective_seed)
        self.last_meta = {"seed": seed, "seconds": resp.headers.get("X-Cardgen-Seconds"),
                          "model": resp.headers.get("X-Cardgen-Model"),
                          "size": f"{self._gen_size[0]}x{self._gen_size[1]}"}
        return _decode_and_fit(resp.content, pad=0, padded_width=CARD_SIZE[0])

    def generate_batch(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str, count: int) -> list[bytes]:
        """한 요청에 `count` 장(서비스 `count`, #557 E2). 장마다 카드 크기 PNG, 장별 seed 는 `last_meta["seeds"]`."""
        _, resp = self._post(template_png=template_png, photo_jpeg=photo_jpeg, prompt=prompt, count=count,
                             seed=self._seed)
        try:
            data = resp.json()
            images = [base64.b64decode(b) for b in data["images_png_b64"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise EngineError("no_image", f"여러 장 응답을 읽을 수 없습니다: {exc}") from exc
        if len(images) != count:
            raise EngineError("no_image", f"{count} 장을 요청했는데 {len(images)} 장이 왔습니다")
        self.last_meta = {"seeds": data.get("seeds"), "seconds": data.get("seconds"), "model": data.get("model"),
                          "size": data.get("size"), "count": count}
        return [_decode_and_fit(image, pad=0, padded_width=CARD_SIZE[0]) for image in images]
