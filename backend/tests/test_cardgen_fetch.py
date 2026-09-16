"""가중치 받기 잡이 서비스가 읽는 파일만 받는지 (D-078). 네트워크는 부르지 않는다."""

import huggingface_hub
import pytest

from daengs_cardgen import fetch
from daengs_cardgen.diffusion import MODEL_REPOS


def test_fetch_downloads_only_pipeline_files(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_snapshot_download(repo_id: str, **kwargs) -> str:
        calls.append((repo_id, kwargs))
        return f"/models/{repo_id}"

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    assert fetch.main(["klein-4b"]) == 0
    # 한 번에 한 파일 — FUSE 가 쓰는 파일을 메모리에 스테이징하므로 병렬이면 잡 메모리를 넘는다.
    assert calls == [(MODEL_REPOS["klein-4b"], {"allow_patterns": ["model_index.json", "*/*"], "max_workers": 1})]


def test_fetch_rejects_unknown_model() -> None:
    assert fetch.main(["joyai"]) == 2
