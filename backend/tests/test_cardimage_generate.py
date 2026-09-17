import dataclasses
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


def test_october_sends_face_hidden_prompt():
    """10월 유령 천 틀은 본문에 얼굴이 없다 — 공통 앞부분이 가면 천 위에 얼굴이 합성된다(09-16)."""
    eng, jd = FakeEngine(), FakeJudge([5])
    generate.generate_card(
        photo=_photo(), content_type="image/jpeg", month=10, dog_name="네오",
        engine=eng, judge=jd, base_dir=CARDIMAGE, open_months=frozenset({10}), judge_min=3,
    )
    prompt = eng.calls[0]["prompt"]
    assert "26OCT" in prompt and "ghost-sheet" in prompt
    assert "do not draw the dog's face" in prompt
    assert "in the main illustration is replaced by the dog from image 2" not in prompt


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


# ── generate_cards: 한 요청에 여러 장 (#572 Task 4) ──────────────────────


class _FailSecondCallEngine(FakeEngine):
    """두 번째 호출만 실패하는 엔진 — "한 장 실패해도 나머지는 돌려준다" 테스트용."""

    def generate(self, *, template_png, photo_jpeg, prompt, seed=None):
        self.calls.append({"template": template_png, "photo": photo_jpeg, "prompt": prompt, "seed": seed})
        if len(self.calls) == 2:
            raise EngineError("upstream", "두 번째 호출 실패")
        return self.outputs[0]


def test_generate_cards_makes_each_card_with_a_different_seed():
    """L4 는 한 번에 2장부터 CUDA OOM 이다(#557 E2) — 반드시 순차 호출이어야 한다."""
    eng = FakeEngine()
    cards = generate.generate_cards(
        count=2, photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오",
        engine=eng, judge=None, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3,
        rng=random.Random(0),
    )
    assert len(cards) == 2
    assert cards[0].seed != cards[1].seed
    assert {cards[0].seed, cards[1].seed} == set(catalog.get(4).seeds)
    assert len(eng.calls) == 2      # 한 번에 두 장이 아니라 두 번 부른다
    assert eng.calls[0]["seed"] != eng.calls[1]["seed"]


def test_generate_cards_keeps_going_when_one_attempt_fails():
    """두 장 중 한 장이 실패해도 나머지 한 장은 돌려준다 — 사용자가 고를 게 남는다."""
    eng = _FailSecondCallEngine()
    cards = generate.generate_cards(
        count=2, photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오",
        engine=eng, judge=None, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3,
        rng=random.Random(0),
    )
    assert len(cards) == 1
    assert len(eng.calls) == 2  # 둘 다 시도는 했다 — 두 번째만 실패했다


def test_generate_cards_raises_last_exception_when_all_fail():
    """전부 실패하면(고를 게 하나도 없으면) 마지막 예외를 그대로 올린다."""
    eng = FakeEngine(error=EngineError("upstream", "x"))
    with pytest.raises(EngineError) as exc_info:
        generate.generate_cards(
            count=2, photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오",
            engine=eng, judge=None, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3,
            rng=random.Random(0),
        )
    assert exc_info.value.code == "upstream"
    assert len(eng.calls) == 2  # 둘 다 시도했다 — 첫 실패에서 멈추지 않는다


def test_generate_cards_no_retry_even_with_low_judge_score():
    """카드가 여럿이면(count>1) 검수 점수가 낮아도 재시도하지 않는다 — 이미 여러 장을 만드는
    것 자체가 재시도의 대안이고, 재시도를 더하면 최악 2×count 번 돈이 나간다."""
    eng, jd = FakeEngine(), FakeJudge([1, 1])
    cards = generate.generate_cards(
        count=2, photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오",
        engine=eng, judge=jd, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3,
        rng=random.Random(0),
    )
    assert len(cards) == 2 and len(eng.calls) == 2  # 점수가 낮아도 카드당 한 번뿐이다


def test_generate_cards_caps_to_distinct_seed_pool(monkeypatch, caplog):
    """#572 Task 4 fix round 1 Important 2 — 겹치지 않는 seed 가 count 보다 적으면 있는 만큼만
    만든다(같은 seed 두 번은 완전히 같은 이미지 두 장에 돈을 두 번 내는 것이다). 4월 풀은
    {2,3}(2개) 인데, 하나만 검증된 것처럼 흉내 낸다."""
    monkeypatch.setitem(catalog._CARDS, 4, dataclasses.replace(catalog.get(4), seeds=(7,)))
    eng = FakeEngine()
    with caplog.at_level("WARNING"):
        cards = generate.generate_cards(
            count=2, photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오",
            engine=eng, judge=None, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3,
            rng=random.Random(0),
        )
    assert len(cards) == 1 and cards[0].seed == 7
    assert len(eng.calls) == 1
    assert "distinct seed" in caplog.text


def test_plan_seeds_caps_to_pool_size_instead_of_repeating():
    """4월 풀은 {2,3}(2개) 뿐이다 — 5장을 부탁해도 2개만, 중복 없이 돌려준다."""
    seeds = generate.plan_seeds(4, 5, random.Random(0))
    assert len(seeds) == 2 and set(seeds) == set(catalog.get(4).seeds)


def test_generate_cards_with_seed_and_count_over_one_raises():
    """#572 Task 4 fix round 1 controller ruling C — 조용히 한 장으로 줄이지 않고 알린다."""
    with pytest.raises(ValueError, match="count"):
        generate.generate_cards(
            count=2, photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오",
            engine=FakeEngine(), judge=None, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3,
            seed=3,
        )


def test_generate_cards_single_count_keeps_generate_card_behavior():
    """`count=1` 이면 옛 `generate_card` 와 같다 — 재시도가 살아 있다."""
    eng, jd = FakeEngine([png(color=(1, 1, 1)), png(color=(2, 2, 2))]), FakeJudge([2, 4])
    cards = generate.generate_cards(
        count=1, photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오",
        engine=eng, judge=jd, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3,
        rng=random.Random(0),
    )
    assert len(cards) == 1 and cards[0].attempts == 2 and len(eng.calls) == 2


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
