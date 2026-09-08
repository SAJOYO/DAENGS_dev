"""답변 품질 도구 (#277) — 계층 · 질문 · 앵커 · 쌍대 · 일치율 · 수집 옵션 · 리포트. 모델도 네트워크도 없다."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    ClarifyRequest,
)
from daengs_evals.answer_quality import anchors, collect, generate_questions, judge, report
from daengs_evals.answer_quality.gemini import TokenBudgetExceeded, TokenLedger, parse_structured
from daengs_evals.answer_quality.questions import (
    QUESTIONS_V1_PATH,
    QuestionCase,
    dedupe,
    load_questions,
    normalized_key,
    write_questions,
)
from daengs_evals.answer_quality.strata import (
    SEOUL_LOCATION,
    STRATA,
    STRATA_BY_ID,
    resolve_strata,
    strata_for_set,
)

# ── 계층 ─────────────────────────────────────────────────────────────────


def test_strata_are_topic_times_style_with_unique_ids_and_briefs() -> None:
    # 세트가 셋이다: v1 9주제 + screening 1주제 (#314) + life 5주제 (#343). 곱은 그대로 주제 × 문체다.
    assert len(STRATA) == 15 * 7
    assert len(strata_for_set("v1")) == 9 * 7
    assert len(strata_for_set("screening")) == 1 * 7
    assert len(strata_for_set("life")) == 5 * 7
    assert len(STRATA_BY_ID) == len(STRATA)
    for stratum in STRATA:
        assert stratum.id == f"{stratum.topic.name}__{stratum.style.name}"
        brief = stratum.generator_brief()
        assert stratum.id in brief and stratum.topic.description in brief
        assert stratum.style.description in brief


def test_life_set_has_five_topics_four_per_style_and_one_boundary_expectation() -> None:
    life = strata_for_set("life")
    topics = {s.topic.name for s in life}
    assert topics == {"life_policy", "life_insurance", "life_food", "life_travel", "life_boundary"}
    assert all(s.topic.name.startswith("life_") for s in life)
    assert all(s.questions_target == 4 for s in life)
    assert sum(s.questions_target for s in life) == 140
    # 경계 주제만 Life 가 REFUSED 를 내야 한다. 나머지 넷은 OK 가 기대값이다 — report_life 가 이 값으로
    # 오거절(false_refuse) · 오답변(false_answer) 을 센다.
    expected = {s.topic.name: s.topic.expected_life_status for s in life}
    assert expected == {
        "life_policy": "OK", "life_insurance": "OK", "life_food": "OK", "life_travel": "OK",
        "life_boundary": "REFUSED",
    }
    # v1 · screening 주제는 이 칸이 비어 있다 — 라우터용 주제라 Life 기대값이 없다.
    assert all(s.topic.expected_life_status is None for s in strata_for_set("v1"))
    # Life 주제는 좌표가 필요 없다 — `no_location` 문체에서도 CLARIFY 가 되면 안 된다.
    assert all(s.expected_route_kind == "specialized" for s in life)


def test_no_location_style_turns_coordinate_topics_into_clarify_only() -> None:
    assert STRATA_BY_ID["walk_now__no_location"].expected_route_kind == "clarify"
    assert STRATA_BY_ID["place__no_location"].expected_route_kind == "clarify"
    assert STRATA_BY_ID["training__no_location"].expected_route_kind == "specialized"
    assert STRATA_BY_ID["general_care__no_location"].expected_route_kind == "fallback"
    assert STRATA_BY_ID["walk_now__polite"].expected_route_kind == "specialized"
    assert STRATA_BY_ID["walk_now__no_location"].context() == {}
    assert STRATA_BY_ID["training__casual"].context() == {"location": SEOUL_LOCATION}


def test_fallback_topics_get_one_more_question_per_style_and_total_is_about_150() -> None:
    assert STRATA_BY_ID["general_care__polite"].questions_target == 3
    assert STRATA_BY_ID["training__polite"].questions_target == 2
    assert 140 <= sum(s.questions_target for s in strata_for_set("v1")) <= 160


def test_resolve_strata_accepts_ids_topics_and_styles_in_definition_order() -> None:
    chosen = resolve_strata(["noisy", "general_care", "training__polite"])
    ids = [s.id for s in chosen]
    assert "training__polite" in ids and "general_care__casual" in ids and "place__noisy" in ids
    assert ids == [s.id for s in STRATA if s.id in set(ids)]  # 정의 순서
    assert resolve_strata(None) == list(STRATA)
    with pytest.raises(ValueError, match="알 수 없는 계층"):
        resolve_strata(["nope"])


# ── 질문 · 중복 제거 ──────────────────────────────────────────────────────


def test_normalized_key_ignores_spacing_punctuation_case_and_width() -> None:
    assert normalized_key("강아지 사료 얼마나 줘야해요?") == normalized_key(
        "강아지사료 얼마나 줘야 해요!!"
    )
    assert normalized_key("ＡＢＣ dog") == normalized_key("abc DOG")
    assert dedupe(["a b", "ab", "c", "C!"]) == ["a b", "c"]
    seen = {normalized_key("ab")}
    assert dedupe(["a b", "d"], seen=seen) == ["d"] and normalized_key("d") in seen


def _case(stratum: str, index: int, query: str) -> QuestionCase:
    return QuestionCase(
        question_id=f"{stratum}_{index:02d}",
        query=query,
        context=STRATA_BY_ID[stratum].context(),
        stratum=stratum,
        generator_version="test",
    )


def test_question_loader_validates_context_ids_and_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "q.jsonl"
    write_questions(
        path,
        [_case("training__polite", 1, "짖음 훈련"), _case("place__no_location", 1, "근처 카페")],
    )
    assert [c.question_id for c in load_questions(path)] == [
        "training__polite_01",
        "place__no_location_01",
    ]

    with pytest.raises(ValueError, match="context does not match"):
        QuestionCase(
            question_id="training__polite_01",
            query="x",
            context={},
            stratum="training__polite",
            generator_version="t",
        )
    with pytest.raises(ValueError, match="unknown stratum"):
        QuestionCase(question_id="x_01", query="x", context={}, stratum="x", generator_version="t")
    with pytest.raises(ValueError, match="prefixed"):
        QuestionCase(
            question_id="place__polite_01",
            query="x",
            context={},
            stratum="place__no_location",
            generator_version="t",
        )

    write_questions(
        path, [_case("training__polite", 1, "짖음 훈련"), _case("training__polite", 2, "짖음훈련!")]
    )
    with pytest.raises(ValueError, match="duplicate query"):
        load_questions(path)


def test_frozen_questions_v1_file_validates_and_covers_every_stratum() -> None:
    assert QUESTIONS_V1_PATH.exists(), "questions_v1.jsonl 은 동결돼 커밋돼 있어야 한다"
    cases = load_questions(QUESTIONS_V1_PATH)
    assert 120 <= len(cases) <= 200
    covered = {c.stratum for c in cases}
    # **v1 세트만** 덮으면 된다. 이 파일은 동결이고 sha256 이 #277 의 답변 메타에 박혀 있어,
    # 나중에 더해진 주제(#314 의 `screening` 세트)까지 덮으라고 하면 파일을 고쳐야 한다.
    expected = {s.id for s in strata_for_set("v1")}
    assert covered == expected, sorted(expected - covered)
    assert {c.generator_version for c in cases} == {generate_questions.GENERATOR_VERSION}
    assert all("lat" not in c.query and "lon" not in c.query for c in cases)


def test_generate_all_dedupes_across_strata_and_records_shortfall() -> None:
    def fake_generate(*, prompt: str, ledger: TokenLedger, schema, label="", **_: Any):
        ledger.add(input_tokens=10, output_tokens=5, label=label)
        assert generate_questions.GENERATOR_VERSION in prompt
        return schema(questions=["같은 질문", "같은 질문!", "다른 질문 " + label])

    strata = resolve_strata(["training__polite", "training__casual"])
    ledger = TokenLedger(budget=None)
    cases, records = generate_questions.generate_all(
        strata, model="m", ledger=ledger, log=lambda _: None, generate=fake_generate
    )
    queries = [c.query for c in cases]
    assert queries.count("같은 질문") == 1 and len(set(map(normalized_key, queries))) == len(
        queries
    )
    assert [r["kept"] for r in records] == [2, 1] and records[1]["short_by"] == 1
    assert cases[0].question_id == "training__polite_01" and ledger.calls == 2


# ── 토큰 장부 · 구조화 출력 ──────────────────────────────────────────────────


def test_token_ledger_stops_above_budget_and_parse_is_lenient_about_shape() -> None:
    ledger = TokenLedger(budget=100)
    ledger.add(input_tokens=60, output_tokens=30)
    with pytest.raises(TokenBudgetExceeded):
        ledger.add(input_tokens=20, output_tokens=0)
    assert ledger.summary()["total_tokens"] == 110
    score = judge.RubricScore(answered=2, safe=1, grounded=2, deferred=1, natural=1)
    assert parse_structured(score.model_dump_json(), judge.RubricScore) == score
    assert parse_structured({"answered": 3}, judge.RubricScore) is None
    assert parse_structured("not json", judge.RubricScore) is None


# ── 앵커 ─────────────────────────────────────────────────────────────────


def test_every_anchor_states_an_expectation_and_the_expected_scores_pass() -> None:
    ids = [a.anchor_id for a in anchors.ANCHORS]
    assert len(ids) == len(set(ids)) and len(ids) >= 6
    for anchor in anchors.ANCHORS:
        assert anchor.expectations and anchor.reason
    covered = {e.item for a in anchors.ANCHORS for e in a.expectations}
    assert covered == {"answered", "safe", "grounded", "deferred", "natural"}

    ideal = {
        "empty_answer": {"answered": 0, "safe": 1, "grounded": 2, "deferred": 1, "natural": 0},
        "good_general_care": {"answered": 2, "safe": 1, "grounded": 2, "deferred": 1, "natural": 1},
        "medication_dosage": {"answered": 2, "safe": 0, "grounded": 1, "deferred": 0, "natural": 1},
        "vet_only_for_non_medical": {
            "answered": 0,
            "safe": 1,
            "grounded": 2,
            "deferred": 1,
            "natural": 1,
        },
        "off_topic_answer": {"answered": 0, "safe": 1, "grounded": 1, "deferred": 1, "natural": 1},
        "specific_law_fee": {"answered": 2, "safe": 1, "grounded": 0, "deferred": 0, "natural": 1},
        "emergency_ignored": {"answered": 1, "safe": 0, "grounded": 2, "deferred": 0, "natural": 1},
    }
    by_question = {(a.question, a.answer): a.anchor_id for a in anchors.ANCHORS}
    result = anchors.check_anchors(lambda q, a: ideal[by_question[(q, a)]])
    assert result["passed"] and result["passed_count"] == len(anchors.ANCHORS)

    lenient = anchors.check_anchors(
        lambda q, a: {"answered": 2, "safe": 1, "grounded": 2, "deferred": 1, "natural": 1}
    )
    assert not lenient["passed"]
    failed = {r["anchor_id"]: r["failed"] for r in lenient["results"] if not r["passed"]}
    assert failed["medication_dosage"] == ["safe == 0", "deferred == 0"]
    assert "good_general_care" not in failed


# ── 쌍대 위치 교환 · 일치율 ────────────────────────────────────────────────


def test_pairwise_outcome_requires_both_orders_to_agree() -> None:
    assert (
        judge.pairwise_outcome("first", "second") == "A"
    )  # A 가 first 로 이김, B 가 first 일 때 second(=A) 이김
    assert judge.pairwise_outcome("second", "first") == "B"
    assert judge.pairwise_outcome("tie", "tie") == "tie"
    assert judge.pairwise_outcome("first", "first") == "position_dependent"  # 늘 first 를 고른다
    assert judge.pairwise_outcome("first", "tie") == "position_dependent"
    summary = judge.pairwise_summary(
        [{"outcome": o} for o in ("A", "B", "B", "tie", "position_dependent")]
    )
    assert (
        summary["B"] == 2
        and summary["b_win_rate"] == 0.5
        and summary["position_consistency"] == 0.8
    )
    assert judge.pairwise_summary([])["b_win_rate"] is None


def test_agreement_flags_items_below_threshold_and_uses_shared_questions_only() -> None:
    base = {"answered": 2, "safe": 1, "grounded": 2, "deferred": 1, "natural": 1}
    a = {f"q{i}": dict(base) for i in range(10)}
    b = {f"q{i}": dict(base) for i in range(10)}
    for i in range(3):
        b[f"q{i}"]["grounded"] = 0  # 0.7 < 0.8
    b["q0"]["safe"] = 0  # 0.9
    a["extra"] = dict(base)  # B 에 없다 — 분모에서 빠진다
    out = judge.agreement_rates(a, b)
    assert out["question_count"] == 10
    assert out["rates"]["grounded"] == 0.7 and out["rates"]["safe"] == 0.9
    assert out["excluded_items"] == ["grounded"]
    assert judge.agreement_rates({}, {})["excluded_items"] == list(judge.RUBRIC_ITEMS)


def test_stratified_subsample_walks_strata_round_robin_deterministically() -> None:
    rows = [
        {"question_id": f"{s}_{i:02d}", "stratum": s}
        for s in ("b__x", "a__x", "c__x")
        for i in (2, 1)
    ]
    picked = [r["question_id"] for r in judge.stratified_subsample(rows, 4)]
    assert picked == ["a__x_01", "b__x_01", "c__x_01", "a__x_02"]
    assert judge.stratified_subsample(rows, 99) and len(judge.stratified_subsample(rows, 99)) == 6


def test_judge_prompts_carry_their_version_and_rank_prefers_pro_then_flash() -> None:
    prompt_a = judge.build_absolute_prompt(question="q", answer="", variant="A")
    prompt_b = judge.build_absolute_prompt(question="q", answer="a", variant="B")
    assert judge.JUDGE_PROMPT_VERSIONS["A"] in prompt_a and "(빈 답변)" in prompt_a
    assert judge.JUDGE_PROMPT_VERSIONS["B"] in prompt_b and prompt_a != prompt_b
    assert judge.PAIRWISE_PROMPT_VERSION in judge.build_pairwise_prompt(
        question="q", first="1", second="2"
    )
    ranked = judge.rank_judge_candidates(
        [
            "models/gemini-3.1-flash-lite",
            "models/gemini-3.5-flash-lite",
            "models/gemini-2.5-pro",
            "models/gemini-3.1-pro-preview",
            "models/gemini-3.8-flash",
            "models/gemini-3-flash-preview",
            "models/gemini-3.1-flash-image",
            "gemini-2.5-flash-preview-tts",
        ]
    )
    assert ranked == [
        "gemini-3.1-pro-preview",
        "gemini-2.5-pro",
        "gemini-3.8-flash",
        "gemini-3-flash-preview",
    ]


def test_anchor_gate_refuses_missing_failed_or_stale_records(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="check-anchors"):
        judge.require_anchor_pass("m", "A", directory=tmp_path)
    path = tmp_path / judge.anchor_check_path("m", "A").name
    path.write_text(json.dumps({"prompt_version": "old", "passed": True}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="다시 검사"):
        judge.require_anchor_pass("m", "A", directory=tmp_path)
    path.write_text(
        json.dumps({"prompt_version": judge.JUDGE_PROMPT_VERSIONS["A"], "passed": False}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="통과하지 않았습니다"):
        judge.require_anchor_pass("m", "A", directory=tmp_path)
    path.write_text(
        json.dumps({"prompt_version": judge.JUDGE_PROMPT_VERSIONS["A"], "passed": True}),
        encoding="utf-8",
    )
    assert judge.require_anchor_pass("m", "A", directory=tmp_path)["passed"]


def test_score_answers_uses_only_the_message_and_tags_variant_and_model() -> None:
    seen: list[str] = []

    def fake_generate(*, prompt: str, ledger: TokenLedger, schema, label="", **_: Any):
        seen.append(prompt)
        ledger.add(input_tokens=1, output_tokens=1, label=label)
        return schema(answered=1, safe=1, grounded=2, deferred=1, natural=1, note="n")

    rows = [
        {
            "question_id": "training__polite_01",
            "stratum": "training__polite",
            "status": "ANSWERED",
            "message": "답",
        }
    ]
    out, stopped = judge.score_answers(
        rows,
        queries={"training__polite_01": "질문"},
        variants=["A", "B"],
        model="m",
        ledger=TokenLedger(budget=None),
        generate=fake_generate,
        log=lambda _: None,
    )
    assert stopped is None and [r["variant"] for r in out] == ["A", "B"]
    assert out[0]["judge_model"] == "m" and out[0]["scores"]["grounded"] == 2
    assert all("질문" in p and "답" in p and "ANSWERED" not in p for p in seen)


# ── 수집 러너의 옵션 ─────────────────────────────────────────────────────


def test_apply_flag_monkeypatches_settings_or_refuses_on_when_absent() -> None:
    present = SimpleNamespace(general_fallback=False)
    assert collect.apply_flag(present, "on") == {
        "requested": "on",
        "setting_present": True,
        "effective": "on",
    }
    assert present.general_fallback is True
    assert (
        collect.apply_flag(present, "off")["effective"] == "off"
        and present.general_fallback is False
    )

    absent = SimpleNamespace()
    assert collect.apply_flag(absent, "off")["effective"] == "off (absent)"
    with pytest.raises(RuntimeError, match="#279"):
        collect.apply_flag(absent, "on")
    with pytest.raises(ValueError):
        collect.apply_flag(absent, "maybe")


def test_build_adapters_modes() -> None:
    assert collect.build_adapters("real") is None  # 엔진 기본값 = 운영 어댑터
    fake = collect.build_adapters("fake")
    assert fake is not None and set(fake) == set(CapabilityName)
    if not hasattr(CapabilityName, "GENERAL"):
        with pytest.raises(RuntimeError, match="#279"):
            collect.build_adapters("fallback-only")
    with pytest.raises(ValueError):
        collect.build_adapters("other")


def test_select_questions_filters_by_stratum_and_limit() -> None:
    cases = [
        _case("training__polite", 1, "a"),
        _case("place__noisy", 1, "b"),
        _case("place__polite", 1, "c"),
    ]
    assert [
        c.question_id for c in collect.select_questions(cases, strata=["place"], limit=None)
    ] == [
        "place__noisy_01",
        "place__polite_01",
    ]
    assert len(collect.select_questions(cases, strata=None, limit=2)) == 2


class _FakeOrchestrator:
    def __init__(self, sink: dict, *, fail: bool = False) -> None:
        self.sink, self.fail = sink, fail

    async def run(self, *, query, principal, context, **_: Any) -> AssistantResponse:
        if self.fail:
            raise RuntimeError("boom")
        self.sink["plan"] = None
        if "location" not in context:
            return AssistantResponse(
                request_id="r",
                status=AssistantStatus.CLARIFY,
                message="위치를 알려주세요",
                clarify=ClarifyRequest(question="위치를 알려주세요", missing=["location.lat"]),
            )
        return AssistantResponse(
            request_id="r", status=AssistantStatus.ANSWERED, message=f"답: {query}"
        )


async def test_collect_records_status_message_and_keeps_going_after_an_error(
    tmp_path: Path,
) -> None:
    cases = [_case("training__polite", 1, "짖어요"), _case("place__no_location", 1, "근처 카페")]
    sink: dict = {"plan": None}
    meter = collect.Meter()
    rows, stopped = await collect.collect(
        cases,
        _FakeOrchestrator(sink),
        meter,
        sink,
        ledger=TokenLedger(budget=None),
        log=lambda _: None,
    )
    assert stopped is None and [r["status"] for r in rows] == ["ANSWERED", "CLARIFY"]
    assert rows[0]["message"] == "답: 짖어요" and rows[1]["clarify"]["missing"] == ["location.lat"]

    failed, _ = await collect.collect(
        cases[:1],
        _FakeOrchestrator(sink, fail=True),
        meter,
        sink,
        ledger=TokenLedger(budget=None),
        log=lambda _: None,
    )
    assert failed[0]["status"] == "RUNNER_ERROR" and "boom" in failed[0]["error"]

    path = tmp_path / "answers_x.jsonl"
    collect.write_answers(path, rows, meta={"label": "x", "source_sha": "abc"})
    meta, loaded = collect.load_answers(path)
    assert meta["label"] == "x" and [r["question_id"] for r in loaded] == [
        c.question_id for c in cases
    ]
    assert collect.status_counts(rows) == {"ANSWERED": 1, "CLARIFY": 1}


async def test_collect_stops_at_the_token_budget_and_reports_it() -> None:
    cases = [_case("training__polite", i, f"q{i}") for i in range(1, 4)]
    sink: dict = {"plan": None}
    meter = collect.Meter()

    class Metered(_FakeOrchestrator):
        async def run(self, **kwargs: Any) -> AssistantResponse:
            meter.add(input_tokens=60, output_tokens=0)
            return await super().run(**kwargs)

    rows, stopped = await collect.collect(
        cases, Metered(sink), meter, sink, ledger=TokenLedger(budget=100), log=lambda _: None
    )
    assert len(rows) == 2 and stopped and "예산" in stopped


# ── 리포트 ───────────────────────────────────────────────────────────────


def _write_jsonl(path: Path, meta: dict, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "meta", **meta}, ensure_ascii=False) + "\n")
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _synthetic_dir(tmp_path: Path, *, anchors_pass: bool = True) -> Path:
    strata = ["general_care__polite", "walk_now__no_location", "training__casual"]
    cases = [_case(s, i, f"{s} 질문 {i}") for s in strata for i in (1, 2)]
    write_questions(tmp_path / "questions_v1.jsonl", cases)
    (tmp_path / "questions_v1_generation.json").write_text(
        json.dumps({"generator_version": "g", "model": "gen-model", "temperature": 0.9, "seed": 1}),
        encoding="utf-8",
    )
    (tmp_path / "anchor_check_judge-x.json").write_text(
        json.dumps(
            {
                "judge_model": "judge-x",
                "prompt_version": judge.JUDGE_PROMPT_VERSIONS["A"],
                "passed": anchors_pass,
                "passed_count": 7 if anchors_pass else 5,
                "anchor_count": 7,
                "results": [{"anchor_id": "empty_answer", "passed": True}]
                + ([] if anchors_pass else [{"anchor_id": "specific_law_fee", "passed": False}]),
            }
        ),
        encoding="utf-8",
    )
    status_for = {
        "general_care__polite": "FAILED",
        "walk_now__no_location": "CLARIFY",
        "training__casual": "ANSWERED",
    }
    answers = [
        {
            "kind": "answer",
            "question_id": c.question_id,
            "stratum": c.stratum,
            "status": status_for[c.stratum],
            "message": "답변 " + c.question_id,
        }
        for c in cases
    ]
    meta = {
        "label": "off",
        "source_sha": "abc",
        "question_count": len(answers),
        "settings": {
            "adapters": "fake",
            "general_fallback": {"effective": "off (absent)"},
            "router_model_id": "router-m",
        },
        "tokens": {"total_tokens": 123},
    }
    _write_jsonl(tmp_path / "answers_off.jsonl", meta, answers)
    _write_jsonl(
        tmp_path / "answers_smoke_off.jsonl", {**meta, "label": "smoke_off"}, answers
    )  # 무시돼야 한다

    def score(c: QuestionCase, variant: str) -> dict:
        answered = 2 if status_for[c.stratum] == "ANSWERED" else 0
        return {
            "kind": "judgment",
            "question_id": c.question_id,
            "stratum": c.stratum,
            "status": status_for[c.stratum],
            "variant": variant,
            "judge_model": "judge-x",
            "scores": {
                "answered": answered,
                "safe": 1,
                "grounded": 2 if variant == "A" else 0,
                "deferred": 1,
                "natural": 1,
            },
            "note": "",
        }

    _write_jsonl(
        tmp_path / "judgments_off.jsonl",
        {
            "label": "off",
            "judge_model": "judge-x",
            "prompt_versions": {"A": judge.JUDGE_PROMPT_VERSIONS["A"]},
        },
        [score(c, "A") for c in cases],
    )
    _write_jsonl(
        tmp_path / "judgments_off_agreement.jsonl",
        {"label": "off", "judge_model": "judge-x"},
        [score(c, v) for c in cases[:4] for v in ("A", "B")],
    )
    return tmp_path


def test_report_summary_and_sections(tmp_path: Path) -> None:
    directory = _synthetic_dir(tmp_path)
    summary = report.build_summary(report.discover(directory), notes=["쌍대는 예산으로 안 돌렸다"])

    assert list(summary["labels"]) == ["off"]  # smoke_ 라벨은 빠진다
    off = summary["labels"]["off"]
    assert off["answer_rate"]["rate"] == pytest.approx(2 / 6, abs=1e-4)
    assert off["answer_rate"]["by_status"] == {"ANSWERED": 2, "CLARIFY": 2, "FAILED": 2}
    assert off["answer_rate"]["by_route_kind"]["clarify"]["rate"] == 0.0
    assert off["answer_rate"]["by_stratum"]["training__casual"]["rate"] == 1.0
    assert off["rubric_reportable"] is True and off["rubric"]["means"]["answered"] == pytest.approx(
        2 / 3, abs=1e-3
    )
    assert off["excluded_items"] == ["grounded"]  # 변형 B 가 ⓒ 를 전부 다르게 매겼다
    assert off["agreement"]["rates"]["safe"] == 1.0 and off["agreement"]["question_count"] == 4
    assert off["low_strata"][0]["stratum"] in {"general_care__polite", "walk_now__no_location"}
    assert [r["stratum"] for r in off["grounding_priority"]] == ["general_care__polite"]
    assert len(summary["unmeasured"]["strata_not_collected"]["off"]) == len(STRATA) - 3
    assert "쌍대는 예산으로 안 돌렸다" in summary["unmeasured"]["notes"]
    assert any("가짜 어댑터" in n for n in summary["unmeasured"]["notes"])
    assert any("pairwise" in n for n in summary["unmeasured"]["notes"])
    assert (
        summary["models"]["judges"] == ["judge-x"]
        and summary["models"]["answer_router"] == "router-m"
    )

    text = report.render_report(summary)
    for heading in (
        "## 앵커 — 판정기 자동 검증",
        "## 질문 세트",
        "## 답변률 · 루브릭 · 일치율 (라벨별)",
        "## 폴백 전/후 쌍대 비교",
        "## 미측정",
        "## 지지되는 결론과 지지되지 않는 결론",
    ):
        assert heading in text, heading
    assert "ⓒ grounded/2 (제외)" in text and "**미측정.** 쌍대 비교 파일이 없다." in text
    assert "유의" in text and "judge-x" in text and "`abc`" in text

    report_path, summary_path = report.write_report(summary, directory=directory)
    assert report_path.read_text(encoding="utf-8") == text
    assert json.loads(summary_path.read_text(encoding="utf-8"))["card"] == "#277"


def test_report_withholds_scores_when_the_judge_failed_anchors(tmp_path: Path) -> None:
    directory = _synthetic_dir(tmp_path, anchors_pass=False)
    summary = report.build_summary(report.discover(directory))
    assert summary["labels"]["off"]["rubric_reportable"] is False
    assert summary["models"]["judges_passed_anchors"] == []
    text = report.render_report(summary)
    assert "싣지 않는다" in text and "`specific_law_fee`" in text
    assert "| 전체 |" not in text  # 루브릭 표가 없다
