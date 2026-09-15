"""카드 생성 테스트용 가짜 엔진·검수. 실제 Gemini 는 어떤 테스트도 부르지 않는다."""

from __future__ import annotations

import io

from PIL import Image

from daengs_cardimage.judge import JudgeResult


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
    """`scores` 항목은 점수(`int`)거나, 그 회차에서 그대로 raise 할 `Exception` 이어도 된다
    (예: `FakeJudge([2, JudgeError("boom")])` 로 "두 번째 검수만 실패"를 흉내낸다).
    `error` 는 매 호출마다 실패하는 옛 방식 그대로 남겨 둔다."""

    def __init__(self, scores: list[int | Exception] | None = None, error: Exception | None = None) -> None:
        self.scores = scores or [5]
        self.error = error
        self.calls: int = 0

    def judge(self, *, photo_jpeg: bytes, card_png: bytes) -> JudgeResult:
        self.calls += 1
        if self.error:
            raise self.error
        s = self.scores[min(self.calls - 1, len(self.scores) - 1)]
        if isinstance(s, Exception):
            raise s
        return JudgeResult(likeness=s, text_ok=True, avatar_ok=True, note="fake")
