"""모델 하나가 지켜야 할 모양과 요청. 실제 diffusers 구현은 `diffusion.py`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image

#: 한 요청의 이미지 수 상한. 우리는 틀·사진 두 장만 쓴다 — klein 호스팅 API 의 상한(4)에 맞췄다.
MAX_IMAGES = 4
SIZE_STEP = 16


@dataclass(frozen=True)
class EditRequest:
    images: list[Image.Image]
    prompt: str
    seed: int
    width: int
    height: int
    steps: int | None = None
    guidance: float | None = None


class CardGenModel(Protocol):
    name: str

    def load(self) -> None: ...

    def edit(self, req: EditRequest) -> Image.Image: ...


def snap(value: int) -> int:
    """diffusers 파이프라인은 16의 배수 크기를 요구한다. 가장 가까운 배수로(최소 16)."""
    return max(SIZE_STEP, round(value / SIZE_STEP) * SIZE_STEP)
