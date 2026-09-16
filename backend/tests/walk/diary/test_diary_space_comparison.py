"""The comparison must not spend another call on a completed or interrupted run."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def comparison(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "tools"))
    return importlib.import_module("compare_diary_space_writers")


async def test_resume_skips_completed_failed_and_interrupted(comparison, tmp_path):
    spec = {"cases": [{"index": 1}, {"index": 2}]}
    for name, status in (
        ("case-1-all_materials.json", "completed"),
        ("case-1-optional_details.json", "failed"),
        ("case-2-all_materials.json", "started"),
    ):
        comparison.save(tmp_path / name, {"status": status})
    called = []

    async def writer(case, variant, spec, path):
        called.append((case["index"], variant))
        comparison.save(path, {"status": "completed"})

    await comparison.run(spec, tmp_path, writer)
    await comparison.run(spec, tmp_path, writer)
    assert called == [(2, "optional_details")]


def test_render_preserves_answer_and_escapes_markup(comparison, tmp_path):
    spec = {"cases": [{"index": 1, "label": "숲 배경", "payload": {"materials": []}}]}
    text = '<script>alert("x")</script> 숲 근처에 공원이 있었다.'
    comparison.save(
        tmp_path / "case-1-all_materials.json",
        {"status": "completed", "requests": [{}], "answer": {"text": text}},
    )
    summary = comparison.render(spec, tmp_path)
    assert summary["model_requests"] == 1
    assert summary["completed_variants"] == 1
    page = (tmp_path / "preview.html").read_text(encoding="utf-8")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "숲 근처에 공원이 있었다." in page
    assert "생성 미완료 · not_run" in page
