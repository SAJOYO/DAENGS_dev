"""앵커와 변이 — 판정기에 먹이기 전에 모양이 맞는지. 모델 없이 돈다."""

from __future__ import annotations

from daengs_evals.profile_fitness.anchors import (
    ANCHORS,
    ANCHORS_BY_SET,
    OBSERVATIONS,
    evaluate_anchor,
    observe,
)
from daengs_evals.profile_fitness.mutations import (
    MUTATIONS,
    build_mutation_cases,
    inject_record,
    inject_stereotype,
    mention_only,
    reorder,
    verbose,
)
from daengs_evals.profile_fitness.profiles import Profile
from daengs_evals.profile_fitness.questions import Question
from daengs_evals.profile_fitness.rubric import ProfileDiffVerdict


def verdict(**kwargs: object) -> ProfileDiffVerdict:
    base: dict[str, object] = {
        "differences": [],
        "profile_attributable": [],
        "unstated_facts": [],
        "stereotype_leaps": [],
        "changed": False,
        "change_justified": "none",
        "confidence": "high",
        "note": "",
    }
    base.update(kwargs)
    return ProfileDiffVerdict.model_validate(base)


# ---------------------------------------------------------------------------
# 앵커
# ---------------------------------------------------------------------------


def test_every_anchor_states_at_least_one_expectation_on_a_known_observation() -> None:
    for a in ANCHORS:
        assert a.expectations, a.anchor_id
        for exp in a.expectations:
            assert exp.item in OBSERVATIONS, f"{a.anchor_id}: {exp.item}"


def test_anchor_sets_are_disjoint_and_both_nonempty() -> None:
    dev = {a.anchor_id for a in ANCHORS_BY_SET["dev"]}
    holdout = {a.anchor_id for a in ANCHORS_BY_SET["holdout"]}
    assert dev and holdout and not (dev & holdout)
    assert dev | holdout == {a.anchor_id for a in ANCHORS}


def test_anchor_ids_are_unique() -> None:
    ids = [a.anchor_id for a in ANCHORS]
    assert len(ids) == len(set(ids))


def test_anchor_answers_do_not_leak_our_labels() -> None:
    """답변 문장에 'reactive' 같은 골드가 들어 있으면 판정기가 그걸 읽는다."""
    for a in ANCHORS:
        blob = a.question + a.answer_a + a.answer_b
        for leaked in ("reactive", "invariant", "probe", "sensitive_to", "changed", "fabricated"):
            assert leaked not in blob, f"{a.anchor_id}: {leaked}"


def test_observe_maps_verdict_to_binary_cells() -> None:
    v = verdict(
        differences=["x"], stereotype_leaps=["통념"], changed=True, change_justified="stereotype"
    )
    assert observe(v) == {
        "changed": 1,
        "profile": 0,
        "fabricated": 0,
        "stereotype": 1,
        "abstained": 0,
    }
    assert observe(verdict(confidence="low"))["abstained"] == 1


def test_evaluate_anchor_reports_only_failed_expectations() -> None:
    a = next(x for x in ANCHORS if x.anchor_id == "dev_age_split")
    good = evaluate_anchor(
        a, {"changed": 1, "profile": 1, "fabricated": 0, "stereotype": 0, "abstained": 0}
    )
    assert good["passed"]
    bad = evaluate_anchor(
        a, {"changed": 1, "profile": 1, "fabricated": 1, "stereotype": 0, "abstained": 0}
    )
    assert bad["failed"] == ["fabricated == 0"]


# ---------------------------------------------------------------------------
# 변이 연산자
# ---------------------------------------------------------------------------

BASE = "4개월령이라면 짧게 여러 번 나눠 나가는 편이 좋아요. 성장기라 평지 위주로 다녀오세요."
DOG = {"breed": "치와와", "age_months": 4}


def test_verbose_keeps_the_original_intact() -> None:
    out = verbose(BASE, DOG)
    assert out.startswith(BASE) and len(out) > len(BASE)


def test_mention_only_prefixes_the_breed() -> None:
    assert mention_only(BASE, DOG) == "치와와시군요! " + BASE
    assert mention_only(BASE, None).startswith("강아지시군요!")


def test_reorder_keeps_every_sentence() -> None:
    out = reorder(BASE, DOG)
    assert out != BASE
    assert sorted(out.split(". ")) == sorted(BASE.rstrip(".").split(". ") + [""]) or set(
        BASE.split()
    ) == set(out.split())


def test_inject_record_adds_a_claim_absent_from_every_profile() -> None:
    out = inject_record(BASE, DOG, claim_index=0)
    assert "간 수치" in out and BASE[1:] in out


def test_inject_stereotype_uses_the_breed_and_changes_the_advice() -> None:
    out = inject_stereotype(BASE, DOG)
    assert "치와와는 원래" in out and "엄격하게" in out


def test_every_mutation_declares_an_observation_expectation() -> None:
    for m in MUTATIONS:
        assert m.expectations
        for exp in m.expectations:
            assert exp.item in OBSERVATIONS


def test_build_mutation_cases_uses_only_answered_profiled_baseline_cells() -> None:
    q = Question(
        question_id="pf_walk_01",
        query="산책?",
        kind="reactive",
        tier="coarse",
        arms=["toy_puppy", "large_senior"],
        sensitive_to=["age_months"],
        author="t",
    )
    p = Profile(profile_id="toy_puppy", label="a", dog=DOG, visible_to=["general"], axis="size_age")
    n = Profile(profile_id="none", label="n", dog=None, visible_to=["general"], axis="absent")
    cells = [
        {
            "question_id": "pf_walk_01",
            "arm": "toy_puppy",
            "run": 0,
            "status": "ANSWERED",
            "message": BASE,
        },
        {
            "question_id": "pf_walk_01",
            "arm": "toy_puppy",
            "run": 1,
            "status": "ANSWERED",
            "message": BASE,
        },
        {
            "question_id": "pf_walk_01",
            "arm": "none",
            "run": 0,
            "status": "ANSWERED",
            "message": BASE,
        },
        {
            "question_id": "pf_walk_01",
            "arm": "toy_puppy",
            "run": 0,
            "status": "FAILED",
            "message": "",
        },
    ]
    cases = build_mutation_cases(
        cells, {"pf_walk_01": q}, {"toy_puppy": p, "none": n}, per_mutation=4
    )
    assert len(cases) == len(MUTATIONS)  # 쓸 수 있는 셀이 하나뿐
    assert all(c.original == BASE and c.mutated != BASE for c in cases)
    assert len({c.case_id for c in cases}) == len(cases)
