"""A saved or interrupted experiment must never spend another model request."""

import importlib
from pathlib import Path

import pytest


async def test_existing_attempt_blocks_generation(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "tools"))
    preview = importlib.import_module("preview_diary_space_action")
    from daengs_backend.services.walk_diary.writing import provider

    async def unexpected(*args, **kwargs):
        raise AssertionError("a saved attempt must not reach the provider")

    monkeypatch.setattr(provider, "generate_card_prose", unexpected)
    for status in ("started", "accepted", "failed"):
        preview.save(tmp_path / "action-attempt.json", {"status": status})
        with pytest.raises(ValueError, match="no automatic retry"):
            await preview.generate([], {}, tmp_path)
