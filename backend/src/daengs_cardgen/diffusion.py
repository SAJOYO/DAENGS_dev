"""diffusers 편집 파이프라인 두 개 (D-078). torch·diffusers 는 `load()`·`edit()` 안에서만 import 한다.

Qwen-Image-Edit-2511 은 bf16 가 약 40GB(transformer 20B + Qwen2.5-VL 7B)라 L4(24GB)에 그대로 안 들어간다.
기본은 transformer·text_encoder 를 bitsandbytes nf4 로 올린다(`CARDGEN_QWEN_QUANT=nf4`). `none` 은
96GB GPU(RTX PRO 6000)에서 bf16 으로 비교할 때만 쓴다. klein 4B 는 약 13GB 라 bf16 그대로다.
"""

from __future__ import annotations

import os
from typing import Any

from PIL import Image

from daengs_cardgen.models import CardGenModel, EditRequest

MODEL_REPOS = {
    "klein-4b": "black-forest-labs/FLUX.2-klein-4B",
    "qwen-edit-2511": "Qwen/Qwen-Image-Edit-2511",
}
KLEIN_DEFAULTS = {"steps": 4, "guidance": 1.0}      # 증류판 권장값 (모델 카드)
QWEN_DEFAULTS = {"steps": 40, "guidance": 4.0}      # true_cfg_scale (모델 카드). Lightning 4-step 은 일러스트 품질 저하 보고가 있어 안 쓴다
QWEN_QUANTS = ("nf4", "none")


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


class QwenModel:
    name = "qwen-edit-2511"

    def __init__(self, quant: str | None = None) -> None:
        self.quant = quant or os.environ.get("CARDGEN_QWEN_QUANT", "nf4")
        if self.quant not in QWEN_QUANTS:
            raise ValueError(f"CARDGEN_QWEN_QUANT 는 {QWEN_QUANTS} 중 하나입니다: {self.quant!r}")
        self._pipe: Any = None

    def load(self) -> None:
        import torch
        from diffusers import QwenImageEditPlusPipeline

        kwargs: dict[str, Any] = {"torch_dtype": torch.bfloat16}
        if self.quant == "nf4":
            from diffusers.quantizers import PipelineQuantizationConfig

            kwargs["quantization_config"] = PipelineQuantizationConfig(
                quant_backend="bitsandbytes_4bit",
                quant_kwargs={
                    "load_in_4bit": True,
                    "bnb_4bit_quant_type": "nf4",
                    "bnb_4bit_compute_dtype": torch.bfloat16,
                },
                components_to_quantize=["transformer", "text_encoder"],
            )
        self._pipe = QwenImageEditPlusPipeline.from_pretrained(MODEL_REPOS[self.name], **kwargs).to("cuda")
        # VAE 디코딩을 타일로 나눈다 — 2026-09-15 L4(24GB, 실사용 22GiB) 에서 40 step 확산은 들어갔지만 1024×1632 디코딩이
        # 612MiB 를 더 달라다 CUDA OOM 으로 죽었다(#544 Task 7, `autoencoder_kl_qwenimage` normalize).
        self._pipe.vae.enable_tiling()

    def edit(self, req: EditRequest) -> Image.Image:
        if self._pipe is None:
            raise RuntimeError("load() 를 먼저 불러야 합니다")
        import torch

        steps, guidance = resolve(req, QWEN_DEFAULTS)
        result = self._pipe(
            image=req.images, prompt=req.prompt, negative_prompt=" ",
            width=req.width, height=req.height,
            num_inference_steps=steps, true_cfg_scale=guidance,
            generator=torch.Generator("cuda").manual_seed(req.seed),
        )
        return result.images[0]


MODELS: dict[str, type] = {KleinModel.name: KleinModel, QwenModel.name: QwenModel}


def model_by_name(name: str) -> CardGenModel:
    try:
        factory = MODELS[name]
    except KeyError:
        raise ValueError(f"모르는 모델 {name!r} — 가능: {sorted(MODELS)}") from None
    return factory()
