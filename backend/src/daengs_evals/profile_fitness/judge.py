"""판정기 — 앵커 검사 · 편향 시험 · 쌍 채점.

    uv run python -m daengs_evals.profile_fitness.judge check-anchors --set dev
    uv run python -m daengs_evals.profile_fitness.judge check-anchors --set holdout      # 버전당 한 번
    uv run python -m daengs_evals.profile_fitness.judge bias-suite --cells pf_v1
    uv run python -m daengs_evals.profile_fitness.judge score --cells pf_v1 --variant A
    uv run python -m daengs_evals.profile_fitness.judge score --cells pf_v1 --variant B --subsample 30

**순서가 규칙이다.** `score` 는 같은 판정 모델 · 변형 · 프롬프트 버전으로 `check-anchors --set dev`
가 통과한 기록이 없으면 돌지 않는다 (`answer_quality/judge.py` 의 앵커 게이트와 같은 약속).
holdout 은 프롬프트 버전당 결과 파일을 하나만 쓰고 **이미 있으면 거부**한다 — 두 번째 실행이
가능하면 그건 홀드아웃이 아니다.

**쌍마다 두 번 부른다.** (A,B) 와 (B,A). `changed` 가 뒤집히면 `position_dependent` 로 따로 세고
점수에서 뺀다 — 순서에 흔들린 판정을 점수로 옮기면 그 점수가 무엇을 뜻하는지 아무도 말할 수 없다.

**판정 모델은 OpenAI.** `calibration/hygiene.py` 가 계열 분리 · 날짜 핀을 강제한다. 생성은 전부
Gemini 이므로 판정을 같은 계열로 두면 자기편애가 남는다.

**재개된다.** 판정 파일에 이미 있는 pair_id 는 건너뛴다. 2시간짜리 채점이 90% 에서 죽어도
처음부터 돌리지 않는다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from daengs_evals.answer_quality.gemini import TokenBudgetExceeded, TokenLedger

#: 한 명령의 기본 예산. 2026-09-09 에 하루 140만 토큰을 쓴 뒤 5만으로 내렸다 — 넘길 일이면 사람이
#: --token-budget 으로 명시한다. 양방향 판정은 표본 20쌍만 (--both-orders-sample).
DEFAULT_TOKEN_BUDGET = 50_000
from daengs_evals.answer_quality.provenance import source_provenance, utc_now
from daengs_evals.answer_quality.questions import file_sha256
from daengs_evals.calibration.hygiene import require_judge_hygiene
from daengs_evals.calibration.openai_client import client as openai_client
from daengs_evals.calibration.openai_client import generate_structured
from daengs_evals.profile_fitness.anchors import (
    AnchorSet,
    PairAnchor,
    check_anchors,
    observe,
)
from daengs_evals.profile_fitness.collect import cells_path, load_cells
from daengs_evals.profile_fitness.mutations import MutationCase, build_mutation_cases
from daengs_evals.profile_fitness.pairs import Pair, build_pairs
from daengs_evals.profile_fitness.profiles import ASSETS_DIR, load_profiles, profiles_by_id
from daengs_evals.profile_fitness.questions import load_questions
from daengs_evals.profile_fitness.rubric import (
    PROMPT_VERSIONS,
    TEMPERATURE,
    ProfileDiffVerdict,
    build_prompt,
    score_pair,
)

Variant = Literal["A", "B"]
Judge = Callable[..., ProfileDiffVerdict]

#: 자기일관성 시험에서 같은 쌍을 몇 번 판정하나.
CONSISTENCY_REPEATS = 3
#: 자기일관성 · 위치 편향 시험에 쓰는 쌍 수.
BIAS_SAMPLE = 8


def _real(meta: Mapping[str, Any]) -> frozenset[str] | None:
    """이 수집에서 진짜였던 어댑터. fallback-only 면 general 뿐."""
    return frozenset({"general"}) if meta.get("adapters") == "fallback-only" else None


def _generation_models() -> list[str]:
    from daengs_backend.orchestration.adapters.general import GENERAL_MODEL_ID
    from daengs_backend.orchestration.semantic import ROUTER_MODEL_ID

    return sorted({ROUTER_MODEL_ID, GENERAL_MODEL_ID})


def _judge_model(explicit: str | None) -> str:
    if explicit:
        return explicit
    from daengs_backend.config import settings

    return settings.openai_judge_model


def _safe(name: str) -> str:
    return name.replace("/", "_").replace(":", "_")


def anchor_record_path(anchor_set: AnchorSet, model: str, variant: Variant) -> Path:
    # 프롬프트 버전이 이름에 있어야 "holdout 은 버전당 한 번" 이 성립한다 — v1a 의 기록이 v2a 를 막으면 안 된다
    return ASSETS_DIR / f"anchor_check_{anchor_set}_{PROMPT_VERSIONS[variant]}_{_safe(model)}.json"


def bias_record_path(cells_label: str, model: str, variant: Variant) -> Path:
    return ASSETS_DIR / f"bias_suite_{cells_label}_{PROMPT_VERSIONS[variant]}_{_safe(model)}.json"


def judgments_path(cells_label: str, variant: Variant) -> Path:
    return ASSETS_DIR / f"judgments_{cells_label}_{PROMPT_VERSIONS[variant]}.jsonl"


# ---------------------------------------------------------------------------
# 판정기 만들기
# ---------------------------------------------------------------------------


def make_judge(
    *, model: str, variant: Variant, ledger: TokenLedger, cli: Any = None
) -> tuple[Judge, dict[str, str]]:
    """프롬프트 조립 + 구조화 호출. 위생 검사를 통과해야 만들어진다."""
    hygiene = require_judge_hygiene(model, _generation_models())
    active = cli or openai_client()

    def judge(
        *,
        question: str,
        profile_a: Mapping[str, Any] | None,
        profile_b: Mapping[str, Any] | None,
        answer_a: str,
        answer_b: str,
        label: str = "",
    ) -> ProfileDiffVerdict:
        prompt = build_prompt(
            question=question,
            profile_a=dict(profile_a) if profile_a else None,
            profile_b=dict(profile_b) if profile_b else None,
            answer_a=answer_a,
            answer_b=answer_b,
            variant=variant,
        )
        return generate_structured(
            model=model,
            prompt=prompt,
            schema=ProfileDiffVerdict,
            temperature=TEMPERATURE,
            ledger=ledger,
            cli=active,
            label=label,
        )

    return judge, hygiene


def judge_both_orders(
    judge: Judge, pair: Pair, label: str = "", *, both: bool = True
) -> dict[str, Any]:
    """(A,B) 와 (B,A). 관찰은 (A,B) 것을 쓰되 `changed` 가 뒤집히면 position_dependent.

    `both=False` 면 (A,B) 한 번만 — 위치 편향은 표본에서만 재고 나머지는 한 방향으로 채점한다
    (토큰 절반). 그 쌍은 `position_dependent=None` 으로 남아 "안 봤다" 와 "안 뒤집혔다" 가 갈린다.
    """
    assert pair.cell_a is not None and pair.cell_b is not None
    prof_a = pair.cell_a.get("_profile")
    prof_b = pair.cell_b.get("_profile")
    ab = judge(
        question=pair.query,
        profile_a=prof_a,
        profile_b=prof_b,
        answer_a=pair.cell_a["message"],
        answer_b=pair.cell_b["message"],
        label=f"{label} ab",
    )
    if not both:
        return {
            "ab": ab.model_dump(mode="json"),
            "ba": None,
            "position_dependent": None,
            "observation": observe(ab),
            "observation_ba": None,
        }
    ba = judge(
        question=pair.query,
        profile_a=prof_b,
        profile_b=prof_a,
        answer_a=pair.cell_b["message"],
        answer_b=pair.cell_a["message"],
        label=f"{label} ba",
    )
    return {
        "ab": ab.model_dump(mode="json"),
        "ba": ba.model_dump(mode="json"),
        "position_dependent": ab.changed != ba.changed,
        "observation": observe(ab),
        "observation_ba": observe(ba),
    }


# ---------------------------------------------------------------------------
# 앵커 게이트
# ---------------------------------------------------------------------------


def require_anchor_pass(
    model: str, variant: Variant, anchor_set: AnchorSet = "dev"
) -> dict[str, Any]:
    path = anchor_record_path(anchor_set, model, variant)
    if not path.exists():
        raise RuntimeError(
            f"앵커 통과 기록이 없습니다: {path.name} — 먼저 `judge check-anchors --set {anchor_set}` 을 돌리세요"
        )
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("prompt_version") != PROMPT_VERSIONS[variant]:
        raise RuntimeError(
            f"앵커 기록의 프롬프트 버전({record.get('prompt_version')})이 지금({PROMPT_VERSIONS[variant]})과 다릅니다 — "
            "프롬프트를 고쳤으면 앵커를 다시 돌리세요"
        )
    if not record.get("passed"):
        raise RuntimeError(
            f"앵커가 통과하지 못했습니다 ({record.get('passed_count')}/{record.get('anchor_count')}) — 점수를 내지 않습니다"
        )
    return record


def run_check_anchors(
    *, anchor_set: AnchorSet, model: str, variant: Variant, budget: int, log=print
) -> Path:
    path = anchor_record_path(anchor_set, model, variant)
    if anchor_set == "holdout" and path.exists():
        raise FileExistsError(
            f"{path.name} 이 이미 있습니다. holdout 은 프롬프트 버전당 한 번입니다 — "
            "프롬프트를 고쳤으면 버전을 올리고, 새 홀드아웃을 짜세요"
        )
    ledger = TokenLedger(budget=budget, log=log)
    judge, hygiene = make_judge(model=model, variant=variant, ledger=ledger)

    def judge_anchor(a: PairAnchor) -> ProfileDiffVerdict:
        return judge(
            question=a.question,
            profile_a=a.profile_a,
            profile_b=a.profile_b,
            answer_a=a.answer_a,
            answer_b=a.answer_b,
            label=a.anchor_id,
        )

    report = check_anchors(judge_anchor, anchor_set)
    record = {
        **report,
        "judge_model": model,
        "variant": variant,
        "prompt_version": PROMPT_VERSIONS[variant],
        "hygiene": hygiene,
        "tokens": {
            "input": ledger.input_tokens,
            "output": ledger.output_tokens,
            "calls": ledger.calls,
        },
        "checked_at": utc_now(),
        "source": source_provenance(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in report["results"]:
        mark = "통과" if r["passed"] else f"실패 {r['failed']}"
        log(f"  {r['anchor_id']:42s} {mark}")
    log(f"{anchor_set}: {report['passed_count']}/{report['anchor_count']} → {path.name}")
    return path


# ---------------------------------------------------------------------------
# 편향 시험 — 변이 · 자기일관성 · 위치
# ---------------------------------------------------------------------------


def _attach_profiles(pairs: Iterable[Pair], by_id: Mapping[str, Any]) -> list[Pair]:
    """셀 dict 에 `_profile` 을 얹는다 — 판정기가 프로필을 봐야 한다."""
    out = []
    for p in pairs:
        if p.cell_a is None or p.cell_b is None:
            out.append(p)
            continue
        a = dict(p.cell_a)
        b = dict(p.cell_b)
        a["_profile"] = dict(by_id[p.arm_a].dog) if by_id[p.arm_a].dog else None
        b["_profile"] = dict(by_id[p.arm_b].dog) if by_id[p.arm_b].dog else None
        out.append(Pair(**{**asdict(p), "cell_a": a, "cell_b": b}))
    return out


def run_bias_suite(
    *, cells_label: str, model: str, variant: Variant, budget: int, per_mutation: int, log=print
) -> Path:
    meta, cells = load_cells(cells_path(cells_label))
    questions = load_questions(Path(meta["questions_path"]))
    profiles = load_profiles(Path(meta["profiles_path"]))
    q_by_id = {q.question_id: q for q in questions}
    p_by_id = profiles_by_id(profiles)

    ledger = TokenLedger(budget=budget, log=log)
    judge, hygiene = make_judge(model=model, variant=variant, ledger=ledger)

    # ① 변이 — 실제 답변에 한 가지만 바꾼다
    cases: list[MutationCase] = build_mutation_cases(
        cells, q_by_id, p_by_id, per_mutation=per_mutation
    )
    mutation_results = []
    for c in cases:
        v = judge(
            question=c.question,
            profile_a=c.profile,
            profile_b=c.profile,
            answer_a=c.original,
            answer_b=c.mutated,
            label=c.case_id,
        )
        obs = observe(v)
        failed = [str(e) for e in c.expectations if not e.holds(obs[e.item])]
        mutation_results.append(
            {
                "case_id": c.case_id,
                "mutation": c.mutation,
                "observation": obs,
                "failed": failed,
                "passed": not failed,
                "note": v.note,
            }
        )
        log(f"  변이 {c.case_id:60s} {'통과' if not failed else '실패 ' + str(failed)}")

    # ② 자기일관성 · 위치 — 실제 contrast 쌍 몇 개
    pairs = [
        p for p in build_pairs(questions, profiles, cells, conditions=("contrast",)) if p.judgeable
    ]
    pairs = _attach_profiles(pairs[:BIAS_SAMPLE], p_by_id)
    consistency, position = [], []
    for p in pairs:
        runs = [judge_both_orders(judge, p, label=p.pair_id) for _ in range(CONSISTENCY_REPEATS)]
        changed_votes = [r["observation"]["changed"] for r in runs]
        fab_votes = [r["observation"]["fabricated"] for r in runs]
        consistency.append(
            {
                "pair_id": p.pair_id,
                "changed_consistent": len(set(changed_votes)) == 1,
                "fabricated_consistent": len(set(fab_votes)) == 1,
                # 어긋났을 때 **왜** 어긋났는지 사람이 읽을 수 있게 — 앵커 기록과 같은 이유
                "notes": [r["ab"]["note"] for r in runs],
                "unstated_facts": [r["ab"]["unstated_facts"] for r in runs],
            }
        )
        position.append(
            {
                "pair_id": p.pair_id,
                "flips": sum(1 for r in runs if r["position_dependent"]),
                "runs": CONSISTENCY_REPEATS,
            }
        )
        log(
            f"  일관성 {p.question_id:26s} changed={changed_votes} fab={fab_votes} 뒤집힘={position[-1]['flips']}"
        )

    n_pairs = max(1, len(consistency))
    summary = {
        "mutations": {
            "count": len(mutation_results),
            "passed": sum(1 for r in mutation_results if r["passed"]),
            "by_mutation": {
                m: {
                    "passed": sum(
                        1 for r in mutation_results if r["mutation"] == m and r["passed"]
                    ),
                    "count": sum(1 for r in mutation_results if r["mutation"] == m),
                }
                for m in sorted({r["mutation"] for r in mutation_results})
            },
        },
        "self_consistency": {
            "pairs": len(consistency),
            "changed_rate": sum(1 for c in consistency if c["changed_consistent"]) / n_pairs,
            "fabricated_rate": sum(1 for c in consistency if c["fabricated_consistent"]) / n_pairs,
        },
        "position": {
            "pairs": len(position),
            "flip_rate": sum(r["flips"] for r in position)
            / max(1, sum(r["runs"] for r in position)),
        },
    }
    record = {
        "cells_label": cells_label,
        "judge_model": model,
        "variant": variant,
        "prompt_version": PROMPT_VERSIONS[variant],
        "hygiene": hygiene,
        "summary": summary,
        "mutation_results": mutation_results,
        "consistency": consistency,
        "position": position,
        "tokens": {
            "input": ledger.input_tokens,
            "output": ledger.output_tokens,
            "calls": ledger.calls,
        },
        "checked_at": utc_now(),
        "source": source_provenance(),
    }
    path = bias_record_path(cells_label, model, variant)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(json.dumps(summary, ensure_ascii=False))
    log(f"→ {path.name}")
    return path


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------


def _stratified(pairs: list[Pair], n: int) -> list[Pair]:
    """조건 × 종류 층에서 돌아가며 하나씩 — 부분표본이 한 조건에 쏠리지 않게. 결정론(pair_id 순)."""
    strata: dict[str, list[Pair]] = {}
    for p in sorted(pairs, key=lambda x: x.pair_id):
        strata.setdefault(f"{p.condition}|{p.kind}", []).append(p)
    out: list[Pair] = []
    keys = sorted(strata)
    while len(out) < n and any(strata[k] for k in keys):
        for k in keys:
            if strata[k] and len(out) < n:
                out.append(strata[k].pop(0))
    return out


def _existing_pair_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("kind") == "judgment":
                ids.add(row["pair_id"])
    return ids


def run_score(
    *,
    cells_label: str,
    model: str,
    variant: Variant,
    budget: int,
    subsample: int | None,
    conditions: Sequence[str],
    both_orders_sample: int = 20,
    log=print,
) -> Path:
    anchor = require_anchor_pass(model, variant, "dev")
    meta, cells = load_cells(cells_path(cells_label))
    questions = load_questions(Path(meta["questions_path"]))
    profiles = load_profiles(Path(meta["profiles_path"]))
    p_by_id = profiles_by_id(profiles)

    pairs = build_pairs(
        questions, profiles, cells, conditions=tuple(conditions), real_capabilities=_real(meta)
    )  # type: ignore[arg-type]
    judgeable = _attach_profiles([p for p in pairs if p.judgeable], p_by_id)
    skipped = [p for p in pairs if not p.judgeable]
    if subsample:
        judgeable = _stratified(judgeable, subsample)

    ledger = TokenLedger(budget=budget, log=log)
    judge, hygiene = make_judge(model=model, variant=variant, ledger=ledger)

    path = judgments_path(cells_label, variant)
    done = _existing_pair_ids(path)
    if path.exists():
        # 재개: 셀이 재시도로 바뀌었을 수 있으니 meta 의 건너뜀 목록을 새로 쓴다 — 옛 값을 두면
        # 리포트가 이미 답한 셀을 "못 잰 것" 으로 센다 (2026-09-09 실측: not_answered 50 이 그대로 남았다)
        lines = path.read_text(encoding="utf-8").splitlines()
        head = json.loads(lines[0])
        head["skipped_pairs"] = [{"pair_id": p.pair_id, "reason": p.skip_reason} for p in skipped]
        head["resumed_at"] = utc_now()
        path.write_text(
            "\n".join([json.dumps(head, ensure_ascii=False), *lines[1:]]) + "\n", encoding="utf-8"
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        head = {
            "kind": "meta",
            "cells_label": cells_label,
            "cells_sha256": file_sha256(cells_path(cells_label)),
            "judge_model": model,
            "variant": variant,
            "prompt_version": PROMPT_VERSIONS[variant],
            "hygiene": hygiene,
            # 어느 앵커 통과 기록에 기대어 점수를 냈는지 — 나중에 그 기록이 바뀌면 이 판정도 의심 대상이다
            "anchor_record": {
                "file": anchor_record_path("dev", model, variant).name,
                "passed_count": anchor.get("passed_count"),
                "anchor_count": anchor.get("anchor_count"),
                "checked_at": anchor.get("checked_at"),
            },
            "conditions": list(conditions),
            "subsample": subsample,
            "generation": {"router": meta["router"], "general": meta["general"]},
            "skipped_pairs": [{"pair_id": p.pair_id, "reason": p.skip_reason} for p in skipped],
            "started_at": utc_now(),
            "source": source_provenance(),
        }
        with path.open("w", encoding="utf-8") as h:
            h.write(json.dumps(head, ensure_ascii=False) + "\n")
    todo = [p for p in judgeable if p.pair_id not in done]
    log(
        f"쌍 {len(pairs)} · 판정 가능 {len(judgeable)} · 이미 {len(done)} · 이번에 {len(todo)} · 건너뜀 {len(skipped)}"
    )

    try:
        for i, p in enumerate(todo, start=1):
            # 위치 편향은 앞 N쌍만 양방향으로 잰다 — 나머지는 한 방향 (토큰 절반)
            result = judge_both_orders(
                judge, p, label=p.pair_id, both=(i + len(done)) <= both_orders_sample
            )
            ab = ProfileDiffVerdict.model_validate(result["ab"])
            score = score_pair(ab, kind=p.kind, is_control=p.is_control, in_scope=True)
            if result["position_dependent"] and score.scored:
                score_dict = {"items": {}, "failures": [], "skipped": "position_dependent"}
            else:
                score_dict = {
                    "items": score.items,
                    "failures": list(score.failures),
                    "skipped": score.skipped,
                }
            row = {
                "kind": "judgment",
                "pair_id": p.pair_id,
                "question_id": p.question_id,
                "question_kind": p.kind,
                "tier": p.tier,
                "condition": p.condition,
                "arm_a": p.arm_a,
                "arm_b": p.arm_b,
                "capability": p.capability,
                "paraphrase_of": p.paraphrase_of,
                "orders": {"ab": result["ab"], "ba": result["ba"]},
                "position_dependent": result["position_dependent"],
                "observation": result["observation"],
                "score": score_dict,
                "judged_at": utc_now(),
            }
            with path.open("a", encoding="utf-8") as h:
                h.write(json.dumps(row, ensure_ascii=False) + "\n")
            obs = result["observation"]
            log(
                f"  [{i:>3}/{len(todo)}] {p.condition:8s} {p.question_id:26s} changed={obs['changed']} "
                f"fab={obs['fabricated']} st={obs['stereotype']} {'위치뒤집힘' if result['position_dependent'] else ''} "
                f"→ {score_dict['items'] or score_dict['skipped']}"
            )
    except TokenBudgetExceeded as exc:
        log(
            f"⚠ {exc} — 지금까지의 판정은 파일에 남아 있습니다. --resume 없이 같은 명령을 다시 돌리면 이어서 합니다"
        )
    # 이 실행의 장부. 재개하면 한 줄 더 쌓이고, 리포트가 합쳐서 "1000쌍당 비용" 을 낸다.
    with path.open("a", encoding="utf-8") as h:
        h.write(
            json.dumps(
                {
                    "kind": "ledger",
                    "judge_model": model,
                    "variant": variant,
                    "pairs_judged": len(todo),
                    "input_tokens": ledger.input_tokens,
                    "output_tokens": ledger.output_tokens,
                    "calls": ledger.calls,
                    "finished_at": utc_now(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    log(f"토큰 {ledger.input_tokens}/{ledger.output_tokens} (호출 {ledger.calls}) → {path.name}")
    return path


# ---------------------------------------------------------------------------
# 판정기 대 판정기 — 같은 답변, 다른 프롬프트 버전
# ---------------------------------------------------------------------------


def _load_judgments(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("kind") == "judgment":
                out[r["pair_id"]] = r
    return out


def compare_versions(a_path: Path, b_path: Path) -> dict[str, Any]:
    """같은 셀을 두 프롬프트 버전으로 채점한 파일을 쌍 단위로 맞댄다. 판정기 효과만 분리된다.

    2026-09-09: v1a→v2 는 같은 90쌍에서 S 100%→29%, 뒤집힘 12%→8%. v2→v3 는 32쌍(크레딧 소진으로
    중단)에서 뒤집힘 6%→19% — 가설(대칭 필드 + 기권)이 지지되지 않았다. 이 함수가 그 표를 낸다.
    """
    a, b = _load_judgments(a_path), _load_judgments(b_path)
    shared = sorted(set(a) & set(b))

    def rate(rows: list[dict[str, Any]], cond: str, kind: str | None = None) -> dict[str, Any]:
        rs = [
            r
            for r in rows
            if r["condition"] == cond
            and (kind is None or r["question_kind"] == kind)
            and not r["position_dependent"]
        ]
        k = sum(int(r["observation"]["changed"]) for r in rs)
        return {"k": k, "n": len(rs), "rate": round(k / len(rs), 3) if rs else None}

    def side(rows: list[dict[str, Any]]) -> dict[str, Any]:
        flips = sum(1 for r in rows if r["position_dependent"])
        abst = sum(1 for r in rows if r["observation"].get("abstained"))
        return {
            "N_noise": rate(rows, "noise"),
            "S_invariant": rate(rows, "contrast", "invariant"),
            "P_ablation": rate(rows, "ablation"),
            "reactive_contrast": rate(rows, "contrast", "reactive"),
            "position_flips": {
                "k": flips,
                "n": len(rows),
                "rate": round(flips / len(rows), 3) if rows else None,
            },
            "abstained": abst,
        }

    ra, rb = [a[k] for k in shared], [b[k] for k in shared]
    changed_transitions: dict[str, int] = {}
    flip_transitions: dict[str, int] = {}
    for k in shared:
        ct = f"{a[k]['observation']['changed']}->{b[k]['observation']['changed']}"
        changed_transitions[ct] = changed_transitions.get(ct, 0) + 1
        ft = f"{int(a[k]['position_dependent'])}->{int(b[k]['position_dependent'])}"
        flip_transitions[ft] = flip_transitions.get(ft, 0) + 1
    return {
        "a": {"file": a_path.name, "pairs": len(a), **side(ra)},
        "b": {"file": b_path.name, "pairs": len(b), **side(rb)},
        "shared_pairs": len(shared),
        "changed_transitions": changed_transitions,
        "flip_transitions": flip_transitions,
    }


def render_compare(c: dict[str, Any]) -> str:
    def row(name: str, s: dict[str, Any]) -> str:
        f = lambda d: (
            f"{d['rate'] * 100:.0f}% ({d['k']}/{d['n']})" if d["rate"] is not None else "—"
        )
        return (
            f"| {name} | {f(s['N_noise'])} | {f(s['S_invariant'])} | {f(s['P_ablation'])} | "
            f"{f(s['reactive_contrast'])} | {f(s['position_flips'])} | {s['abstained']} |"
        )

    return "\n".join(
        [
            f"공유 쌍 {c['shared_pairs']} (A {c['a']['pairs']} · B {c['b']['pairs']})",
            "",
            "| 판정 | N 잡음 | S 특이도 | P 절제 | 본 비교 | 위치 뒤집힘 | 기권 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            row(c["a"]["file"], c["a"]),
            row(c["b"]["file"], c["b"]),
            "",
            f"changed 전이 (A→B): {c['changed_transitions']}",
            f"뒤집힘 전이 (A→B): {c['flip_transitions']}",
        ]
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개체 적합성 판정기")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--judge-model", default=None, help="기본 settings.openai_judge_model")
        p.add_argument("--variant", choices=("A", "B"), default="A")
        p.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)

    p_anchor = sub.add_parser("check-anchors")
    p_anchor.add_argument("--set", choices=("dev", "holdout"), default="dev")
    common(p_anchor)

    p_bias = sub.add_parser("bias-suite")
    p_bias.add_argument("--cells", required=True, help="cells_<label>.jsonl 의 label")
    p_bias.add_argument("--per-mutation", type=int, default=4)
    common(p_bias)

    p_cmp = sub.add_parser("compare", help="같은 셀을 두 프롬프트 버전으로 채점한 파일을 맞댄다")
    p_cmp.add_argument("--a", required=True)
    p_cmp.add_argument("--b", required=True)

    p_score = sub.add_parser("score")
    p_score.add_argument("--cells", required=True)
    p_score.add_argument("--subsample", type=int, default=None)
    p_score.add_argument("--conditions", nargs="+", default=["contrast", "ablation", "noise"])
    p_score.add_argument(
        "--both-orders-sample",
        type=int,
        default=20,
        help="앞 N쌍만 (A,B)·(B,A) 둘 다. 나머지는 한 방향",
    )
    common(p_score)

    args = parser.parse_args(argv)
    if args.command == "compare":
        print(render_compare(compare_versions(Path(args.a), Path(args.b))))
        return 0
    model = _judge_model(args.judge_model)
    if args.command == "check-anchors":
        run_check_anchors(
            anchor_set=args.set, model=model, variant=args.variant, budget=args.token_budget
        )
    elif args.command == "bias-suite":
        run_bias_suite(
            cells_label=args.cells,
            model=model,
            variant=args.variant,
            budget=args.token_budget,
            per_mutation=args.per_mutation,
        )
    elif args.command == "score":
        run_score(
            cells_label=args.cells,
            model=model,
            variant=args.variant,
            budget=args.token_budget,
            subsample=args.subsample,
            conditions=args.conditions,
            both_orders_sample=args.both_orders_sample,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
