"""리포트 집계 — 합성 판정 파일로. 모델 없이 돈다."""

from __future__ import annotations

from typing import Any

from daengs_evals.profile_fitness.report import (
    condition_rates,
    failure_counts,
    inequality,
    item_means,
    paraphrase_stability,
    render_markdown,
    render_svg,
    summarize,
    unmeasured,
    variant_agreement,
)


def row(
    qid: str,
    cond: str,
    kind: str,
    *,
    changed: int,
    fab: int = 0,
    st: int = 0,
    profile: int | None = None,
    tier: str = "coarse",
    items: dict[str, int] | None = None,
    failures: list[str] | None = None,
    skipped: str | None = None,
    pos: bool = False,
    para: str | None = None,
) -> dict[str, Any]:
    if profile is None:
        profile = 1 if changed else 0
    return {
        "kind": "judgment",
        "pair_id": f"{qid}|{cond}",
        "question_id": qid,
        "question_kind": kind,
        "tier": tier,
        "condition": cond,
        "arm_a": "a",
        "arm_b": "b",
        "capability": "general",
        "paraphrase_of": para,
        "position_dependent": pos,
        "observation": {
            "changed": changed,
            "profile": profile,
            "fabricated": fab,
            "stereotype": st,
            "abstained": 0,
        },
        "score": {"items": items or {}, "failures": failures or [], "skipped": skipped},
    }


def healthy_rows() -> list[dict[str, Any]]:
    rows = []
    for i in range(6):
        q = f"pf_r_{i:02d}"
        rows.append(row(q, "noise", "reactive", changed=0, skipped="control"))
        rows.append(
            row(
                q,
                "ablation",
                "reactive",
                changed=1,
                items={"responsiveness": 2, "no_fabrication": 1},
            )
        )
        rows.append(
            row(
                q,
                "contrast",
                "reactive",
                changed=1,
                items={"responsiveness": 2, "no_fabrication": 1},
            )
        )
    for i in range(4):
        q = f"pf_i_{i:02d}"
        rows.append(row(q, "noise", "invariant", changed=0, skipped="control"))
        rows.append(
            row(
                q,
                "contrast",
                "invariant",
                changed=int(i == 0),
                profile=0,
                items={"invariance": int(i != 0), "no_fabrication": 1},
            )
        )
    return rows


META = {
    "kind": "meta",
    "cells_label": "t",
    "judge_model": "gpt-5.4-2026-03-05",
    "variant": "A",
    "prompt_version": "profile-fitness-diff-ko-v1a",
    "hygiene": {},
    "generation": {"router": {"model": "g"}, "general": {"model": "g", "prompt_version": "v3"}},
    "anchor_record": {},
    "skipped_pairs": [{"pair_id": "x", "reason": "not_answered"}],
}


def test_inequality_holds_on_a_healthy_run() -> None:
    rows = healthy_rows()
    rates = condition_rates(rows)
    assert rates["N_noise"]["point"] == 0.0
    assert rates["S_invariant"]["point"] == 0.25
    assert rates["P_ablation"]["point"] == 1.0
    ineq = inequality(rates, rows)
    assert ineq["holds"] is True
    assert ineq["paired_questions"] == 6
    assert ineq["P_minus_N"]["point"] == 1.0 and ineq["P_minus_N_ci_excludes_zero"]


def test_inequality_fails_when_ablation_does_not_move() -> None:
    """P 에서 안 움직이면 부등식이 깨진다 — 그게 이 리포트의 가장 중요한 발견이다."""
    rows = [r for r in healthy_rows() if r["condition"] != "ablation"]
    rows += [
        row(
            f"pf_r_{i:02d}",
            "ablation",
            "reactive",
            changed=0,
            items={"responsiveness": 0, "no_fabrication": 1},
            failures=["ignored"],
        )
        for i in range(6)
    ]
    ineq = inequality(condition_rates(rows), rows)
    assert ineq["holds"] is False and ineq["holds_S_lt_P"] is False


def test_rates_exclude_position_dependent_and_abstained() -> None:
    rows = [
        row("q1", "ablation", "reactive", changed=1),
        row("q2", "ablation", "reactive", changed=1, pos=True),
    ]
    rows[1]["observation"]["abstained"] = 0
    assert condition_rates(rows)["P_ablation"]["n"] == 1


def test_item_means_and_distribution() -> None:
    rows = healthy_rows()
    means = item_means(rows)["all"]
    assert means["responsiveness"]["mean"] == 2.0 and means["responsiveness"]["n"] == 12
    assert means["invariance"]["mean"] == 0.75 and means["invariance"]["distribution"] == {
        1: 3,
        0: 1,
    }
    by_kind = item_means(rows, group="question_kind")
    assert set(by_kind) == {"reactive", "invariant"}


def test_failure_counts_only_scored_pairs() -> None:
    rows = healthy_rows() + [
        row(
            "q9",
            "contrast",
            "reactive",
            changed=1,
            fab=1,
            items={"responsiveness": 2, "no_fabrication": 0},
            failures=["fabricated"],
        )
    ]
    f = failure_counts(rows)
    assert f["fabricated"] == 1 and f["ignored"] == 0
    assert f["scored_pairs"] == 6 + 6 + 4 + 1  # noise(control) 는 채점 안 됨


def test_unmeasured_counts_everything_that_did_not_yield_a_number() -> None:
    rows = healthy_rows()
    rows[0]["position_dependent"] = True
    u = unmeasured(META, rows)
    assert u["judged"] == len(rows) and u["skipped_before_judging"] == {"not_answered": 1}
    assert u["position_dependent"] == 1
    assert u["total_pairs"] == len(rows) + 1


def test_paraphrase_stability_compares_to_origin_in_same_condition() -> None:
    rows = [
        row("pf_a_01", "contrast", "reactive", changed=1),
        row("pf_a_02", "contrast", "reactive", changed=1, para="pf_a_01"),
        row("pf_b_01", "contrast", "reactive", changed=1),
        row("pf_b_02", "contrast", "reactive", changed=0, para="pf_b_01"),
    ]
    assert paraphrase_stability(rows) == {"pairs": 2, "agreement": 0.5}


def test_variant_agreement_on_shared_pairs() -> None:
    a = healthy_rows()
    b = [dict(r, observation=dict(r["observation"])) for r in a]
    b[3]["observation"]["changed"] = 1 - b[3]["observation"]["changed"]
    va = variant_agreement(a, b)
    assert va["shared_pairs"] == len(a)
    assert va["items"]["changed"]["agreement"] < 1.0
    assert va["items"]["fabricated"]["undefined"] == "single_category"  # 전부 0


def test_summary_is_provisional_without_labels_and_renders() -> None:
    s = summarize(
        META, healthy_rows(), [{"kind": "ledger", "input_tokens": 1000, "output_tokens": 300}]
    )
    assert s["provisional"] is True
    assert s["gate"]["responsiveness"]["reason"] == "not_calibrated"
    assert s["items"]["responsiveness"] == 2.0  # 잠정이지만 지우지는 않는다
    md = render_markdown(s)
    assert "잠정" in md and "N < S" in md and "종합 점수는 없다" in md
    assert "<svg" in render_svg(s)


def test_summary_withholds_items_when_labels_say_kappa_is_low() -> None:
    rows = healthy_rows()
    # 사람이 판정기와 반대로 라벨한 30건 — κ 가 낮거나 못 잰다
    labels = {}
    for r in rows[:30]:
        labels[r["pair_id"]] = {
            "pair_id": r["pair_id"],
            "changed": 1 - r["observation"]["changed"],
            "profile": 0,
            "fabricated": 0,
            "stereotype": 0,
            "responsiveness": None,
        }
    s = summarize(META, rows, [], labels=labels)
    assert s["provisional"] is True
    assert s["items"]["no_fabrication"] is None  # fabricated 는 전부 0 → 못 잼 → 보류
    assert "no_fabrication" in s["unmeasured"]


def test_unmeasured_excludes_conditions_that_were_never_collected() -> None:
    """조건을 골라 모은 실행: 잡음 쌍이 전부 셀 없음이면 '안 잰 것' 이지 '못 잰 것' 이 아니다."""
    from daengs_evals.profile_fitness.report import unmeasured

    rows = [
        {"condition": "contrast", "position_dependent": None, "observation": {"abstained": False}},
        {"condition": "ablation", "position_dependent": None, "observation": {"abstained": False}},
    ]
    meta = {
        "skipped_pairs": [
            {"pair_id": "q1|noise|a#0|a#1", "reason": "missing_cell"},
            {"pair_id": "q2|noise|a#0|a#1", "reason": "missing_cell"},
            {"pair_id": "q3|contrast|a#0|b#0", "reason": "not_answered"},
        ]
    }
    u = unmeasured(meta, rows)
    assert u["not_planned"] == {"noise": 2}
    assert u["skipped_before_judging"] == {"not_answered": 1}
    assert u["total_pairs"] == 3 and u["unmeasured_rate"] == round(1 / 3, 3)
