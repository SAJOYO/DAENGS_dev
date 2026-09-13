"""카드 생성 테스트용 가짜 엔진·검수. 실제 Gemini 는 어떤 테스트도 부르지 않는다."""

from __future__ import annotations

import io

from PIL import Image

from daengs_backend.services.cardimage.judge import JudgeResult


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


class FakeJudge:
    def __init__(self, scores: list[int] | None = None, error: Exception | None = None) -> None:
        self.scores = scores or [5]
        self.error = error
        self.calls = 0

    def judge(self, *, photo_jpeg: bytes, card_png: bytes) -> JudgeResult:
        self.calls += 1
        if self.error:
            raise self.error
        s = self.scores[min(self.calls - 1, len(self.scores) - 1)]
        return JudgeResult(likeness=s, text_ok=True, avatar_ok=True, note="fake")
