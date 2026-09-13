"""생성된 카드가 그 강아지인지, 글자·아바타가 무사한지 텍스트 모델(flash-lite)에 묻는다.
실험(worklog 09-14)에서 정면 사진도 6장 중 1장이 어긋났고, 2K 에서 1장 편차가 있었다 — 그래서 검수+재시도가 필수다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

PROMPT = """Image 1 is a photo of a real dog. Image 2 is an illustrated trading card that is supposed to show the SAME dog.

Answer with JSON only, no prose:
{"likeness": <1-5>, "text_ok": <true|false>, "avatar_ok": <true|false>, "note": "<one short sentence>"}

- likeness: 5 = clearly the same dog (breed, fur color and length, ear shape, muzzle, markings); 3 = same breed but details differ; 1 = a different dog.
- text_ok: true if every piece of printed text on the card is intact and legible (no garbled or missing letters).
- avatar_ok: true if the small circular portrait at the top-left shows the same dog as the main illustration."""


class JudgeError(Exception):
    pass


@dataclass(frozen=True)
class JudgeResult:
    likeness: int
    text_ok: bool
    avatar_ok: bool
    note: str


class CardJudge(Protocol):
    def judge(self, *, photo_jpeg: bytes, card_png: bytes) -> JudgeResult: ...


def parse_judge_json(text: str) -> JudgeResult:
    try:
        d = json.loads(text)
        likeness = int(d["likeness"])
        text_ok, avatar_ok = bool(d["text_ok"]), bool(d["avatar_ok"])
        note = str(d.get("note", ""))[:200]
    except (ValueError, KeyError, TypeError) as exc:
        raise JudgeError(f"검수 응답을 읽을 수 없습니다: {text[:80]!r}") from exc
    if not 1 <= likeness <= 5:
        raise JudgeError(f"likeness 범위 밖: {likeness}")
    return JudgeResult(likeness=likeness, text_ok=text_ok, avatar_ok=avatar_ok, note=note)


class GeminiCardJudge:
    """flash-lite 로 생성된 카드를 검수하는 실제 판정자. `google.genai` 는 `judge()` 안에서만
    import 한다 — 이 모듈을 불러오는 것만으로 SDK 가 딸려오지 않게 (`engine.py` 와 같은 규칙)."""

    def __init__(self, *, api_key: str, model: str, timeout_ms: int) -> None:
        self._api_key, self._model, self._timeout_ms = api_key.strip(), model, timeout_ms

    def judge(self, *, photo_jpeg: bytes, card_png: bytes) -> JudgeResult:
        if not self._api_key:
            raise JudgeError("DAENGS_CARDIMAGE_GEMINI_API_KEY 가 비어 있습니다")
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key, http_options=types.HttpOptions(timeout=self._timeout_ms))
        try:
            resp = client.models.generate_content(
                model=self._model,
                contents=[
                    PROMPT,
                    types.Part.from_bytes(data=photo_jpeg, mime_type="image/jpeg"),
                    types.Part.from_bytes(data=card_png, mime_type="image/png"),
                ],
                config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
            )
        except Exception as exc:  # SDK 예외 계층이 넓다 — 코드 하나로 모은다
            raise JudgeError(f"검수 호출 실패: {exc}") from exc
        return parse_judge_json(resp.text or "")
