import io
from pathlib import Path

import pytest
from cardimage_fakes import FakeEngine, FakeJudge, png
from PIL import Image

from daengs_backend.services.cardimage import catalog, generate
from daengs_backend.services.cardimage import judge as judge_mod
from daengs_backend.services.cardimage import photo as photo_mod
from daengs_backend.services.cardimage.engine import EngineError

CARDIMAGE = Path(__file__).resolve().parents[2] / "cardimage"


def _photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (3000, 4000), (230, 220, 200)).save(buf, "JPEG")
    return buf.getvalue()


def _run(engine, judge=None, **kw):
    return generate.generate_card(
        photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오",
        engine=engine, judge=judge, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3, **kw,
    )


def test_happy_path_one_attempt_titled_output():
    eng, jd = FakeEngine(), FakeJudge([5])
    out = _run(eng, jd)
    assert out.attempts == 1 and out.judge.likeness == 5 and out.title == "BLOSSOM 네오"
    assert Image.open(io.BytesIO(out.png)).size == (994, 1582)
    assert eng.calls[0]["prompt"].count("26APR") == 1
    assert Image.open(io.BytesIO(eng.calls[0]["photo"])).size == (1200, 1600)   # 1600 으로 줄여 보냈다


def test_low_likeness_retries_once_and_keeps_better():
    eng, jd = FakeEngine([png(color=(1, 1, 1)), png(color=(2, 2, 2))]), FakeJudge([2, 4])
    out = _run(eng, jd)
    assert out.attempts == 2 and out.judge.likeness == 4 and len(eng.calls) == 2


def test_two_low_scores_returns_best_of_two():
    eng, jd = FakeEngine([png(color=(1, 1, 1)), png(color=(2, 2, 2))]), FakeJudge([2, 1])
    out = _run(eng, jd)
    assert out.attempts == 2 and out.judge.likeness == 2


def test_judge_failure_is_not_fatal():
    out = _run(FakeEngine(), FakeJudge(error=judge_mod.JudgeError("boom")))
    assert out.judge is None and out.attempts == 1


def test_no_judge_means_single_attempt():
    out = _run(FakeEngine(), None)
    assert out.judge is None and out.attempts == 1


def test_engine_no_key_becomes_card_image_unavailable():
    with pytest.raises(generate.CardImageUnavailable):
        _run(FakeEngine(error=EngineError("no_key", "x")))


def test_engine_upstream_error_propagates_unchanged():
    with pytest.raises(EngineError) as exc_info:
        _run(FakeEngine(error=EngineError("upstream", "x")))
    assert exc_info.value.code == "upstream"


def test_tie_keeps_first_attempt():
    eng, jd = FakeEngine([png(color=(1, 1, 1)), png(color=(2, 2, 2))]), FakeJudge([2, 2])
    out = _run(eng, jd)
    assert out.attempts == 2 and out.judge.likeness == 2
    assert Image.open(io.BytesIO(out.png)).getpixel((0, 0)) == (1, 1, 1)


def test_second_attempt_judge_failure_keeps_first():
    eng = FakeEngine([png(color=(1, 1, 1)), png(color=(2, 2, 2))])
    jd = FakeJudge(scores=[2, judge_mod.JudgeError("boom")])
    out = _run(eng, jd)
    assert out.attempts == 2 and out.judge.likeness == 2
    assert Image.open(io.BytesIO(out.png)).getpixel((0, 0)) == (1, 1, 1)


def test_closed_month_raises():
    with pytest.raises(catalog.MonthNotOpenError):
        generate.generate_card(photo=_photo(), content_type="image/jpeg", month=9, dog_name="x", engine=FakeEngine(),
                               judge=None, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3)


def test_bad_photo_raises_photo_error():
    with pytest.raises(photo_mod.PhotoError):
        generate.generate_card(photo=b"nope", content_type="image/jpeg", month=4, dog_name="x", engine=FakeEngine(),
                               judge=None, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3)


def test_missing_template_dir_is_unavailable(tmp_path):
    with pytest.raises(generate.CardImageUnavailable):
        generate.generate_card(photo=_photo(), content_type="image/jpeg", month=4, dog_name="x", engine=FakeEngine(),
                               judge=None, base_dir=tmp_path, open_months=frozenset({4}), judge_min=3)
