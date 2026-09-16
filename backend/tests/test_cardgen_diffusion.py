"""모델 선택과 기본값 (D-078). GPU·가중치 없이 도는 부분만 — 실제 추론은 Cloud Run 에서 확인한다.

Qwen-Image-Edit-2511 은 09-15 L4 결과가 깨져 D-078 에서 뺐다 — 후보는 FLUX.2-klein-4B 하나다 (#557)."""

import pytest
from PIL import Image

from daengs_cardgen.diffusion import KLEIN_DEFAULTS, MODEL_REPOS, KleinModel, model_by_name, resolve
from daengs_cardgen.models import EditRequest


def _req(**over) -> EditRequest:
    base = {"images": [Image.new("RGB", (8, 8))], "prompt": "p", "seed": 1, "width": 1024, "height": 1632}
    base.update(over)
    return EditRequest(**base)


def test_model_by_name_knows_only_klein() -> None:
    assert MODEL_REPOS == {"klein-4b": "black-forest-labs/FLUX.2-klein-4B"}
    assert isinstance(model_by_name("klein-4b"), KleinModel)
    with pytest.raises(ValueError, match="klein-4b"):
        model_by_name("qwen-edit-2511")


def test_resolve_uses_model_defaults_unless_request_overrides() -> None:
    assert resolve(_req(), KLEIN_DEFAULTS) == (4, 1.0)
    assert resolve(_req(steps=8, guidance=2.5), KLEIN_DEFAULTS) == (8, 2.5)


def test_edit_before_load_is_a_clear_error() -> None:
    with pytest.raises(RuntimeError, match="load"):
        KleinModel().edit(_req())
