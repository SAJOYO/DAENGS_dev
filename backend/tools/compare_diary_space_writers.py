"""Small saved-data comparison. Preparing/rendering never calls a provider.

--run explicitly enables Gemini. Existing attempt files, including interrupted or
failed attempts, are never retried. This is an experiment, not diary publication.
"""

import argparse
import asyncio
import hashlib
import html
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from run_diary_route_scenario import configure, prepare, read

SELECTED = ((1, "풀밭 배경"), (2, "길 배경"), (3, "숲 배경"))
VARIANTS = ("all_materials", "optional_details")
LABELS = {"all_materials": "기존 · 일괄 공급", "optional_details": "새 방식 · 선택적 상세"}


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def experiment(source):
    from daengs_backend.services.walk_diary import space_details
    from daengs_backend.services.walk_diary.model_input import SpaceAnswer
    from daengs_backend.services.walk_diary.preparation.board import with_scene_backgrounds
    from daengs_backend.services.walk_diary.writing import policy
    from daengs_backend.services.walk_diary.writing.context import get_space_context
    from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
    from daengs_walk.diary.contracts.input import digest

    raw = read(source / "input.json")
    snapshot = SceneBackgroundSnapshot.model_validate(read(source / "backgrounds.json"))
    base = with_scene_backgrounds(prepare(raw), snapshot)
    return {
        "version": "diary-space-comparison-v1",
        "source": source.name,
        "input_sha256": digest(raw),
        "backgrounds_sha256": digest(snapshot),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "provider_sha256": hashlib.sha256(
            Path("src/daengs_backend/services/walk_diary/writing/provider.py").read_bytes()
        ).hexdigest(),
        "model": policy.MODEL,
        "temperature": 0,
        "max_output_tokens": 512,
        "timeout_s": policy.TIMEOUT_SECONDS,
        "max_model_requests": len(SELECTED) * 3,
        "schema": SpaceAnswer.model_json_schema(),
        "prompts": {
            "all_materials": policy.PROMPTS["space"],
            "optional_details": policy.PROMPTS["space"] + space_details.INSTRUCTION,
        },
        "writing_policy": policy.writing_version(),
        "source_note": "공공자료는 실제 저장 응답. GPS·행동·메모는 합성 시나리오.",
        "cases": [
            {
                "index": index,
                "label": label,
                "card_id": base.board.scenes[index - 1].id,
                "payload": get_space_context(base, base.board.scenes[index - 1].id).llm_input,
            }
            for index, label in SELECTED
        ],
    }


async def baseline(payload, spec):
    from google import genai
    from google.genai import types

    from daengs_backend.config import settings

    async with genai.Client(
        api_key=settings.gemini_api_key.get_secret_value().strip(),
        http_options=types.HttpOptions(
            timeout=int(spec["timeout_s"] * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    ).aio as client:
        response = await client.models.generate_content(
            model=spec["model"],
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=spec["prompts"]["all_materials"],
                temperature=spec["temperature"],
                candidate_count=1,
                max_output_tokens=spec["max_output_tokens"],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                response_mime_type="application/json",
                response_json_schema=spec["schema"],
            ),
        )
        return response.text


def validate_answer(payload, answer):
    from daengs_backend.services.walk_diary.model_input import SpaceAnswer

    value = (
        SpaceAnswer.model_validate_json(answer)
        if isinstance(answer, str)
        else SpaceAnswer.model_validate(answer)
    )
    refs = set(value.evidence_ids)
    if (
        len(refs) != len(value.evidence_ids)
        or not refs <= {item["id"] for item in payload["materials"]}
        or bool(value.text.strip()) != bool(refs)
    ):
        raise ValueError("invalid space evidence")
    return value.model_dump(mode="json")


async def attempt(case, variant, spec, path):
    from google.genai import models

    from daengs_backend.services.walk_diary.writing.provider import generate_card_prose

    result = {
        "status": "started",
        "variant": variant,
        "started_at": datetime.now(UTC).isoformat(),
        "requests": [],
        "public_api_calls": 0,
    }
    save(path, result)  # A crash must not silently turn into another paid attempt.
    original = models.AsyncModels.generate_content
    limit = 1 if variant == "all_materials" else 2

    async def measured(client, *args, **kwargs):
        if len(result["requests"]) >= limit:
            raise RuntimeError("comparison request budget")
        request = {"status": "started"}
        result["requests"].append(request)
        save(path, result)
        started = time.monotonic()
        try:
            response = await original(client, *args, **kwargs)
            request.update(
                status="returned",
                elapsed_s=round(time.monotonic() - started, 3),
                usage=response.usage_metadata.model_dump(mode="json", exclude_none=True)
                if response.usage_metadata
                else None,
                # Only visible answer text. No raw responses, signatures or reasoning.
                answer_text="".join(
                    part.text
                    for candidate in response.candidates or []
                    for part in (candidate.content.parts or [] if candidate.content else [])
                    if part.text is not None and not part.thought
                ),
            )
            return response
        except BaseException as exc:
            request.update(status="failed", error_type=type(exc).__name__)
            code = getattr(exc, "code", None)
            if isinstance(code, int):
                request["http_status"] = code
            raise
        finally:
            save(path, result)

    started = time.monotonic()
    try:
        # Instrument only the real SDK boundary, leaving the production tool loop intact.
        with patch.object(models.AsyncModels, "generate_content", measured):
            async with asyncio.timeout(spec["timeout_s"]):
                if variant == "all_materials":
                    answer = await baseline(case["payload"], spec)
                else:
                    generated = await generate_card_prose("space", case["payload"], spec["schema"])
                    result["tool_trace"] = generated.trace
                    if generated.failure_code:
                        result["failure_code"] = generated.failure_code
                        raise ValueError("space generation failed")
                    answer = generated.value
                result["answer"] = validate_answer(case["payload"], answer)
                result["status"] = "completed"
    except Exception as exc:  # noqa: BLE001 - retain sanitized failures without retrying
        result.update(status="failed", error_type=type(exc).__name__)
    finally:
        result["elapsed_s"] = round(time.monotonic() - started, 3)
        save(path, result)
    print(
        json.dumps(
            {
                "case": case["index"],
                "variant": variant,
                "status": result["status"],
                "model_requests": len(result["requests"]),
                "answer": result.get("answer"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


async def run(spec, output, writer=attempt):
    for case in spec["cases"]:
        for variant in VARIANTS:
            path = output / f"case-{case['index']}-{variant}.json"
            if not path.exists():
                await writer(case, variant, spec, path)


def render(spec, output):
    cards = []
    counts = {"model_requests": 0, "completed_variants": 0, "tool_calls": 0, "public_api_calls": 0}
    for case in spec["cases"]:
        columns = []
        for variant in VARIANTS:
            path = output / f"case-{case['index']}-{variant}.json"
            result = read(path) if path.exists() else {"status": "not_run"}
            count = len(result.get("requests", []))
            trace = result.get("tool_trace")
            counts["model_requests"] += count
            counts["completed_variants"] += result["status"] == "completed"
            counts["tool_calls"] += len(trace.get("tool_calls", [])) if trace else 0
            text = result.get("answer", {}).get("text", "")
            if not text:
                text = (
                    "(빈 답변)"
                    if result["status"] == "completed"
                    else f"생성 미완료 · {result['status']}"
                )
            supplied = trace or (case["payload"] if variant == "all_materials" else None)
            debug = {"supplied": supplied, "result": result}
            columns.append(
                f'<article><h3>{LABELS[variant]}</h3><p class="prose">{html.escape(text)}</p>'
                f'<p class="meta">모델 요청 {count}회 · {result.get("elapsed_s", "—")}초</p>'
                "<details><summary>개발자용 · 공급 재료와 실행 기록</summary>"
                f"<pre>{html.escape(json.dumps(debug, ensure_ascii=False, indent=2))}</pre></details>"
                "</article>"
            )
        cards.append(
            f'<section><h2>{case["index"]:02d} · {case["label"]}</h2><div class="pair">'
            + "".join(columns)
            + "</div></section>"
        )
    save(output / "summary.json", counts)
    page = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>공간 서술 · 두 방식 비교</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f3ef;color:#242e2a;font-family:system-ui,sans-serif}
main{max-width:1160px;margin:auto;padding:48px 24px}h1{font-size:32px;margin:12px 0}
header>p{max-width:850px;line-height:1.8;color:#56645d}h2{font-size:20px;margin-top:38px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:18px}article{background:white;border:1px solid #d9ded6;
border-radius:14px;padding:24px}h3{font-size:14px;color:#38614e;margin:0 0 20px}.prose{font-size:19px;
line-height:1.9;min-height:115px;white-space:pre-wrap}.meta,summary{font-size:12px;color:#66726a}
summary{cursor:pointer;padding-top:16px;border-top:1px solid #e7e9e5}pre{font-size:12px;line-height:1.6;
white-space:pre-wrap;overflow-wrap:anywhere}.badge{font-size:12px;color:#38614e;letter-spacing:1px}
@media(max-width:720px){.pair{grid-template-columns:1fr}.prose{min-height:0}main{padding:28px 16px}}
</style><main><header><span class="badge">DAENGS / SAVED DATA / GEMINI</span>
<h1>공간 서술, 두 방식으로 읽기</h1>
<p>같은 세 장면의 공공자료를 사용했다. 왼쪽은 정규화 재료 전체를 한 번에 공급하고,
오른쪽은 기본 배경을 먼저 준 뒤 필요한 주변 상세를 모델이 조회한다.
새 방식에는 도구 사용 지침도 추가되어 있어, 공급 방식만의 효과를 분리한 실험은 아니다.</p>
<p>공공자료는 실제 저장 응답이며 GPS·행동·메모는 합성 시나리오다.
이번에는 공간 문장만 생성했다. 아래 제목은 비교용 배경 이름이다.</p>"""
    page += f"<p>모델 요청 {counts['model_requests']}회 · 상세 조회 {counts['tool_calls']}회 · 추가 공공 API 0회</p>"
    page += "</header>" + "".join(cards) + "</main></html>"
    (output / "preview.html").write_text(page, encoding="utf-8")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    if args.render_only:
        if args.run:
            parser.error("render-only cannot generate")
        print(json.dumps(render(read(args.output / "experiment.json"), args.output)))
        return
    if args.source is None:
        parser.error("source is required for preparation/generation")
    configure(args.env_file if args.run else None)
    spec = experiment(args.source.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    saved = args.output / "experiment.json"
    if saved.exists() and read(saved) != spec:
        parser.error("saved experiment differs; choose a new output directory")
    save(saved, spec)
    if args.run:
        from daengs_backend.config import settings

        if not settings.gemini_api_key.get_secret_value().strip():
            parser.error("Gemini credential missing")
        asyncio.run(run(spec, args.output))
    print(json.dumps(render(spec, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
