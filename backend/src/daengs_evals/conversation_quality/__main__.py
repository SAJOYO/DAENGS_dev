"""대화 품질 명령 (#401).

    uv run python -m daengs_evals.conversation_quality collect --lap lap1 --out-dir <dir> \\
        --adapter-mode fake
    uv run python -m daengs_evals.conversation_quality check-anchors --anchor-set dev
    uv run python -m daengs_evals.conversation_quality score --lap-file <lap.jsonl>
    uv run python -m daengs_evals.conversation_quality score --lap-file <lap.jsonl> \\
        --out <judgments.jsonl> --resume   # 죽었던 판정 파일을 이어서 돈다
    uv run python -m daengs_evals.conversation_quality report --lap-file <lap.jsonl> \\
        --judgments <judgments.jsonl>
    uv run python -m daengs_evals.conversation_quality compare \\
        --before-lap <lap_before.jsonl> --before-judgments <judgments_before.jsonl> \\
        --after-lap <lap_after.jsonl> --after-judgments <judgments_after.jsonl>

`score` 가 **실제 judge 를 부르는 유일한 자리**다. `judge.run_score` 는 `anchors_sha256` 을
기본값 없는 필수 키워드로 받는다 — 넘길 수 있게만 해 두면 안 넘기는 길이 기본 경로가 되고
마지막 검사 자리가 열린 채로 남기 때문이다(선택은 잊힌다). 그래서 이 CLI 는 항상
`anchors.anchors_sha256()` 로 지금 앵커 파일의 해시를 내어 그대로 넘긴다 — 손으로 넣지
않는다.

`collect` · `check-anchors` 도 실 모델을 부른다(`--adapter-mode real` 이거나 세만틱
라우터가 Gemini 를 물기 때문). `report` · `compare` 는 모델을 다시 안 부르고 이미 있는
파일만 읽는다는 점은 같지만, **완전히 설정 없이 도는 것은 아니다** — `summarize` 가
`daengs_backend.orchestration.redirects`(`dead_end` 진단 · 코드 기반 검사) 를 늦게 물기
때문에, `backend/.env` 가 없는 체크아웃에서는 그 두 자리가 죽는 대신 "측정 불가"로
내려갈 뿐 값을 계산하지는 못한다. 판정 파일을 다시 만들거나 judge 를 부르지는 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from daengs_evals.conversation_quality import CASES_V1_PATH
from daengs_evals.conversation_quality import anchors as anchors_mod
from daengs_evals.conversation_quality import report as report_mod
from daengs_evals.conversation_quality.cases import load_cases
from daengs_evals.conversation_quality.collect import (
    ADAPTER_MODES,
    build_stateless_driver,
    load_lap,
    run_collect,
)
from daengs_evals.conversation_quality.drivers import FakeDriver
from daengs_evals.conversation_quality.judge import PROMPT_VERSION, openai_generate, run_score
from daengs_evals.conversation_quality.judge import judge_model as default_judge_model


def _resolve_model(override: str | None) -> str:
    return override or default_judge_model()


def cmd_collect(args: argparse.Namespace) -> int:
    cases_path = Path(args.cases) if args.cases else CASES_V1_PATH
    cases = load_cases(cases_path)
    if args.adapter_mode == "fake-driver":
        n = sum(len(c.target_turns) for c in cases)
        driver = FakeDriver(replies=[args.fake_reply] * n)
    else:
        driver = build_stateless_driver(args.adapter_mode)
    path = run_collect(
        cases=cases,
        driver=driver,
        out_dir=Path(args.out_dir),
        lap=args.lap,
        cases_path=cases_path,
        judge_model=_resolve_model(args.judge_model),
        prompt_version=PROMPT_VERSION,
        anchor_set=args.anchor_set,
    )
    print(f"랩 파일 → {path}")
    return 0


def cmd_check_anchors(args: argparse.Namespace) -> int:
    model = _resolve_model(args.judge_model)
    record = anchors_mod.check(args.anchor_set, generate=openai_generate, model=model)
    for row in record["results"]:
        mark = "OK  " if row["passed"] else "FAIL"
        print(f"  [{mark}] {row['anchor_id']:<32} 기대={row['expected']} 실제={row['actual']}")
    path = anchors_mod.write_record(
        record,
        anchor_dir=Path(args.anchor_dir) if args.anchor_dir else None,
        model=model,
        anchor_set=args.anchor_set,
    )
    print(f"\n앵커 {record['n_passed']}/{record['n']} 통과 → {path}")
    if not record["passed"]:
        print("⚠ 통과하지 못했습니다. 프롬프트를 고치면 PROMPT_VERSION 을 올려야 합니다.")
        return 1
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    cases_path = Path(args.cases) if args.cases else CASES_V1_PATH
    cases = load_cases(cases_path)
    lap_meta, lap_rows = load_lap(Path(args.lap_file))
    model = _resolve_model(args.judge_model)
    out_path = (
        Path(args.out)
        if args.out
        else Path(args.lap_file).with_name(f"judgments_{lap_meta.get('lap', 'lap')}.jsonl")
    )
    # `anchors_sha256` 은 기본값이 없는 필수 키워드다 — 여기서 늘 지금 앵커 파일의 해시를
    # 내어 넘긴다. 이 줄을 빼먹는 것이 정확히 이 설계가 막으려는 실패다.
    judgments = run_score(
        rows=lap_rows,
        cases=cases,
        model=model,
        anchor_dir=Path(args.anchor_dir) if args.anchor_dir else None,
        anchor_set=args.anchor_set,
        anchors_sha256=anchors_mod.anchors_sha256(),
        lap=str(lap_meta.get("lap", "")),
        out_path=out_path,
        resume=args.resume,
    )
    print(f"판정 {len(judgments)}건 → {out_path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    lap_meta, lap_rows = load_lap(Path(args.lap_file))
    judge_header, judgments = report_mod.load_judgments(Path(args.judgments))
    summary = report_mod.summarize(
        lap_meta=lap_meta, lap_rows=lap_rows, judge_header=judge_header, judgments=judgments
    )
    text = report_mod.render(summary)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


def _load_summary(lap_file: str, judgments_file: str) -> report_mod.Summary:
    lap_meta, lap_rows = load_lap(Path(lap_file))
    judge_header, judgments = report_mod.load_judgments(Path(judgments_file))
    return report_mod.summarize(
        lap_meta=lap_meta, lap_rows=lap_rows, judge_header=judge_header, judgments=judgments
    )


def cmd_compare(args: argparse.Namespace) -> int:
    before = _load_summary(args.before_lap, args.before_judgments)
    after = _load_summary(args.after_lap, args.after_judgments)
    # 다섯 고정 항목 중 하나라도 다르면 `render_compare` 가 `ValueError` 로 거부한다 —
    # 여기서 잡아 사람이 읽을 수 있는 종료 메시지로만 바꾼다.
    try:
        text = report_mod.render_compare(before=before, after=after)
    except ValueError as exc:
        print(f"비교를 만들지 않습니다: {exc}", file=sys.stderr)
        return 1
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daengs_evals.conversation_quality")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="케이스를 드라이버에 먹여 랩 파일을 만든다")
    p_collect.add_argument("--lap", required=True)
    p_collect.add_argument("--out-dir", required=True)
    p_collect.add_argument("--cases")
    p_collect.add_argument(
        "--adapter-mode", choices=(*ADAPTER_MODES, "fake-driver"), default="fake-driver"
    )
    p_collect.add_argument("--fake-reply", default="(fake)")
    p_collect.add_argument("--judge-model")
    p_collect.add_argument("--anchor-set", default="dev")
    p_collect.set_defaults(func=cmd_collect)

    p_anchors = sub.add_parser("check-anchors", help="앵커 세트를 돌려 judge 를 재본다")
    p_anchors.add_argument("--anchor-set", choices=("dev", "holdout"), default="dev")
    p_anchors.add_argument("--judge-model")
    p_anchors.add_argument("--anchor-dir")
    p_anchors.set_defaults(func=cmd_check_anchors)

    p_score = sub.add_parser("score", help="랩 파일을 판정한다 (앵커 통과 기록 필요)")
    p_score.add_argument("--lap-file", required=True)
    p_score.add_argument("--cases")
    p_score.add_argument("--judge-model")
    p_score.add_argument("--anchor-set", default="dev")
    p_score.add_argument("--anchor-dir")
    p_score.add_argument("--out")
    # 39콜짜리 랩 한복판에서 죽으면 이 하나로 이어서 돈다 — 이미 성공한 판정은 다시 안
    # 부르고, 헤더 핀(judge_model·prompt_version·anchor_set·anchors_sha256)이 하나라도
    # 옮겨졌으면 run_score 가 SystemExit 으로 거부한다.
    p_score.add_argument("--resume", action="store_true")
    p_score.set_defaults(func=cmd_score)

    p_report = sub.add_parser("report", help="랩·판정 파일에서 리포트를 렌더한다")
    p_report.add_argument("--lap-file", required=True)
    p_report.add_argument("--judgments", required=True)
    p_report.add_argument("--out")
    p_report.set_defaults(func=cmd_report)

    p_compare = sub.add_parser("compare", help="두 랩을 견준다 (고정 항목이 움직이면 거부)")
    p_compare.add_argument("--before-lap", required=True)
    p_compare.add_argument("--before-judgments", required=True)
    p_compare.add_argument("--after-lap", required=True)
    p_compare.add_argument("--after-judgments", required=True)
    p_compare.add_argument("--out")
    p_compare.set_defaults(func=cmd_compare)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
