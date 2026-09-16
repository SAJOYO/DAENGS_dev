"""모델 하나가 지켜야 할 모양과 요청. 실제 diffusers 구현은 `diffusion.py`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image

#: 한 요청의 이미지 수 상한. 우리는 틀·사진 두 장만 쓴다 — klein 호스팅 API 의 상한(4)에 맞췄다.
MAX_IMAGES = 4
#: 한 요청에 뽑는 장수 상한 — "4장 뽑아 고르기"(roadmap 3번). L4 메모리는 #557 E2 에서 잰다.
MAX_COUNT = 4
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
    count: int = 1


class CardGenModel(Protocol):
    name: str

    def load(self) -> None: ...

    def edit(self, req: EditRequest) -> list[Image.Image]:
        """`req.count` 장을 돌려준다. i 번째 장의 seed 는 `seeds_for(req.seed, req.count)[i]`."""
        ...


def seeds_for(seed: int, count: int) -> list[int]:
    """장마다 다른 seed — 요청 seed 부터 1씩(2**31 에서 0 으로 돈다). 운영에서는 이 값을 장별로 저장한다(roadmap 3번)."""
    return [(seed + i) % 2**31 for i in range(count)]


def snap(value: int) -> int:
    """diffusers 파이프라인은 16의 배수 크기를 요구한다. 가장 가까운 배수로(최소 16)."""
    return max(SIZE_STEP, round(value / SIZE_STEP) * SIZE_STEP)
