"""Offline facility Judge. See the adjacent README before running paid calls."""

import argparse
import json
import re
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .judge_anchors import DEFAULT_ANCHORS, load_anchors
from .judge_contract import (
    AXES,
    Contract,
    JudgeInput,
    Judgment,
    Verdict,
    append_jsonl,
    digest,
    file_hash,
    read_jsonl,
    validate_evidence,
    write_json,
)
from .judge_rubric import PROMPT_VERSION, build_inputs, prompt


class JudgeSettings(BaseSettings):
    # Same env contract as the other judges; no import of the backend app/DB settings.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        extra="ignore",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )
    api_key: SecretStr = Field(default=SecretStr(""), validation_alias="OPENAI_API_KEY")
    model: str = Field(default="gpt-5.4-2026-03-05", validation_alias="OPENAI_JUDGE_MODEL")
    timeout_s: float = Field(default=120, gt=0, validation_alias="OPENAI_TIMEOUT_S")


class ProviderResult(Contract):
    verdict: Verdict
    usage: dict[str, int] = Field(default_factory=dict)


def openai_provider(settings: JudgeSettings) -> Callable:
    from openai import OpenAI

    if not settings.api_key.get_secret_value().strip():
        raise ValueError("OPENAI_API_KEY is required for live judging")
    client = OpenAI(
        api_key=settings.api_key.get_secret_value(),
        timeout=settings.timeout_s,
        max_retries=0,  # All attempts must be visible to our call ledger and budget.
    )

    def generate(item: JudgeInput, model: str) -> ProviderResult:
        response = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": prompt(item.axis)},
                {"role": "user", "content": json.dumps(item.payload, ensure_ascii=False)},
            ],
            text_format=Verdict,
            temperature=0,
            max_output_tokens=2200,
            store=False,
        )
        if response.status != "completed" or response.output_parsed is None:
            raise ValueError("judge returned refusal or incomplete structured output")
        usage = response.usage
        return ProviderResult(
            verdict=response.output_parsed,
            usage={k: getattr(usage, k) for k in ("input_tokens", "output_tokens", "total_tokens")}
            if usage
            else {},
        )

    return generate


def key_from_file(path: Path) -> SecretStr:
    if path.is_dir():
        path = path / ".env"
    match = re.search(
        r"(?im)^\s*(?:OPENAI_API_KEY|DAENGS_OPENAI_API_KEY)\s*[:=]\s*(\S+)",
        path.read_text(encoding="utf-8-sig"),
    )
    if not match:
        raise ValueError("OpenAI key field missing in key file")
    return SecretStr(match.group(1).strip("\"'"))


def judge_directory(run: Path, judge_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", judge_id):
        raise ValueError("judge ID must be a simple directory name")
    run = run.resolve(strict=True)
    result = run / "judges" / judge_id
    if not result.resolve().is_relative_to(run):
        raise ValueError("judge directory escapes the observation run")
    return result


@contextmanager
def locked(directory: Path):
    lock = directory / ".lock"
    with lock.open("x", encoding="utf-8") as output:
        output.write(datetime.now(UTC).isoformat())
    try:
        yield
    finally:
        lock.unlink()


def manifest(run: Path, model: str, anchors: Path) -> dict:
    required = ("metadata.json", "cases.json", "observations.jsonl")
    return {
        "schema_version": 1,
        "judge_model": model,
        "prompt_version": PROMPT_VERSION,
        "prompts_sha256": digest({axis: prompt(axis) for axis in AXES}),
        "verdict_schema_sha256": digest(Verdict.model_json_schema()),
        "implementation_sha256": digest(
            {p.name: file_hash(p) for p in Path(__file__).parent.glob("judge*.py")}
        ),
        "source_hashes": {name: file_hash(run / name) for name in required},
        "anchors_sha256": file_hash(anchors),
        "generation": {"temperature": 0, "max_output_tokens": 2200, "store": False},
        "boundary": "server prepare/answer; synthetic search; no APP/member write evidence",
        "purpose": "review assistance, not calibrated quality or release approval",
    }


def input_key(item: JudgeInput) -> tuple:
    return (*item.key.identity(), item.axis)


def load_inputs(directory: Path) -> list[JudgeInput]:
    rows = [JudgeInput.model_validate(row) for row in read_jsonl(directory / "inputs.jsonl")]
    keys = [input_key(row) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate judge input")
    return rows


def verify_run(run: Path, directory: Path, model: str, anchors: Path) -> dict:
    meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if meta["identity"] != manifest(run, model, anchors):
        raise ValueError("judge settings or source changed; use a new judge ID")
    if meta["inputs_sha256"] != file_hash(directory / "inputs.jsonl"):
        raise ValueError("judge inputs changed")
    load_inputs(directory)
    return meta


def create_run(run: Path, judge_id: str, model: str, anchors: Path) -> Path:
    identity = manifest(run, model, anchors)
    inputs = build_inputs(read_jsonl(run / "observations.jsonl"))
    directory = judge_directory(run, judge_id)
    directory.mkdir(parents=True, exist_ok=False)
    for item in inputs:
        append_jsonl(directory / "inputs.jsonl", item.model_dump(mode="json"))
    if not inputs:
        (directory / "inputs.jsonl").touch()
    write_json(
        directory / "metadata.json",
        {
            "identity": identity,
            "created_at": datetime.now(UTC).isoformat(),
            "inputs_sha256": file_hash(directory / "inputs.jsonl"),
            "source": "llm_judge; not independent human review",
        },
    )
    return directory


class Calls:
    def __init__(
        self,
        directory: Path,
        generate: Callable,
        model: str,
        max_calls: int,
        retries: int = 1,
        retry_delay: float = 1,
    ):
        if max_calls < 1 or not 0 <= retries <= 2:
            raise ValueError("positive call cap and 0..2 retries required")
        self.path = directory / "calls.jsonl"
        self.generate, self.model = generate, model
        self.max_calls, self.retries, self.retry_delay = max_calls, retries, retry_delay
        self.fatal_error = None

    def records(self) -> list[dict]:
        return read_jsonl(self.path) if self.path.exists() else []

    def count(self) -> int:
        return sum(row["event"] == "started" for row in self.records())

    def judge(self, item: JudgeInput, phase: str) -> Judgment:
        hashed = digest(item.model_dump(mode="json"))
        base = {"key": item.key, "axis": item.axis, "input_sha256": hashed}
        if item.unavailable_reason:
            return Judgment(**base, status="unmeasured", reason=item.unavailable_reason, attempts=0)
        if self.fatal_error:
            return Judgment(**base, status="judge_error", reason=self.fatal_error, attempts=0)
        attempted = 0
        error_type = None
        for attempt in range(self.retries + 1):
            if self.count() >= self.max_calls:
                return Judgment(
                    **base,
                    status="budget_exhausted",
                    attempts=attempted,
                    reason="judge-run call cap includes anchors and retries",
                )
            call_id = uuid4().hex
            started = time.perf_counter()
            append_jsonl(
                self.path,
                {
                    "event": "started",
                    "call_id": call_id,
                    "phase": phase,
                    "key": item.key.model_dump(),
                    "axis": item.axis,
                    "input_sha256": hashed,
                    "at": datetime.now(UTC).isoformat(),
                },
            )
            attempted += 1
            try:
                result = ProviderResult.model_validate(self.generate(item, self.model))
                validate_evidence(result.verdict, item.payload)
            except Exception as error:  # noqa: BLE001 -- preserve failed provider attempts, no secrets
                # Never log exception strings, response bodies, auth headers or API keys.
                error_type = type(error).__name__
                append_jsonl(
                    self.path,
                    {
                        "event": "finished",
                        "call_id": call_id,
                        "error_type": error_type,
                        "http_status": getattr(error, "status_code", None),
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                    },
                )
                http_status = getattr(error, "status_code", None)
                if (
                    isinstance(http_status, int)
                    and 400 <= http_status < 500
                    and http_status not in {408, 409, 429}
                ):
                    self.fatal_error = f"non-retryable provider error: {error_type} ({http_status})"
                    break
                if attempt < self.retries:
                    time.sleep(self.retry_delay)
            else:
                append_jsonl(
                    self.path,
                    {
                        "event": "finished",
                        "call_id": call_id,
                        "usage": result.usage,
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                    },
                )
                return Judgment(**base, status="judged", verdict=result.verdict, attempts=attempted)
        return Judgment(**base, status="judge_error", reason=error_type, attempts=attempted)


def check_anchors(directory: Path, anchors: Path, calls: Calls) -> dict:
    meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if calls.model != meta["identity"]["judge_model"]:
        raise ValueError("call model must match judge metadata")
    results = []
    for anchor in load_anchors(anchors):
        judgment = calls.judge(anchor.judge_input(), "anchor")
        results.append(
            {
                "id": anchor.id,
                "expected": anchor.expected,
                "input": anchor.judge_input().model_dump(mode="json"),
                "judgment": judgment.model_dump(mode="json"),
                "passed": judgment.verdict is not None
                and judgment.verdict.status == anchor.expected,
            }
        )
    record = {
        "passed": all(r["passed"] for r in results),
        "results": results,
        "metadata_sha256": file_hash(directory / "metadata.json"),
    }
    write_json(directory / "anchor-check.json", record)
    return record


def read_judgments(directory: Path) -> dict[tuple, Judgment]:
    inputs = {input_key(item): item for item in load_inputs(directory)}
    path = directory / "judgments.jsonl"
    result = {}
    for raw in read_jsonl(path) if path.exists() else []:
        row = Judgment.model_validate(raw)
        key = (*row.key.identity(), row.axis)
        if key not in inputs or row.input_sha256 != digest(inputs[key].model_dump(mode="json")):
            raise ValueError("judgment does not match observed input")
        if row.verdict:
            validate_evidence(row.verdict, inputs[key].payload)
        if key in result and result[key].status in {"judged", "unmeasured"}:
            raise ValueError("duplicate completed judgment")
        result[key] = row
    return result


def score(directory: Path, calls: Calls, *, resume: bool = False) -> dict:
    meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if calls.model != meta["identity"]["judge_model"]:
        raise ValueError("call model must match judge metadata")
    anchor = json.loads((directory / "anchor-check.json").read_text(encoding="utf-8"))
    if not anchor["passed"] or anchor["metadata_sha256"] != file_hash(directory / "metadata.json"):
        raise ValueError("matching anchor pass required")
    if (directory / "judgments.jsonl").exists() and not resume:
        raise ValueError("judgments exist; explicitly resume or use a new judge ID")
    completed = read_judgments(directory)
    for item in load_inputs(directory):
        previous = completed.get(input_key(item))
        if previous and previous.status in {"judged", "unmeasured"}:
            continue
        row = calls.judge(item, "score")
        append_jsonl(directory / "judgments.jsonl", row.model_dump(mode="json"))
        if row.status == "budget_exhausted":
            break
    return {"calls": calls.count(), "judgments": len(read_judgments(directory))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-anchors", "score"))
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--judge-id", required=True)
    parser.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--judge-model")
    parser.add_argument(
        "--key-file", type=Path, help="Read a named OpenAI key field; never copy or log it"
    )
    parser.add_argument("--max-calls", type=int, default=100)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    settings = JudgeSettings()
    if args.key_file:
        settings = settings.model_copy(update={"api_key": key_from_file(args.key_file)})
    model = args.judge_model or settings.model
    generate = openai_provider(settings)
    if args.command == "check-anchors":
        directory = create_run(args.run, args.judge_id, model, args.anchors)
    else:
        directory = judge_directory(args.run, args.judge_id)
    with locked(directory):
        verify_run(args.run, directory, model, args.anchors)
        calls = Calls(directory, generate, model, args.max_calls, args.retries)
        if args.command == "check-anchors":
            result = check_anchors(directory, args.anchors, calls)
            print(json.dumps({"directory": str(directory), "passed": result["passed"]}))
            if not result["passed"]:
                raise SystemExit(1)
        else:
            print(json.dumps(score(directory, calls, resume=args.resume)))
            if any(
                r.status in {"judge_error", "budget_exhausted"}
                for r in read_judgments(directory).values()
            ):
                raise SystemExit(1)


if __name__ == "__main__":
    main()
