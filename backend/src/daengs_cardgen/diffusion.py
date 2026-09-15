"""diffusers 편집 파이프라인 (D-078). torch·diffusers 는 `load()`·`edit()` 안에서만 import 한다.

FLUX.2-klein-4B 는 약 13GB 라 L4(24GB)에 bf16 그대로 올린다. Qwen-Image-Edit-2511 은 09-15 L4 nf4 결과가
깨져(틀 강아지가 남고 노이즈) D-078 에서 뺐다 — 코드도 #557 에서 걷어냈다.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from daengs_cardgen.models import CardGenModel, EditRequest

MODEL_REPOS = {
    "klein-4b": "black-forest-labs/FLUX.2-klein-4B",
}
KLEIN_DEFAULTS = {"steps": 4, "guidance": 1.0}      # 증류판 권장값 (모델 카드)


def resolve(req: EditRequest, defaults: dict[str, float]) -> tuple[int, float]:
    steps = req.steps if req.steps is not None else int(defaults["steps"])
    guidance = req.guidance if req.guidance is not None else float(defaults["guidance"])
    return steps, guidance


class KleinModel:
    name = "klein-4b"

    def __init__(self) -> None:
        self._pipe: Any = None

    def load(self) -> None:
        import torch
        from diffusers import Flux2KleinPipeline

        self._pipe = Flux2KleinPipeline.from_pretrained(
            MODEL_REPOS[self.name], torch_dtype=torch.bfloat16
        ).to("cuda")

    def edit(self, req: EditRequest) -> Image.Image:
        if self._pipe is None:
            raise RuntimeError("load() 를 먼저 불러야 합니다")
        import torch

        steps, guidance = resolve(req, KLEIN_DEFAULTS)
        result = self._pipe(
            image=req.images, prompt=req.prompt, width=req.width, height=req.height,
            num_inference_steps=steps, guidance_scale=guidance,
            generator=torch.Generator("cuda").manual_seed(req.seed),
        )
        return result.images[0]


MODELS: dict[str, type] = {KleinModel.name: KleinModel}


def model_by_name(name: str) -> CardGenModel:
    try:
        factory = MODELS[name]
    except KeyError:
        raise ValueError(f"모르는 모델 {name!r} — 가능: {sorted(MODELS)}") from None
    return factory()
