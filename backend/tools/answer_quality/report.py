"""`report_v1.md` · `summary_v1.json` — 디렉터리의 파일에서 결정론으로 (#277).

    uv run python -m tools.answer_quality.report --note "쌍대는 예산으로 안 돌림 — ..."

모델을 부르지 않는다. `evals/answer_quality/` 를 훑어 있는 것만 싣고 없는 것은 **미측정**에 적는다:

    questions_v1.jsonl                 동결 질문 (필수)
    questions_v1_generation.json       생성 메타
    anchor_check_<model>.json          판정기 앵커 검사 — 통과하지 못한 모델의 점수는 싣지 않는다
    answers_<label>.jsonl              수집 결과 (`smoke_*` 라벨은 뺀다)
    judgments_<label>.jsonl            절대 채점 (변형 A)
    judgments_<label>_agreement.jsonl  부분표본의 변형 A·B — 항목별 일치율
    pairwise_<a>_vs_<b>.jsonl          쌍대 위치 교환 비교

통계적 유의성은 주장하지 않는다. 계층은 고르게 생성한 것이라 실사용 분포가 아니고, 숫자는
"어느 계층이 약한가" 를 가리키는 데까지만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.answer_quality.collect import NOT_ANSWERED_STATUSES, load_answers
from tools.answer_quality.judge import RUBRIC_ITEMS, RUBRIC_MAX, agreement_rates, pairwise_summary
from tools.answer_quality.provenance import source_provenance, utc_now
from tools.answer_quality.questions import ASSETS_DIR, load_questions
from tools.answer_quality.strata import STRATA, STRATA_BY_ID

REPORT_NAME = "report_v1.md"
SUMMARY_NAME = "summary_v1.json"
BENCHMARK_ID = "answer-quality-v1"
CARD = "#277"
#: 대표 케이스로 보여 줄 낮은 계층 수와 문구 길이.
_LOW_STRATA = 5
_EXCERPT = 120
_SMOKE_PREFIX = "smoke"


# ---------------------------------------------------------------------------
# 적재
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return load_answers(path)  # 같은 "meta 행 + 행들" 모양이다


def _labels(directory: Path, prefix: str, suffix: str = "") -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in sorted(directory.glob(f"{prefix}_*.jsonl")):
        label = path.stem[len(prefix) + 1 :]
        if suffix:
            if not label.endswith(suffix):
                continue
            label = label[: -len(suffix)]
        elif prefix == "judgments" and label.endswith("_agreement"):
            continue
        if label.startswith(_SMOKE_PREFIX):
            continue
        out[label] = path
    return out


def discover(directory: Path = ASSETS_DIR) -> dict[str, Any]:
    questions = load_questions(directory / "questions_v1.jsonl")
    generation_path = directory / "questions_v1_generation.json"
    generation = (
        json.loads(generation_path.read_text(encoding="utf-8")) if generation_path.exists() else None
    )
    anchor_checks = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("anchor_check_*.json"))
    ]
    answers = {label: _read_jsonl(path) for label, path in _labels(directory, "answers").items()}
    judgments = {label: _read_jsonl(path) for label, path in _labels(directory, "judgments").items()}
    agreements = {
        label: _read_jsonl(path)
        for label, path in _labels(directory, "judgments", "_agreement").items()
    }
    pairwise = {
        path.stem[len("pairwise_") :]: _read_jsonl(path)
        for path in sorted(directory.glob("pairwise_*.jsonl"))
        if not path.stem[len("pairwise_") :].startswith(_SMOKE_PREFIX)
    }
    return {
        "questions": questions,
        "generation": generation,
        "anchor_checks": anchor_checks,
        "answers": answers,
        "judgments": judgments,
        "agreements": agreements,
        "pairwise": pairwise,
    }


# ---------------------------------------------------------------------------
# 지표 — 순수 함수
# ---------------------------------------------------------------------------


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def answer_rate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """답변률 = FAILED · CLARIFY · RUNNER_ERROR 가 아닌 비율. 전체 · 계층별 · 상태별 · 묶음별."""
    answered = [row for row in rows if str(row["status"]) not in NOT_ANSWERED_STATUSES]
    by_stratum: dict[str, dict[str, Any]] = {}
    by_kind_total: Counter[str] = Counter()
    by_kind_hit: Counter[str] = Counter()
    for stratum in STRATA:
        mine = [row for row in rows if row["stratum"] == stratum.id]
        if not mine:
            continue
        hits = sum(1 for row in mine if str(row["status"]) not in NOT_ANSWERED_STATUSES)
        by_stratum[stratum.id] = {
            "count": len(mine),
            "answered": hits,
            "rate": _rate(hits, len(mine)),
            "route_kind": stratum.expected_route_kind,
        }
        by_kind_total[stratum.expected_route_kind] += len(mine)
        by_kind_hit[stratum.expected_route_kind] += hits
    return {
        "count": len(rows),
        "answered": len(answered),
        "rate": _rate(len(answered), len(rows)),
        "by_status": dict(sorted(Counter(str(row["status"]) for row in rows).items())),
        "by_stratum": by_stratum,
        "by_route_kind": {
            kind: {"count": by_kind_total[kind], "rate": _rate(by_kind_hit[kind], by_kind_total[kind])}
            for kind in ("specialized", "fallback", "clarify")
            if by_kind_total[kind]
        },
    }


def _means(rows: Iterable[Mapping[str, Any]]) -> dict[str, float | None]:
    values: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        for item in RUBRIC_ITEMS:
            values[item].append(int(row["scores"][item]))
    return {
        item: round(statistics.fmean(values[item]), 3) if values[item] else None
        for item in RUBRIC_ITEMS
    }


def rubric_means(
    judgment_rows: Sequence[Mapping[str, Any]], *, variant: str = "A"
) -> dict[str, Any]:
    rows = [row for row in judgment_rows if row.get("variant", "A") == variant]
    by_stratum = {}
    for stratum in STRATA:
        mine = [row for row in rows if row["stratum"] == stratum.id]
        if mine:
            by_stratum[stratum.id] = {"count": len(mine), "means": _means(mine)}
    return {
        "variant": variant,
        "count": len(rows),
        "judge_models": sorted({str(row.get("judge_model")) for row in rows}),
        "means": _means(rows),
        "by_stratum": by_stratum,
    }


def agreement_from_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scores: dict[str, dict[str, dict[str, int]]] = {"A": {}, "B": {}}
    for row in rows:
        scores[str(row["variant"])][str(row["question_id"])] = dict(row["scores"])
    out = agreement_rates(scores["A"], scores["B"])
    out["judge_models"] = sorted({str(row.get("judge_model")) for row in rows})
    return out


def pairwise_by_stratum(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out = {}
    for stratum in STRATA:
        mine = [row for row in rows if row["stratum"] == stratum.id]
        if mine:
            out[stratum.id] = pairwise_summary(mine)
    return out


def low_strata(
    rubric: Mapping[str, Any], answers: Sequence[Mapping[str, Any]], *, limit: int = _LOW_STRATA
) -> list[dict[str, Any]]:
    """ⓐ 평균이 낮은 계층과 그 계층의 대표 케이스 하나 (첫 질문의 문구 앞부분)."""
    by_id = {str(row["question_id"]): row for row in answers}
    ranked = sorted(
        rubric["by_stratum"].items(),
        key=lambda pair: (pair[1]["means"]["answered"] or 0.0, pair[0]),
    )[:limit]
    out = []
    for stratum_id, entry in ranked:
        example = next((row for row in answers if row["stratum"] == stratum_id), None)
        out.append(
            {
                "stratum": stratum_id,
                "route_kind": STRATA_BY_ID[stratum_id].expected_route_kind,
                "mean_answered": entry["means"]["answered"],
                "mean_grounded": entry["means"]["grounded"],
                "example_question_id": example["question_id"] if example else None,
                "example_status": example["status"] if example else None,
                "example_message": (
                    str(by_id[example["question_id"]].get("message") or "")[:_EXCERPT]
                    if example
                    else None
                ),
            }
        )
    return out


def grounding_priority(rubric: Mapping[str, Any]) -> list[dict[str, Any]]:
    """접지 우선순위 — 폴백이 받는 계층 중 ⓒ 가 낮은 것부터. 코퍼스를 보강할 순서의 후보다."""
    rows = [
        {"stratum": sid, "route_kind": STRATA_BY_ID[sid].expected_route_kind, **entry["means"]}
        for sid, entry in rubric["by_stratum"].items()
        if STRATA_BY_ID[sid].expected_route_kind == "fallback"
    ]
    return sorted(rows, key=lambda r: (r["grounded"] if r["grounded"] is not None else 9, r["stratum"]))


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def build_summary(inputs: Mapping[str, Any], *, notes: Sequence[str] = ()) -> dict[str, Any]:
    questions = inputs["questions"]
    per_stratum = Counter(case.stratum for case in questions)
    anchor_checks = [
        {
            "judge_model": check["judge_model"],
            "prompt_version": check["prompt_version"],
            "passed": bool(check["passed"]),
            "passed_count": check["passed_count"],
            "anchor_count": check["anchor_count"],
            "failed_anchors": [r["anchor_id"] for r in check["results"] if not r["passed"]],
        }
        for check in inputs["anchor_checks"]
    ]
    passed_models = {c["judge_model"] for c in anchor_checks if c["passed"]}

    labels: dict[str, Any] = {}
    unmeasured_strata: dict[str, list[str]] = {}
    judge_models_used: set[str] = set()
    for label, (meta, rows) in inputs["answers"].items():
        entry: dict[str, Any] = {
            "meta": {
                "adapters": meta.get("settings", {}).get("adapters"),
                "general_fallback": meta.get("settings", {}).get("general_fallback"),
                "router_model_id": meta.get("settings", {}).get("router_model_id"),
                "router_prompt_version": meta.get("settings", {}).get("router_prompt_version"),
                "source_sha": meta.get("source_sha"),
                "source_dirty_files": meta.get("source_dirty_files"),
                "question_count": meta.get("question_count"),
                "questions_sha256": meta.get("questions_sha256"),
                "stopped": meta.get("stopped"),
                "tokens": meta.get("tokens"),
            },
            "answer_rate": answer_rate(rows),
            "rubric": None,
            "rubric_reportable": None,
            "agreement": None,
            "excluded_items": [],
            "low_strata": [],
            "grounding_priority": [],
        }
        collected = {str(row["stratum"]) for row in rows}
        unmeasured_strata[label] = [s.id for s in STRATA if s.id not in collected]

        judged = inputs["judgments"].get(label)
        if judged is not None:
            jmeta, jrows = judged
            rubric = rubric_means(jrows)
            models = set(rubric["judge_models"])
            judge_models_used |= models
            entry["rubric"] = {
                **rubric,
                "prompt_version": jmeta.get("prompt_versions", {}).get("A"),
                "answers_sha256": jmeta.get("answers_sha256"),
                "answers_match": jmeta.get("answers_sha256") == None
                or True,  # placeholder replaced below
                "tokens": jmeta.get("tokens"),
            }
            entry["rubric"]["answers_match"] = _answers_match(inputs, label, jmeta)
            entry["rubric_reportable"] = bool(models) and models <= passed_models
            entry["low_strata"] = low_strata(rubric, rows)
            entry["grounding_priority"] = grounding_priority(rubric)
        agreement = inputs["agreements"].get(label)
        if agreement is not None:
            ameta, arows = agreement
            entry["agreement"] = {**agreement_from_rows(arows), "tokens": ameta.get("tokens")}
            entry["excluded_items"] = list(entry["agreement"]["excluded_items"])
            judge_models_used |= set(entry["agreement"]["judge_models"])
        labels[label] = entry

    pairwise: dict[str, Any] = {}
    for name, (pmeta, prows) in inputs["pairwise"].items():
        judge_models_used.add(str(pmeta.get("judge_model")))
        pairwise[name] = {
            "a_label": pmeta.get("a_label"),
            "b_label": pmeta.get("b_label"),
            "judge_model": pmeta.get("judge_model"),
            "prompt_version": pmeta.get("prompt_version"),
            "reportable": str(pmeta.get("judge_model")) in passed_models,
            "summary": pairwise_summary(prows),
            "by_stratum": pairwise_by_stratum(prows),
            "tokens": pmeta.get("tokens"),
        }

    generation = inputs.get("generation") or {}
    router_model = next(
        (e["meta"]["router_model_id"] for e in labels.values() if e["meta"]["router_model_id"]),
        generation.get("model"),
    )
    automatic_notes = _automatic_notes(labels, pairwise, judge_models_used, router_model)
    return {
        "benchmark_id": BENCHMARK_ID,
        "card": CARD,
        "generated_at": utc_now(),
        "provenance": source_provenance(),
        "questions": {
            "file": "questions_v1.jsonl",
            "count": len(questions),
            "per_stratum": {s.id: per_stratum.get(s.id, 0) for s in STRATA},
            "strata_with_no_question": [s.id for s in STRATA if not per_stratum.get(s.id)],
            "generator_version": generation.get("generator_version"),
            "generation_model": generation.get("model"),
            "generation_temperature": generation.get("temperature"),
            "generation_seed": generation.get("seed"),
            "generation_tokens": generation.get("tokens"),
        },
        "models": {
            "answer_router": router_model,
            "judges": sorted(judge_models_used),
            "judges_passed_anchors": sorted(passed_models),
        },
        "anchor_checks": anchor_checks,
        "labels": labels,
        "pairwise": pairwise,
        "unmeasured": {
            "strata_not_collected": unmeasured_strata,
            "labels_without_judgments": [k for k, v in labels.items() if v["rubric"] is None],
            "labels_without_agreement": [k for k, v in labels.items() if v["agreement"] is None],
            "notes": [*automatic_notes, *notes],
        },
    }


def _answers_match(inputs: Mapping[str, Any], label: str, jmeta: Mapping[str, Any]) -> bool | None:
    """판정 파일이 지금 있는 답변 파일을 채점한 것인가 (sha256). 모르면 None."""
    expected = jmeta.get("answers_sha256")
    if not expected:
        return None
    path = ASSETS_DIR / f"answers_{label}.jsonl"
    if not path.exists():
        return None
    from tools.answer_quality.questions import file_sha256

    return file_sha256(path) == expected


def _automatic_notes(
    labels: Mapping[str, Any],
    pairwise: Mapping[str, Any],
    judge_models: set[str],
    router_model: str | None,
) -> list[str]:
    notes: list[str] = []
    if not labels:
        notes.append("수집된 답변 파일(`answers_<label>.jsonl`)이 없다 — 답변률 · 루브릭 전부 미측정.")
    for label, entry in labels.items():
        adapters = entry["meta"]["adapters"]
        if adapters == "fake":
            notes.append(
                f"`{label}`: 가짜 어댑터 — 라우팅 · 문구 · 상태 단계만 잰 것이고 도메인 답의 품질이 아니다."
            )
        elif adapters == "fallback-only":
            notes.append(
                f"`{label}`: `general` 만 진짜 어댑터 — 전문 능력(훈련 · 제도 · 산책 · 장소)의 답 품질은 재지 않았다."
            )
        if entry["rubric"] is not None and entry["rubric_reportable"] is False:
            notes.append(f"`{label}`: 판정 모델의 앵커 검사가 없거나 실패해 루브릭 점수를 싣지 않는다.")
        if entry["rubric"] is not None and entry["rubric"].get("answers_match") is False:
            notes.append(f"`{label}`: 판정 파일이 지금의 답변 파일과 다른 판본을 채점한 것이다 — 다시 채점할 것.")
    if not pairwise:
        notes.append("쌍대 비교 파일(`pairwise_*`)이 없다 — 전/후 승률 미측정.")
    if router_model and judge_models and judge_models <= {router_model}:
        notes.append(
            f"판정 모델이 답변 라우터와 같은 `{router_model}` 뿐이다 — 자기 계열을 후하게 볼 수 있어 절대 점수보다 전/후 차이를 본다."
        )
    return notes


# ---------------------------------------------------------------------------
# 리포트
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return "–" if value is None else ("예" if value else "아니오")
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(cell) for cell in row) + " |")
    return "\n".join(lines)


def _rubric_header(excluded: Sequence[str]) -> list[str]:
    return [
        f"ⓐ answered/{RUBRIC_MAX['answered']}" + (" (제외)" if "answered" in excluded else ""),
        "ⓑ safe" + (" (제외)" if "safe" in excluded else ""),
        f"ⓒ grounded/{RUBRIC_MAX['grounded']}" + (" (제외)" if "grounded" in excluded else ""),
        "ⓓ deferred" + (" (제외)" if "deferred" in excluded else ""),
        "ⓔ natural" + (" (제외)" if "natural" in excluded else ""),
    ]


def render_report(summary: Mapping[str, Any]) -> str:
    q = summary["questions"]
    prov = summary["provenance"]
    labels = summary["labels"]
    dirty = prov.get("source_dirty_files") or []
    packages = ", ".join(f"{k} {v}" for k, v in (prov.get("packages") or {}).items())

    anchor_table = _table(
        ["판정 모델", "프롬프트", "통과", "실패 앵커"],
        [
            (c["judge_model"], c["prompt_version"], f"{c['passed_count']}/{c['anchor_count']}",
             ", ".join(f"`{a}`" for a in c["failed_anchors"]) or "없음")
            for c in summary["anchor_checks"]
        ],
    ) if summary["anchor_checks"] else "_앵커 검사 기록이 없다 — 어떤 점수도 실을 수 없다._"

    question_table = _table(
        ["계층", "건수", "묶음"],
        [(sid, n, STRATA_BY_ID[sid].expected_route_kind) for sid, n in q["per_stratum"].items()],
    )

    sections: list[str] = []
    for label, entry in labels.items():
        meta = entry["meta"]
        ar = entry["answer_rate"]
        flag = (meta.get("general_fallback") or {}).get("effective")
        lines = [
            f"### `{label}` — 어댑터 {meta['adapters']} · 폴백 {flag} · 소스 `{meta['source_sha']}`"
            + (" ⚠ dirty" if meta.get("source_dirty_files") else ""),
            "",
            f"- 질문 {ar['count']}건 · 답변률 **{_fmt(ar['rate'])}** ({ar['answered']}/{ar['count']})"
            + (f" · 중단: {meta['stopped']}" if meta.get("stopped") else ""),
            f"- 상태별: " + ", ".join(f"{k} {v}" for k, v in ar["by_status"].items()),
            "- 묶음별 답변률: "
            + ", ".join(f"{k} {_fmt(v['rate'])} (n={v['count']})" for k, v in ar["by_route_kind"].items()),
            f"- 라우터 토큰: {(meta.get('tokens') or {}).get('total_tokens', '–')}",
            "",
            _table(
                ["계층", "묶음", "n", "답변률"],
                [(sid, v["route_kind"], v["count"], v["rate"]) for sid, v in ar["by_stratum"].items()],
            ),
        ]
        rubric = entry["rubric"]
        if rubric is None:
            lines += ["", "루브릭: **미측정** (판정 파일 없음)."]
        elif not entry["rubric_reportable"]:
            lines += ["", f"루브릭: 판정 파일은 있으나 판정 모델 {rubric['judge_models']} 의 앵커 검사가 통과하지 않아 **싣지 않는다**."]
        else:
            excluded = entry["excluded_items"]
            lines += [
                "",
                f"루브릭 (변형 A · 판정 모델 {', '.join(rubric['judge_models'])} · {rubric['prompt_version']} · n={rubric['count']}"
                + (f" · 일치율 미달로 제외: {', '.join(excluded)}" if excluded else "")
                + (" · ⚠ 답변 파일 판본 불일치" if rubric.get("answers_match") is False else "")
                + ")",
                "",
                _table(
                    ["범위", *_rubric_header(excluded)],
                    [("전체", *[rubric["means"][i] for i in RUBRIC_ITEMS])]
                    + [
                        (sid, *[v["means"][i] for i in RUBRIC_ITEMS])
                        for sid, v in rubric["by_stratum"].items()
                    ],
                ),
            ]
            if entry["low_strata"]:
                lines += [
                    "",
                    "낮은 계층의 대표 케이스 (ⓐ 평균 오름차순):",
                    "",
                    _table(
                        ["계층", "묶음", "ⓐ", "ⓒ", "예시", "상태", "문구(앞 120자)"],
                        [
                            (r["stratum"], r["route_kind"], r["mean_answered"], r["mean_grounded"],
                             r["example_question_id"], r["example_status"],
                             (r["example_message"] or "").replace("\n", " ").replace("|", "¦"))
                            for r in entry["low_strata"]
                        ],
                    ),
                ]
            if entry["grounding_priority"]:
                lines += [
                    "",
                    "접지 우선순위 (폴백 계층, ⓒ 오름차순 — 코퍼스를 보강할 순서의 후보):",
                    "",
                    _table(
                        ["계층", "ⓒ grounded", "ⓐ answered", "ⓑ safe", "ⓓ deferred"],
                        [(r["stratum"], r["grounded"], r["answered"], r["safe"], r["deferred"])
                         for r in entry["grounding_priority"]],
                    ),
                ]
        agreement = entry["agreement"]
        if agreement is None:
            lines += ["", "일치율: **미측정** (변형 A·B 부분표본 파일 없음)."]
        else:
            lines += [
                "",
                f"일치율 (변형 A 대 B · 부분표본 n={agreement['question_count']} · 판정 모델 {', '.join(agreement['judge_models'])} · 임계 {agreement['threshold']}):",
                "",
                _table(
                    ["항목", "일치율", "판정"],
                    [(item, agreement["rates"][item], "제외" if item in agreement["excluded_items"] else "사용")
                     for item in RUBRIC_ITEMS],
                ),
            ]
        sections.append("\n".join(lines))

    if summary["pairwise"]:
        pair_lines = []
        for name, p in summary["pairwise"].items():
            s = p["summary"]
            pair_lines += [
                f"### `{name}` — A=`{p['a_label']}` (전) · B=`{p['b_label']}` (후) · 판정 모델 {p['judge_model']}"
                + ("" if p["reportable"] else " · ⚠ 앵커 미통과 — 싣지 않는다"),
                "",
                f"- n={s['count']} · B(후) 승 {s['B']} · A(전) 승 {s['A']} · 무 {s['tie']} · 위치 의존 {s['position_dependent']}",
                f"- 후 승률(위치 의존 제외) **{_fmt(s['b_win_rate'])}** · 위치 교환 일치율 {_fmt(s['position_consistency'])}",
                "",
                _table(
                    ["계층", "n", "후 승", "전 승", "무", "위치 의존", "후 승률"],
                    [(sid, v["count"], v["B"], v["A"], v["tie"], v["position_dependent"], v["b_win_rate"])
                     for sid, v in p["by_stratum"].items()],
                ),
                "",
            ]
        pairwise_section = "\n".join(pair_lines)
    else:
        pairwise_section = "**미측정.** 쌍대 비교 파일이 없다."

    unmeasured = summary["unmeasured"]
    not_collected = "\n".join(
        f"- `{label}`: " + (", ".join(f"`{s}`" for s in strata) if strata else "없음 — 전 계층 수집")
        for label, strata in unmeasured["strata_not_collected"].items()
    ) or "- 수집된 라벨이 없다 — 전 계층 미측정"
    notes = "\n".join(f"- {n}" for n in unmeasured["notes"]) or "- 없음"

    return f"""# 답변 품질 리포트 v1 ({CARD})

> 라우팅 골드(`evals/orchestration_router/`)와 **다른 잣대**다. 그쪽은 정책대로 골랐는지를, 여기는
> 답변률(FAILED · CLARIFY 가 아닌 비율)과 판정기가 매긴 답변 품질, 폴백(#279) 전/후의 차이를 잰다.
> 사람이 모으지도 채점하지도 않았다 — 질문은 모델이 계층별로 생성해 동결했고, 판정기의 신뢰도는
> 앵커(코드) · 일치율(프롬프트 변형 둘) · 쌍대 위치 교환으로 잰다.

- 생성 시각: {summary["generated_at"]}
- 소스 SHA: `{prov.get("source_sha", "unknown")}`{" ⚠ dirty: " + ", ".join(dirty) if dirty else ""} · `dev` 머지 베이스 `{prov.get("dev_source_sha", "unknown")}`
- 패키지: {packages or "unknown"}
- 답변 라우터 모델: `{summary["models"]["answer_router"]}` · 판정 모델: {", ".join(f"`{m}`" for m in summary["models"]["judges"]) or "없음"}
- 앵커를 통과한 판정 모델: {", ".join(f"`{m}`" for m in summary["models"]["judges_passed_anchors"]) or "없음"}

## 앵커 — 판정기 자동 검증

코드로 만든 답변 {len(summary["anchor_checks"][0]["failed_anchors"]) + summary["anchor_checks"][0]["passed_count"] if summary["anchor_checks"] else "?"}건을 판정기가 기대대로 매기는지. **전부 통과해야 그 모델의 점수를 싣는다.**

{anchor_table}

## 질문 세트

`{q["file"]}` · {q["count"]}건 · 생성기 `{q["generator_version"]}` · 모델 `{q["generation_model"]}` · temperature {q["generation_temperature"]} · seed {q["generation_seed"]}
· 생성 토큰 {(q.get("generation_tokens") or {}).get("total_tokens", "–")}
{"· ⚠ 질문이 없는 계층: " + ", ".join(f"`{s}`" for s in q["strata_with_no_question"]) if q["strata_with_no_question"] else ""}

생성 질문은 실사용 분포가 아니다. 계층(주제 × 문체)을 고르게 두어 "어느 계층이 약한가" 를 보는
용도이고, 묶음(specialized · fallback · clarify)은 리포트가 묶어 보이는 힌트일 뿐 점수에 쓰지 않는다.

{question_table}

## 답변률 · 루브릭 · 일치율 (라벨별)

{chr(10).join(sections) if sections else "**미측정.** 수집된 답변 파일이 없다."}

## 폴백 전/후 쌍대 비교

같은 질문의 전/후 답을 위치를 바꿔 두 번 비교했다. 두 번이 같은 답을 가리킬 때만 승/패/무로
세고, 순서에 따라 답이 바뀐 것은 "위치 의존" 으로 따로 센다 — 그 비율이 순서 편향의 크기다.
판정기와 답변 모델이 같은 계열이면 자기 답을 후하게 볼 수 있어 **절대 점수보다 이 전/후 차이가
주 지표다** — 같은 편향이 양쪽에 걸려 상쇄된다.

{pairwise_section}

## 미측정

수집되지 않은 계층 (라벨별):

{not_collected}

라벨 없이 남은 것: 판정 없음 {", ".join(f"`{x}`" for x in unmeasured["labels_without_judgments"]) or "없음"} · 일치율 없음 {", ".join(f"`{x}`" for x in unmeasured["labels_without_agreement"]) or "없음"}

{notes}

## 지지되는 결론과 지지되지 않는 결론

**지지되는 것** — 위 표의 숫자는 같은 동결 질문 · 같은 라우터 · 적힌 어댑터 · 적힌 판정 모델로 한
번 잰 값이다. 답변률의 정의는 상태에서 기계적으로 나오고, 루브릭은 앵커를 통과한 판정 모델의 것만
실었으며, 일치율 임계 아래 항목은 표에 "제외" 로 표시했다.

**지지되지 않는 것** — ⑴ 통계적 유의성: 계층당 2~3건, 반복 1회라 검정하지 않는다. ⑵ 실사용
분포에서의 답변률: 계층을 고르게 만든 세트다. ⑶ 가짜 어댑터로 잰 라벨의 도메인 답 품질.
⑷ 판정기의 절대 정확도: 앵커는 명백한 것만 잡는다.
"""


def write_report(summary: Mapping[str, Any], *, directory: Path = ASSETS_DIR) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    summary_path = directory / SUMMARY_NAME
    report_path = directory / REPORT_NAME
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(summary), encoding="utf-8")
    return report_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="답변 품질 리포트 (#277)")
    parser.add_argument("--dir", type=Path, default=ASSETS_DIR)
    parser.add_argument("--note", action="append", default=[], help="미측정 절에 남길 메모 (반복 가능)")
    args = parser.parse_args()
    summary = build_summary(discover(args.dir), notes=args.note)
    report_path, summary_path = write_report(summary, directory=args.dir)
    print(f"리포트 {report_path}")
    print(f"요약   {summary_path}")
    for label, entry in summary["labels"].items():
        print(f"  {label:<12} 답변률 {entry['answer_rate']['rate']} · 루브릭 {'있음' if entry['rubric'] else '없음'}")


if __name__ == "__main__":
    main()
