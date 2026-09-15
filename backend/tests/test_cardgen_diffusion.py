"""모델 선택과 기본값 (D-078). GPU·가중치 없이 도는 부분만 — 실제 추론은 Cloud Run 에서 확인한다."""

import pytest
from PIL import Image

from daengs_cardgen.diffusion import (
    KLEIN_DEFAULTS,
    MODEL_REPOS,
    QWEN_DEFAULTS,
    KleinModel,
    QwenModel,
    model_by_name,
    resolve,
)
from daengs_cardgen.models import EditRequest


def _req(**over) -> EditRequest:
    base = {"images": [Image.new("RGB", (8, 8))], "prompt": "p", "seed": 1, "width": 1024, "height": 1632}
    base.update(over)
    return EditRequest(**base)


def test_model_by_name_knows_exactly_the_two_candidates() -> None:
    assert set(MODEL_REPOS) == {"klein-4b", "qwen-edit-2511"}
    assert isinstance(model_by_name("klein-4b"), KleinModel)
    assert isinstance(model_by_name("qwen-edit-2511"), QwenModel)
    with pytest.raises(ValueError, match="klein-4b"):
        model_by_name("joyai")


def test_resolve_uses_model_defaults_unless_request_overrides() -> None:
    assert resolve(_req(), KLEIN_DEFAULTS) == (4, 1.0)
    assert resolve(_req(), QWEN_DEFAULTS) == (40, 4.0)
    assert resolve(_req(steps=28, guidance=4.5), QWEN_DEFAULTS) == (28, 4.5)


def test_qwen_quant_comes_from_env_and_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARDGEN_QWEN_QUANT", raising=False)
    assert QwenModel().quant == "nf4"
    monkeypatch.setenv("CARDGEN_QWEN_QUANT", "none")
    assert QwenModel().quant == "none"
    with pytest.raises(ValueError, match="nf4"):
        QwenModel(quant="fp8")


def test_edit_before_load_is_a_clear_error() -> None:
    with pytest.raises(RuntimeError, match="load"):
        KleinModel().edit(_req())


def test_qwen_load_enables_vae_tiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """1024×1632 디코딩이 L4 에서 CUDA OOM 이었다(#544 Task 7) — 로드가 끝나면 VAE 타일링이 켜져 있어야 한다.

    GPU·diffusers 없이 호출 순서만 본다: 가짜 torch·diffusers 를 sys.modules 에 끼운다(load 가 함수 안에서 import)."""
    import sys
    import types

    calls: list[str] = []

    class FakeVae:
        def enable_tiling(self) -> None:
            calls.append("tiling")

    class FakePipe:
        vae = FakeVae()

        def to(self, device: str) -> "FakePipe":
            calls.append(f"to:{device}")
            return self

    class FakePipeline:
        @staticmethod
        def from_pretrained(repo: str, **kwargs) -> FakePipe:
            calls.append(f"from:{repo}")
            return FakePipe()

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(bfloat16="bf16"))
    monkeypatch.setitem(sys.modules, "diffusers", types.SimpleNamespace(QwenImageEditPlusPipeline=FakePipeline))
    QwenModel(quant="none").load()
    assert calls == ["from:Qwen/Qwen-Image-Edit-2511", "to:cuda", "tiling"]
