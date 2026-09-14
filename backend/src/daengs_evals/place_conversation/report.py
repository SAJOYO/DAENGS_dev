"""Combine immutable observations with explicit reviews; never infer semantic passes."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from .runner import write_json


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def key(row):
    return row["case_id"], row.get("variant", "production-baseline"), row["repetition"], row["turn"]


def summarize(run, *, judge_id=None):
    observations = read_jsonl(run / "observations.jsonl")
    reviews = read_jsonl(run / "reviews.jsonl")
    observed_keys = {key(row) for row in observations}
    by_key = {key(row): row for row in reviews}
    if len(by_key) != len(reviews) or not set(by_key) <= observed_keys:
        raise ValueError("duplicate reviews or reviews without observations")
    cases = defaultdict(list)
    turn_counts = Counter()
    calls = []
    for row in observations:
        calls.extend(row.get("provider_calls", []))
        status = row["status"]
        review = by_key.get(key(row))
        if status not in {"blocked", "not_run"}:
            semantic = (
                [review[axis]["status"] for axis in ("faithfulness", "task_completion")]
                if review
                else []
            )
            if status == "fail" or "fail" in semantic:
                status = "fail"
            elif semantic == ["pass", "pass"]:
                status = "pass"
            else:
                status = "review_required"
        cases[key(row)[:3]].append(status)
        turn_counts[status] += 1
    case_rows = []
    for (case_id, variant, repetition), statuses in sorted(cases.items()):
        status = next(
            (s for s in ("blocked", "not_run", "fail", "review_required") if s in statuses),
            "pass",
        )
        case_rows.append(
            {"case_id": case_id, "variant": variant, "repetition": repetition, "status": status}
        )
    usage = Counter()
    for call in calls:
        for name in ("total_input_tokens", "total_output_tokens", "total_tokens"):
            value = call.get("response", {}).get("usage", {}).get(name)
            if isinstance(value, int):
                usage[name] += value
    result = {
        "observed_turns": len(observations),
        "reviewed_turns": len(reviews),
        "turn_statuses": dict(turn_counts),
        "case_repetitions": case_rows,
        "provider_calls": len(calls),
        "provider_errors": sum("error_type" in c for c in calls),
        "provider_latency_median_ms": median([c["latency_ms"] for c in calls]) if calls else None,
        "reported_usage": dict(usage),
        "usage_missing_calls": sum("usage" not in c.get("response", {}) for c in calls),
        "latency_note": "provider latency excludes evaluator pacing; per-turn latency includes it",
    }
    write_json(run / "reviewed-summary.json", result)
    lines = [
        "# 실행 검토 요약",
        "",
        "원본 관측은 변경하지 않았다. 의미 판정은 reviews.jsonl의 개별 사유를 따른다.",
        "API의 not_run과 별도 controlled.xml 검증, direct-answer 변형은 원문 모델 평가와 구분한다.",
        "",
        "| ID | 변형 | 반복별 최종 판정 |",
        "| --- | --- | --- |",
    ]
    grouped = defaultdict(list)
    for row in case_rows:
        grouped[(row["case_id"], row["variant"])].append(f"{row['repetition']}: {row['status']}")
    for (case_id, variant), statuses in grouped.items():
        lines.append(f"| {case_id} | {variant} | {', '.join(statuses)} |")
    lines.extend(
        [
            "",
            f"모델 호출 {len(calls)}회, 제공자 오류 {result['provider_errors']}회.",
            f"모델 호출 지연 중앙값 {result['provider_latency_median_ms']}ms (평가 간격 제외).",
            "",
        ]
    )
    (run / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {"reviewed_turns": len(reviews), "statuses": dict(turn_counts)}, ensure_ascii=False
        )
    )
    if judge_id is not None:
        judge_report(run, judge_id)


def judge_report(run, judge_id):
    """Keep model opinions beside code and explicit reviews, never promote them to passes."""
    from .judge import judge_directory, load_inputs, read_judgments
    from .judge_contract import AXES, TurnKey, file_hash

    directory = judge_directory(run, judge_id)
    meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    for name, expected in meta["identity"]["source_hashes"].items():
        if file_hash(run / name) != expected:
            raise ValueError("source observation run changed after judging")
    if file_hash(directory / "inputs.jsonl") != meta["inputs_sha256"]:
        raise ValueError("judge inputs changed")
    judgments = read_judgments(directory)
    observations = read_jsonl(run / "observations.jsonl")
    reviews = {key(r): r for r in read_jsonl(run / "reviews.jsonl")}
    rows = []
    counts = Counter()
    for observed in observations:
        identity = TurnKey.of(observed).identity()
        code = observed["status"]
        if code not in {"blocked", "not_run"}:
            code = (
                "fail"
                if code == "fail" or any(c["status"] == "fail" for c in observed.get("checks", []))
                else "pass"
            )
            if code == "pass" and not any(
                c["status"] == "pass" for c in observed.get("checks", [])
            ):
                code = "unmeasured"
        review = reviews.get(identity)
        reviewed = (
            [review[a]["status"] for a in ("task_completion", "faithfulness")] if review else []
        )
        review_status = (
            "fail"
            if "fail" in reviewed
            else ("pass" if reviewed == ["pass", "pass"] else "review_required")
        )
        axes = {}
        for axis in AXES:
            judgment = judgments.get((*identity, axis))
            status = (
                (judgment.verdict.status if judgment.verdict else judgment.status)
                if judgment
                else "not_run"
            )
            axes[axis] = {
                "status": status,
                "reason": judgment.verdict.rationale
                if judgment and judgment.verdict
                else (judgment.reason if judgment else "not judged"),
                "evidence": [e.model_dump() for e in judgment.verdict.evidence]
                if judgment and judgment.verdict
                else [],
            }
            counts[f"{axis}.{status}"] += 1
        rows.append(
            {
                "key": TurnKey.of(observed).model_dump(),
                "code_status": code,
                "review_status": review_status,
                "final_status": code
                if code in {"fail", "blocked", "not_run"}
                else ("review_required" if code == "unmeasured" else review_status),
                "judge_axes": axes,
                "needs_attention": code != "pass"
                or review_status == "review_required"
                or any(a["status"] not in {"pass", "not_applicable"} for a in axes.values()),
            }
        )
    calls = read_jsonl(directory / "calls.jsonl")
    anchor_path = directory / "anchor-check.json"
    anchors = json.loads(anchor_path.read_text(encoding="utf-8")) if anchor_path.exists() else {}
    started_calls = sum(c["event"] == "started" for c in calls)
    finished_calls = sum(c["event"] == "finished" for c in calls)
    result = {
        "source": meta["source"],
        "judge_model": meta["identity"]["judge_model"],
        "boundary": meta["identity"]["boundary"],
        "rows": rows,
        "axis_counts": dict(counts),
        "expected_axis_rows": len(load_inputs(directory)),
        "anchor_passed": anchors.get("passed"),
        "anchor_count": len(anchors.get("results", [])),
        "anchors_passed_count": sum(r["passed"] for r in anchors.get("results", [])),
        "calls_started": started_calls,
        "call_errors": sum("error_type" in c for c in calls),
        "unfinished_calls": started_calls - finished_calls,
        "usage_missing_calls": started_calls - sum(bool(c.get("usage")) for c in calls),
        "usage": dict(sum((Counter(c.get("usage", {})) for c in calls), Counter())),
    }
    write_json(directory / "summary.json", result)
    lines = [
        "# 시설 Judge 검토 목록",
        "",
        "자동 판정은 검토 보조이며 독립 사람 리뷰나 출시 승인 점수가 아닙니다.",
        "코드 검사 실패와 기존 리뷰는 유지합니다. 판단 보류·미측정·호출 오류는 통과가 아닙니다.",
        "",
        f"Judge: {result['judge_model']}. 호출 {result['calls_started']}회 / 오류 {result['call_errors']}회.",
        f"앵커 {result['anchors_passed_count']}/{result['anchor_count']}. 앵커 미통과 시 실제 턴 판정은 실행하지 않습니다.",
        "",
        "| 사례 / 변형 / 반복 / 턴 | 코드 | 별도 리뷰 | 의도 | 범위 | 결과 설명 | 최종 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        identity = "/".join(str(x) for x in row["key"].values()).replace("|", "\\|")
        statuses = [row["judge_axes"][a]["status"] for a in AXES]
        lines.append(
            "| "
            + " | ".join(
                [
                    identity,
                    row["code_status"],
                    row["review_status"],
                    *statuses,
                    row["final_status"],
                ]
            )
            + " |"
        )
    lines.extend(["", "## 근거", ""])
    for row in rows:
        for axis, judged in row["judge_axes"].items():
            # Indented code keeps model text literal, including Markdown/HTML and backticks.
            lines.append(
                "    " + json.dumps({"key": row["key"], "axis": axis, **judged}, ensure_ascii=False)
            )
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--judge-id")
    args = parser.parse_args()
    summarize(args.run, judge_id=args.judge_id)


if __name__ == "__main__":
    main()
