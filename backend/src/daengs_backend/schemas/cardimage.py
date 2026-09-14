"""`/admin/cardimage/generate` 응답. 저장하지 않으므로 id·created_at 이 없다 — 그 한 번의
결과만 실어 나른다."""

from __future__ import annotations

from pydantic import BaseModel, Field


class JudgeOut(BaseModel):
    likeness: int = Field(ge=1, le=5)
    text_ok: bool
    avatar_ok: bool
    note: str


class CardImageResponse(BaseModel):
    month: int
    title: str
    attempts: int
    judge: JudgeOut | None
    png_base64: str
    elapsed_ms: int
