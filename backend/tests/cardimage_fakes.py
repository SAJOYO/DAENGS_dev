"""카드 생성 테스트용 가짜 엔진. 실제 Gemini 는 어떤 테스트도 부르지 않는다.

Task 6 이 여기에 `FakeJudge`(judge.JudgeResult 를 쓰는) 를 이어 붙인다 — judge 모듈이
아직 없어 이 파일은 그때까지 `FakeEngine` 만 가진다.
"""

from __future__ import annotations

import io

from PIL import Image


def png(w: int = 994, h: int = 1582, color=(10, 200, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class FakeEngine:
    def __init__(self, outputs: list[bytes] | None = None, error: Exception | None = None) -> None:
        self.outputs = outputs or [png()]
        self.error = error
        self.calls: list[dict] = []

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        self.calls.append({"template": template_png, "photo": photo_jpeg, "prompt": prompt})
        if self.error:
            raise self.error
        return self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]
