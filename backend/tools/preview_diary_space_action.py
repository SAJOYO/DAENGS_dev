"""Reuse saved space prose and generate only its existing behavior pin (one request).

Default: offline preparation. --run enables one action generation; any existing
attempt blocks another call. --render-only reads the frozen result without a model.
"""

import argparse
import asyncio
import html
import json
import time
from pathlib import Path

from compare_diary_space_writers import save
from run_diary_route_scenario import configure, prepare, read


def prepare_cases(source, comparison):
    from daengs_backend.services.walk_diary.model_input import normalize
    from daengs_backend.services.walk_diary.preparation.board import with_scene_backgrounds
    from daengs_backend.services.walk_diary.writing import jobs, policy
    from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
    from daengs_walk.diary.board.output import publish_board
    from daengs_walk.diary.contracts.input import digest

    previous = read(comparison / "experiment.json")
    current = policy.writing_version()
    if previous["model"] != current["model"] or (
        previous["writing_policy"]["prompts"]["space"] != current["prompts"]["space"]
    ):
        raise ValueError("saved space prose belongs to another model or strategy")
    raw = read(source / "input.json")
    snapshot = SceneBackgroundSnapshot.model_validate(read(source / "backgrounds.json"))
    if (digest(raw), digest(snapshot)) != (
        previous["input_sha256"],
        previous["backgrounds_sha256"],
    ):
        raise ValueError("space comparison belongs to another source")
    base = with_scene_backgrounds(prepare(raw), snapshot)
    public = {s.id: s for s in publish_board(base.board, base.plan).scenes}
    selected = []
    for case in previous["cases"]:
        scene = next(s for s in base.board.scenes if s.id == case["card_id"])
        stamp = next(s for s in base.slots.stamps if s.scene_id == scene.id)
        space = jobs.space_job(base, scene, stamp)
        model = normalize("space", space.request)
        if model.payload != case["payload"]:
            raise ValueError("current space input differs from saved prose")
        stored = read(comparison / f"case-{case['index']}-optional_details.json")
        if stored["status"] != "completed":
            raise ValueError("saved space prose is incomplete")
        space = space.model_copy(
            update={"llm_request": model.payload, "tool_trace": stored["tool_trace"]}
        )
        space = jobs.validate_output(space, model.restore(stored["answer"]))
        if space.failure_code:
            raise ValueError("saved space result failed current validation")
        action = jobs.action_job(base, scene)
        selected.append((case, public[scene.id], stamp, space, action))
    if sum(action is not None for _, _, _, _, action in selected) != 1:
        raise ValueError("this bounded experiment requires exactly one existing behavior pin")
    action = next(a for _, _, _, _, a in selected if a is not None)
    model = normalize("action", action.request)
    spec = {
        "source": source.name,
        "space_comparison": comparison.name,
        "input_sha256": digest(raw),
        "backgrounds_sha256": digest(snapshot),
        "writer": policy.writing_version(),
        "action_prompt": policy.PROMPTS["action"],
        "action_card_id": action.request["card_id"],
        "action_request_revision": action.request_revision,
        "action_input": model.payload,
        "action_schema": model.schema,
        "spaces": [
            {"index": c["index"], "card_id": s.id, "text": job.accepted["text"]}
            for c, s, _, job, _ in selected
        ],
        "new_model_request_limit": 1,
    }
    return selected, spec


async def generate(selected, spec, output):
    from daengs_backend.services.walk_diary.model_input import normalize
    from daengs_backend.services.walk_diary.writing import assembly, jobs, policy
    from daengs_backend.services.walk_diary.writing.provider import generate_card_prose

    attempt_path = output / "action-attempt.json"
    if attempt_path.exists():
        raise ValueError("an action attempt already exists; no automatic retry")
    action = next(a for _, _, _, _, a in selected if a is not None)
    model = normalize("action", action.request)
    action = action.model_copy(update={"llm_request": model.payload})
    attempt = {"status": "started", "model_requests": 1, "input": model.payload}
    save(attempt_path, attempt)
    start = time.monotonic()
    try:
        async with asyncio.timeout(policy.TIMEOUT_SECONDS):
            raw = await generate_card_prose("action", model.payload, model.schema)
        attempt["raw_answer"] = raw
        action = jobs.validate_output(action, model.restore(raw))
        attempt["status"] = "accepted" if action.accepted else "invalid_response"
    except Exception as exc:  # noqa: BLE001 - persist sanitized failure and never retry
        attempt.update(status="failed", error_type=type(exc).__name__)
        action = action.model_copy(update={"failure_code": "provider_failed"})
    attempt["elapsed_s"] = round(time.monotonic() - start, 3)
    attempt["job"] = action.model_dump(mode="json")
    save(attempt_path, attempt)
    cards = []
    for case, scene, stamp, space, planned in selected:
        result = assembly.frozen_card(scene, stamp, space, action if planned else None)
        if result.writing.space.text != space.accepted["text"].strip():
            raise ValueError("assembly changed the saved space prose")
        if bool(result.writing.actions) != (planned is not None):
            raise ValueError("action escaped its behavior pin")
        expected_original = (
            scene.body
            if scene.user_record and scene.user_record.kind in {"note", "photo"}
            else None
        )
        if result.writing.original_text != expected_original:
            raise ValueError("original note changed")
        cards.append(
            {"index": case["index"], "label": case["label"], "card": result.model_dump(mode="json")}
        )
    result = {
        "cards": cards,
        "new_model_requests": 1,
        "new_space_requests": 0,
        "new_title_requests": 0,
        "new_public_api_calls": 0,
        "generated_actions": int(bool(action.accepted)),
        "reused_spaces": len(cards),
        "action_status": attempt["status"],
    }
    save(output / "result.json", result)
    print(json.dumps({"status": attempt["status"], "answer": action.accepted}, ensure_ascii=False))


def render(output):
    result = read(output / "result.json")
    sections = []
    for entry in result["cards"]:
        parts = entry["card"]["writing"]
        space = html.escape(parts["space"]["text"])
        action = "".join(
            f'<p class="action">{html.escape(a["text"])}</p>' for a in parts["actions"]
        )
        memo = parts["original_text"]
        original = (
            f"<aside><b>보호자 메모 · 원문</b><p>{html.escape(memo)}</p></aside>"
            if memo is not None
            else ""
        )
        debug = html.escape(json.dumps(entry["card"], ensure_ascii=False, indent=2))
        sections.append(
            f'<section><h2>{entry["index"]:02d} · {entry["label"]}</h2><div class="pair">'
            f"<article><h3>공간만</h3><p>{space}</p></article>"
            f"<article><h3>공간 + 기록된 행동</h3><p>{space}</p>{action}</article></div>"
            f"{original}<details><summary>개발자용 · 실제 조립 결과</summary><pre>{debug}</pre></details></section>"
        )
    spec = read(output / "input.json")
    attempt = read(output / "action-attempt.json")
    debug = html.escape(
        json.dumps(
            {"input": spec["action_input"], "prompt": spec["action_prompt"], "attempt": attempt},
            ensure_ascii=False,
            indent=2,
        )
    )
    page = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>공간에 행동을 붙여 읽기</title>
<style>body{margin:0;background:#f4f3ef;color:#24342b;font-family:system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:40px 24px}h1{font-size:32px}header p{line-height:1.8;color:#56645d}
h2{font-size:19px;margin-top:36px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}
article{background:white;border:1px solid #d9ded6;border-radius:14px;padding:24px}
h3{font-size:13px;color:#3c6652}article p{font-size:19px;line-height:1.9;white-space:pre-wrap}
.action{border-left:3px solid #78a889;padding-left:14px}aside{padding:18px;background:#e9ede5;margin-top:12px}
aside p{white-space:pre-wrap;line-height:1.7}aside b,summary{font-size:12px}details{margin-top:18px}
summary{cursor:pointer;color:#65736a}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
@media(max-width:720px){.pair{grid-template-columns:1fr}}</style><main>
<header><h1>공간에 행동을 붙여 읽기</h1><p>이전 선택적 조회의 공간 문장 3개를 그대로 사용했다.
행동 핀이 있는 2번 장면만 기존 행동 작성기로 생성하고 실제 카드 조립기로 붙였다.
초록 선으로 표시한 문장이 이번에 생성한 행동 부분이다.</p>
<p>새 Gemini 요청 1회 · 공간·제목·공공 API 재호출 0회.<br>
공공자료는 실제 저장 응답이며 GPS·행동 핀·메모는 합성 산책 시나리오다.
아래 장면 이름은 비교용 표기다.</p></header>"""
    page += (
        "".join(sections)
        + f"<details><summary>개발자용 · 행동 입력과 호출 원문</summary><pre>{debug}</pre></details></main></html>"
    )
    (output / "preview.html").write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    if args.render_only:
        render(args.output)
        return
    if not args.source or not args.comparison:
        parser.error("source and comparison are required")
    configure(args.env_file if args.run else None)
    selected, spec = prepare_cases(args.source, args.comparison)
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "input.json"
    if path.exists() and read(path) != spec:
        parser.error("saved input differs")
    save(path, spec)
    if args.run:
        asyncio.run(generate(selected, spec, args.output))
        render(args.output)
    else:
        print(
            json.dumps(
                {
                    "prepared_cards": len(selected),
                    "action_input": spec["action_input"],
                    "model_requests": 0,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
