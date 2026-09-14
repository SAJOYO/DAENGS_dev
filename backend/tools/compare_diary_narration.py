"""Compare new shared narration with saved space/action prose; never regenerate baseline.

Default prepares only. --run enables at most 3 space dialogues (2 calls each)
and 1 action call. Existing attempts, including failures, are never retried.
"""

import argparse
import asyncio
import hashlib
import html
import json
import time
from pathlib import Path
from unittest.mock import patch

from compare_diary_space_writers import save
from run_diary_route_scenario import configure, prepare, read


def prepare_cases(source, comparison, previous):
    from daengs_backend.services.walk_diary import space_details
    from daengs_backend.services.walk_diary.model_input import normalize
    from daengs_backend.services.walk_diary.preparation.board import with_scene_backgrounds
    from daengs_backend.services.walk_diary.writing import jobs, policy
    from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
    from daengs_walk.diary.board.output import publish_board
    from daengs_walk.diary.contracts.input import digest

    old = read(comparison / "experiment.json")
    old_action = read(previous / "input.json")
    old_cards = read(previous / "result.json")
    raw = read(source / "input.json")
    snapshot = SceneBackgroundSnapshot.model_validate(read(source / "backgrounds.json"))
    if (digest(raw), digest(snapshot)) != (old["input_sha256"], old["backgrounds_sha256"]):
        raise ValueError("source differs from baseline")
    if old["model"] != policy.MODEL or old_action["writer"]["model"] != policy.MODEL:
        raise ValueError("model differs from baseline")
    base = with_scene_backgrounds(prepare(raw), snapshot)
    public = {s.id: s for s in publish_board(base.board, base.plan).scenes}
    before = {c["card"]["id"]: c["card"] for c in old_cards["cards"]}
    selected, cases = [], []
    for case in old["cases"]:
        scene = next(s for s in base.board.scenes if s.id == case["card_id"])
        stamp = next(s for s in base.slots.stamps if s.scene_id == scene.id)
        space = jobs.space_job(base, scene, stamp)
        action = jobs.action_job(base, scene)
        wires = {}
        for item in (space, action):
            if item is None:
                continue
            wire = normalize(item.stage, item.request)
            baseline = case["payload"] if item.stage == "space" else old_action["action_input"]
            if {k: v for k, v in wire.payload.items() if k != "narration"} != baseline:
                raise ValueError("selected material differs from baseline")
            if item.stage == "action" and scene.id != old_action["action_card_id"]:
                raise ValueError("behavior pin differs from baseline")
            wires[item.stage] = wire.payload
        selected.append((case, public[scene.id], stamp, space, action))
        cases.append(
            {
                "index": case["index"],
                "label": case["label"],
                "card_id": scene.id,
                "inputs": wires,
                "before": before[scene.id]["writing"],
            }
        )
    if len(selected) != 3 or sum(a is not None for _, _, _, _, a in selected) != 1:
        raise ValueError("experiment requires three scenes and exactly one behavior pin")
    paths = [
        *Path("src/daengs_backend/services/walk_diary").rglob("*.py"),
        *Path("src/daengs_walk/diary/board").glob("*.py"),
    ]
    spec = {
        "format": "diary-narration-comparison-v1",
        "source": source.name,
        "baseline": previous.name,
        "input_sha256": digest(raw),
        "backgrounds_sha256": digest(snapshot),
        "baseline_sha256": digest(old_cards),
        "source_code_sha256": digest(
            {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
        ),
        "writer": policy.writing_version(),
        "prompts": {
            "space": policy.PROMPTS["space"] + space_details.INSTRUCTION,
            "action": policy.PROMPTS["action"],
        },
        "max_new_model_requests": 7,
        "new_public_api_calls": 0,
        "new_title_requests": 0,
        "cases": cases,
    }
    return selected, spec


async def attempt(item, path):
    if path.exists() or path.with_suffix(path.suffix + ".gz").exists():
        return read(path)  # Do not retry a failed or interrupted paid call.
    from google.genai import models

    from daengs_backend.services.walk_diary.model_input import normalize
    from daengs_backend.services.walk_diary.writing import jobs, policy
    from daengs_backend.services.walk_diary.writing.provider import generate_card_prose

    wire = normalize(item.stage, item.request)
    item = item.model_copy(update={"llm_request": wire.payload})
    result = {"status": "started", "stage": item.stage, "requests": [], "input": wire.payload}
    save(path, result)
    original = models.AsyncModels.generate_content

    async def measured(client, *args, **kwargs):
        if len(result["requests"]) >= (2 if item.stage == "space" else 1):
            raise RuntimeError("narration comparison request budget")
        call = {"status": "started"}
        result["requests"].append(call)
        save(path, result)
        start = time.monotonic()
        try:
            response = await original(client, *args, **kwargs)
            call.update(
                status="returned",
                usage=response.usage_metadata.model_dump(mode="json", exclude_none=True)
                if response.usage_metadata
                else None,
                answer_text="".join(
                    p.text
                    for c in response.candidates or []
                    for p in (c.content.parts or [] if c.content else [])
                    if p.text is not None and not p.thought
                ),
            )
            return response
        except BaseException as exc:
            call.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            call["elapsed_s"] = round(time.monotonic() - start, 3)
            save(path, result)

    start = time.monotonic()
    try:
        with patch.object(models.AsyncModels, "generate_content", measured):
            async with asyncio.timeout(policy.TIMEOUT_SECONDS):
                raw = await generate_card_prose(item.stage, wire.payload, wire.schema)
        if item.stage == "space":
            item = item.model_copy(update={"tool_trace": raw.trace})
            if raw.failure_code:
                raise ValueError("space dialogue failed")
            raw = raw.value
        result["answer"] = raw
        item = jobs.validate_output(item, wire.restore(raw))
        result["status"] = "accepted" if item.accepted else "invalid_response"
    except Exception as exc:  # noqa: BLE001 - sanitized failure, never an automatic retry
        result.update(status="failed", error_type=type(exc).__name__)
        item = item.model_copy(update={"failure_code": "provider_failed"})
    result["elapsed_s"] = round(time.monotonic() - start, 3)
    result["job"] = item.model_dump(mode="json")
    save(path, result)
    print(
        json.dumps(
            {
                "stage": item.stage,
                "status": result["status"],
                "model_requests": len(result["requests"]),
                "answer": result.get("answer"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return result


async def run(selected, spec, output):
    from daengs_backend.services.walk_diary.contracts import WritingJob
    from daengs_backend.services.walk_diary.writing.assembly import frozen_card

    cards, requests = [], 0
    for case, scene, stamp, space, action in selected:
        adopted = {}
        for item in (space, action):
            if item is None:
                continue
            result = await attempt(item, output / f"case-{case['index']}-{item.stage}.json")
            requests += len(result["requests"])
            if "job" not in result:
                raise ValueError("interrupted attempt; retained without retry")
            adopted[item.stage] = WritingJob.model_validate(result["job"])
        card = frozen_card(scene, stamp, adopted["space"], adopted.get("action"))
        before = next(c["before"] for c in spec["cases"] if c["card_id"] == scene.id)
        if bool(card.writing.actions) != (action is not None):
            raise ValueError("action escaped its pin")
        if card.writing.original_text != before["original_text"]:
            raise ValueError("original note changed")
        cards.append({"index": case["index"], "card": card.model_dump(mode="json")})
    save(
        output / "result.json",
        {
            "cards": cards,
            "new_model_requests": requests,
            "new_public_api_calls": 0,
            "new_title_requests": 0,
        },
    )


def render(output):
    spec, result = read(output / "input.json"), read(output / "result.json")
    sections = []
    for case, value in zip(spec["cases"], result["cards"], strict=True):
        columns = []
        for label, writing in (
            ("이전 · 공간 + 행동", case["before"]),
            ("보호자 시점 적용", value["card"]["writing"]),
        ):
            body = html.escape(writing["space"]["text"])
            actions = "".join(
                '<p class="action">' + html.escape(a["text"]) + "</p>" for a in writing["actions"]
            )
            columns.append(f"<article><h3>{label}</h3><p>{body}</p>{actions}</article>")
        space_attempt = read(output / f"case-{case['index']}-space.json")
        debug = {
            "space_actual_supply": space_attempt["job"].get("tool_trace"),
            "action_input": case["inputs"].get("action"),
            "assembled": value["card"],
        }
        sections.append(
            f"<section><h2>{case['index']:02d} · {html.escape(case['label'])}</h2>"
            '<div class="pair">' + "".join(columns) + "</div>"
            "<details><summary>개발자용 · 입력과 조립 결과</summary><pre>"
            + html.escape(json.dumps(debug, ensure_ascii=False, indent=2))
            + "</pre></details></section>"
        )
    page = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>보호자 시점으로 읽는 산책</title>
<style>body{margin:0;background:#f4f3ef;color:#26372d;font-family:system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:40px 24px}header p{line-height:1.8;color:#56645d}
h2{font-size:19px;margin-top:36px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}
article{background:white;border:1px solid #d9ded6;border-radius:14px;padding:24px}
h3{font-size:13px;color:#3c6652}article p{font-size:19px;line-height:1.9;white-space:pre-wrap}
.action{border-left:3px solid #78a889;padding-left:14px}details{margin-top:18px}
summary{cursor:pointer;color:#65736a;font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
@media(max-width:720px){.pair{grid-template-columns:1fr}}</style><main><header>
<h1>보호자 시점으로 읽는 산책</h1><p>같은 공간·행동 재료에 보호자 시점과 실제 동행을 연결했다.
왼쪽은 저장된 이전 문장 그대로, 오른쪽은 바뀐 입력과 작성 예시를 적용한 Gemini 결과다.
행동은 핀이 있는 두 번째 장면에만 붙였다.</p>
<p>공공자료는 실제 저장 응답이며 GPS·행동·메모는 합성 시나리오다.
제목은 비교용 장면 이름이다. 입력 맥락과 프롬프트를 함께 바꾼 비교다.</p>"""
    page += f"<p>새 Gemini 요청 {result['new_model_requests']}회 · 공공 API·제목 재호출 0회</p></header>"
    page += "".join(sections) + "</main></html>"
    (output / "preview.html").write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    if args.render_only:
        render(args.output)
        return
    if not all((args.source, args.comparison, args.previous)):
        parser.error("source, comparison and previous are required")
    configure(args.env_file if args.run else None)
    selected, spec = prepare_cases(args.source, args.comparison, args.previous)
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "input.json"
    if path.exists() and read(path) != spec:
        parser.error("saved experiment input differs")
    save(path, spec)
    if args.run:
        asyncio.run(run(selected, spec, args.output))
        render(args.output)
    else:
        print(json.dumps({"prepared_scenes": len(selected), "new_model_requests": 0}))


if __name__ == "__main__":
    main()
