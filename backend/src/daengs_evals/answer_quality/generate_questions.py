"""계층마다 질문을 생성해 동결한다 (#277).

    uv run python -m daengs_evals.answer_quality.generate_questions --strata general_care --dry-run
    uv run python -m daengs_evals.answer_quality.generate_questions            # 전 계층 → questions_v1.jsonl

**계층당 호출 한 번**, 스키마 실패에만 한 번 더. 생성 모델은 의미 라우터와 같은
`ROUTER_MODEL_ID` 이고 temperature · seed · 프롬프트 버전을 사이드카(`questions_v1_generation.json`)
에 적는다. 결과 파일이 이미 있으면 `--force` 없이는 덮어쓰지 않는다 — 동결이 뜻하는 것이 그것이다.

생성 질문은 실사용 분포가 아니다. 계층을 고르게 두어 "어느 계층이 약한가" 를 보는 용도다.
개인정보(실명 · 전화번호 · 주소 · 차량번호 등)는 프롬프트로 막고 파일에 들어가지 않는다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from daengs_evals.answer_quality.gemini import (
    DEFAULT_TOKEN_BUDGET,
    TokenBudgetExceeded,
    TokenLedger,
    generate_structured,
)
from daengs_evals.answer_quality.provenance import source_provenance, utc_now
from daengs_evals.answer_quality.questions import (
    ASSETS_DIR,
    QUESTIONS_V1_PATH,
    QuestionCase,
    dedupe,
    normalized_key,
    write_questions,
)
from daengs_evals.answer_quality.strata import Stratum, resolve_strata, strata_for_set

GENERATOR_VERSION = "answer-quality-questions-ko-v1"
GENERATION_TEMPERATURE = 0.9
GENERATION_SEED = 277
GENERATION_MAX_OUTPUT_TOKENS = 1_024
#: 중복 제거 뒤 목표치를 채우려고 목표보다 하나 더 청한다. 그래도 모자라면 모자란 대로 둔다 —
#: 호출을 늘리지 않는다 (예산).
_OVERSAMPLE = 1


class GeneratedQuestions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[str] = Field(min_length=1, max_length=12)


_INSTRUCTIONS = """당신은 반려견 케어 서비스 "댕즈(DAENGS)" 의 채팅 도우미에게 사용자가 보낼 법한 한국어 질문을 만드는 생성기다.
아래 계층(STRATUM) 설명에 정확히 맞는 질문을 {count}개 만들어라.

규칙:
- 각 질문은 사용자가 채팅창에 실제로 입력할 한 덩어리 메시지다. 1~3문장, 자연스러운 한국어.
- TOPIC 의 내용과 STYLE 의 말투 · 형식을 **둘 다** 지켜라. STYLE 이 오타 · 잡음 · 잡담 · 복합 의도를 요구하면 그것을 실제로 넣어라.
- {count}개는 서로 다른 상황 · 표현이어야 한다. 같은 질문을 말만 바꾸지 마라.
- 좌표 · 위도 · 경도 숫자를 쓰지 마라. LOCATION_CONTEXT 가 none 이면 "여기", "근처", "우리 동네" 같은 표현으로 위치를 전제하되 지명은 넣지 않아도 된다.
- 개인정보를 넣지 마라: 사람 이름, 전화번호, 상세 주소, 차량번호, 계정 ID, 실제 업체명. 강아지 이름은 흔한 가명(예: 콩이, 보리)만 허용.
- 실제 법령 조항 번호, 요금, 통계 수치를 질문 안에 단정해 넣지 마라. 사용자는 모르는 채 묻는다.
- 질문만 출력한다. 답변 · 설명 · 번호 매기기 없이 JSON 스키마대로.

{brief}"""


def build_generation_prompt(stratum: Stratum, *, count: int) -> str:
    return (
        f"PROMPT_VERSION: {GENERATOR_VERSION}\n\n"
        + _INSTRUCTIONS.format(count=count, brief=stratum.generator_brief())
        + "\n"
    )


def to_cases(stratum: Stratum, queries: Sequence[str]) -> list[QuestionCase]:
    return [
        QuestionCase(
            question_id=f"{stratum.id}_{index:02d}",
            query=query.strip(),
            context=stratum.context(),
            stratum=stratum.id,
            generator_version=GENERATOR_VERSION,
        )
        for index, query in enumerate(queries, start=1)
    ]


def generate_for_stratum(
    stratum: Stratum,
    *,
    ledger: TokenLedger,
    seen_keys: set[str],
    model: str,
    generate: Callable[..., GeneratedQuestions] | None = None,
) -> tuple[list[QuestionCase], dict[str, Any]]:
    """계층 하나 → 질문 목록. 파일 전체의 정규화 키(`seen_keys`)로 중복을 뺀다."""
    target = stratum.questions_target
    requested = target + _OVERSAMPLE
    call = generate or generate_structured
    batch = call(
        model=model,
        prompt=build_generation_prompt(stratum, count=requested),
        schema=GeneratedQuestions,
        temperature=GENERATION_TEMPERATURE,
        max_output_tokens=GENERATION_MAX_OUTPUT_TOKENS,
        seed=GENERATION_SEED,
        ledger=ledger,
        label=stratum.id,
    )
    unique = dedupe([q for q in batch.questions if q.strip()], seen=seen_keys)[:target]
    record = {
        "stratum": stratum.id,
        "requested": requested,
        "returned": len(batch.questions),
        "kept": len(unique),
        "short_by": max(0, target - len(unique)),
    }
    return to_cases(stratum, unique), record


def generate_all(
    strata: Sequence[Stratum],
    *,
    model: str,
    ledger: TokenLedger,
    log: Callable[[str], None] = print,
    generate: Callable[..., GeneratedQuestions] | None = None,
) -> tuple[list[QuestionCase], list[dict[str, Any]]]:
    cases: list[QuestionCase] = []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, stratum in enumerate(strata, start=1):
        generated, record = generate_for_stratum(
            stratum, ledger=ledger, seen_keys=seen, model=model, generate=generate
        )
        # `dedupe` 가 이미 `seen` 에 넣었지만, 생성기를 바꿔 끼운 테스트에서도 같은 규칙이 서게 한다.
        seen.update(normalized_key(case.query) for case in generated)
        cases.extend(generated)
        records.append(record)
        log(
            f"  [{index:>2}/{len(strata)}] {stratum.id:<36} kept {record['kept']}/{stratum.questions_target}"
            + (f"  (short by {record['short_by']})" if record["short_by"] else "")
        )
    return cases, records


def write_generation_meta(path: Path, meta: dict[str, Any]) -> None:
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    from daengs_backend.orchestration.semantic import ROUTER_MODEL_ID

    parser = argparse.ArgumentParser(description="계층별 질문 생성 · 동결 (#277)")
    parser.add_argument("--out", type=Path, default=QUESTIONS_V1_PATH)
    parser.add_argument("--strata", nargs="*", help="계층 id · 주제 · 문체 이름으로 거른다")
    parser.add_argument(
        "--question-set",
        choices=("v1", "screening"),
        default="v1",
        help="이 세트의 계층만 만든다. **파일과 세트는 1:1 이다** — `questions_v1.jsonl` 은 동결이고 "
        "그 sha256 이 #277 의 답변 메타에 박혀 있어, 나중에 더한 주제를 섞으면 안 된다 (#314)",
    )
    parser.add_argument("--model", default=ROUTER_MODEL_ID)
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    parser.add_argument("--force", action="store_true", help="이미 동결된 파일을 덮어쓴다")
    parser.add_argument("--dry-run", action="store_true", help="프롬프트만 찍고 부르지 않는다")
    args = parser.parse_args()

    in_set = {s.id for s in strata_for_set(args.question_set)}
    strata = [s for s in resolve_strata(args.strata) if s.id in in_set]
    if not strata:
        parser.error(f"--question-set {args.question_set} 에 해당하는 계층이 없습니다")
    target_total = sum(s.questions_target for s in strata)
    print(
        f"계층 {len(strata)}개 · 목표 {target_total}건 · 모델 {args.model} · 호출 {len(strata)}회"
    )
    if args.dry_run:
        print(build_generation_prompt(strata[0], count=strata[0].questions_target + _OVERSAMPLE))
        return
    if args.out.exists() and not args.force:
        parser.error(f"{args.out} 가 이미 있습니다 (동결). 다시 만들려면 --force")

    ledger = TokenLedger(budget=args.token_budget, log=print)
    started = utc_now()
    stopped: str | None = None
    try:
        cases, records = generate_all(strata, model=args.model, ledger=ledger)
    except TokenBudgetExceeded as exc:
        stopped = str(exc)
        print(f"중단: {exc}")
        return
    finally:
        print(
            f"토큰 합계 {ledger.total:,} (입력 {ledger.input_tokens:,} · 출력 {ledger.output_tokens:,})"
        )

    write_questions(args.out, cases)
    meta = {
        "generator_version": GENERATOR_VERSION,
        "model": args.model,
        "temperature": GENERATION_TEMPERATURE,
        "seed": GENERATION_SEED,
        "max_output_tokens": GENERATION_MAX_OUTPUT_TOKENS,
        "calls_per_stratum": 1,
        "schema_retry": "once on schema failure",
        "started_at": started,
        "finished_at": utc_now(),
        "stopped": stopped,
        "strata_count": len(strata),
        "target_total": target_total,
        "written_total": len(cases),
        "per_stratum": records,
        "tokens": ledger.summary(),
        "provenance": source_provenance(),
    }
    meta_path = ASSETS_DIR / f"{args.out.stem}_generation.json"
    write_generation_meta(meta_path, meta)
    print(f"질문  {args.out} ({len(cases)}건)")
    print(f"메타  {meta_path}")


if __name__ == "__main__":
    main()
