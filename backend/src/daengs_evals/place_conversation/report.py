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


def summarize(run):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    summarize(parser.parse_args().run)


if __name__ == "__main__":
    main()
