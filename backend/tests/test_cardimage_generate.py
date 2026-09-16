import io
import random
from pathlib import Path

import pytest
from cardimage_fakes import FakeEngine, FakeJudge, png
from PIL import Image

from daengs_cardimage import catalog, generate
from daengs_cardimage import judge as judge_mod
from daengs_cardimage import photo as photo_mod
from daengs_cardimage.engine import EngineError

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


def test_september_uses_its_own_template_outfit_and_title():
    eng, jd = FakeEngine(), FakeJudge([5])
    out = generate.generate_card(
        photo=_photo(), content_type="image/jpeg", month=9, dog_name="네오",
        engine=eng, judge=jd, base_dir=CARDIMAGE, open_months=frozenset({4, 9}), judge_min=3,
    )
    assert out.month == 9 and out.title == "CHUSEOK 네오"
    prompt = eng.calls[0]["prompt"]
    assert "26SEP" in prompt and "SEPTEMBER SPECIAL" in prompt and "hanbok" in prompt
    assert "wears nothing" not in prompt          # 9월은 한복을 입는다 — 4월 문장이 섞이면 안 된다
    sent_template = Image.open(io.BytesIO(eng.calls[0]["template"]))
    ref = Image.open(CARDIMAGE / "9_harvest_moon_template.webp").convert("RGB")
    assert sent_template.size == ref.size and sent_template.getpixel((500, 800)) == ref.getpixel((500, 800))


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


def test_generated_card_records_the_seed_it_used():
    """#572 Task 3a — 뽑은 seed 가 카드에 남고, 엔진이 실제로 그 값을 받는다."""
    eng, jd = FakeEngine(), FakeJudge([5])
    out = _run(eng, jd, rng=random.Random(0))
    assert out.seed in catalog.get(4).seeds
    assert eng.calls[0]["seed"] == out.seed


def test_generate_card_without_rng_still_works():
    """`rng` 를 안 넘기면(운영 경로) 내부에서 새 `random.Random()` 을 만들어 쓴다."""
    out = _run(FakeEngine(), FakeJudge([5]))
    assert out.seed in catalog.get(4).seeds


def test_low_likeness_retries_with_a_different_seed():
    """#572 fix round 1 F2 — 같은 seed 로 재시도하면 cardgen 엔진에서는 무의미한 재생성이다.
    `pick_seeds` 가 서로 다른 두 값을 미리 뽑아 두고, 두 번째 시도는 그 다른 값을 쓴다."""
    eng = FakeEngine([png(color=(1, 1, 1)), png(color=(2, 2, 2))])
    jd = FakeJudge([2, 4])
    out = _run(eng, jd, rng=random.Random(0))
    assert out.attempts == 2
    assert eng.calls[0]["seed"] != eng.calls[1]["seed"]
    assert {eng.calls[0]["seed"], eng.calls[1]["seed"]} <= set(catalog.get(4).seeds)
    assert out.seed == eng.calls[1]["seed"]


def test_explicit_seed_is_used_verbatim_and_skips_retry(monkeypatch):
    """#572 fix round 1 F1 — 호출자가 seed 를 못박으면 pick_seeds 를 부르지 않고 재시도도 없다."""

    def _boom(*args, **kwargs):
        raise AssertionError("pick_seeds 가 불렸다 — 명시한 seed 를 무시했다")

    monkeypatch.setattr(catalog, "pick_seeds", _boom)
    # 검수 점수를 낮게 둬서, 재시도가 있었다면 걸렸을 조건을 일부러 만든다.
    eng, jd = FakeEngine(), FakeJudge([1])
    out = _run(eng, jd, seed=999)
    assert out.attempts == 1
    assert out.seed == 999
    assert len(eng.calls) == 1 and eng.calls[0]["seed"] == 999
