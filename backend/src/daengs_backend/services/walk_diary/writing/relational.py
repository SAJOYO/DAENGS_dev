"""Separate space/action calls. No mixed scene writer exists in this strategy."""

import asyncio
import json
import time
from copy import deepcopy

import httpx

from daengs_backend.services.walk_diary.writing import policy
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.relational.contracts import VERSION, WriterAnswer, WriterTask

PROMPTS = {
    "space": """입력은 공간 서술 계획이다. initial은 현재 배경, change는 확정된 공간 관계의 차이를 중심으로 보호자가 돌아보는 한국어 과거형 1~2문장을 쓴다. required_relation_ids의 의미를 전달한다. 비교 범위는 관계에 적힌 범위까지다. 동행의 행동이나 이동 사건을 쓰는 작업이 아니다. 주소 도로명은 위치를 식별하는 단서다. 현재 근거와 관계를 자연스럽게 표현하고 새로운 풍경·감각·경험을 만들지 않는다. JSON text, evidence_ids로 실제 사용한 근거를 반환한다.""",
    "action": """입력은 현재 행동핀과 현재 장소·핀 시각의 이동 맥락이다. 기록된 주체의 행동을 한국어 과거형 한 문장으로 남긴다. 현재 장소는 행동을 구별하는 데 도움이 될 때만 사용한다. 이동은 보호자 기기 맥락이며 행동의 주체·원인·지속시간이 아니다. 기록되지 않은 행동 대상·정지·감정·동기를 만들지 않는다. 앞선 장면과 비교하거나 사건을 연결하는 작업이 아니다. JSON text, evidence_ids로 실제 사용한 근거를 반환하고 행동의 ID는 반드시 포함한다.""",
    "title": """확정된 산책 본문과 관측을 나타내는 짧은 한국어 제목을 30자 이내로 쓴다. 제공되지 않은 경험·감정·장소 관계는 더하지 않는다. JSON title로 답한다.""",
}


async def generate_relation_part(stage, payload, schema):
    from google import genai
    from google.genai import types

    from daengs_backend.config import settings

    async with genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(
            timeout=15000, retry_options=types.HttpRetryOptions(attempts=1)
        ),
    ).aio as client:
        response = await client.models.generate_content(
            model=policy.MODEL,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=PROMPTS[stage],
                temperature=0,
                candidate_count=1,
                max_output_tokens=512,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        return response.text


def validate_prepared(prepared):
    snapshot = prepared["snapshot"]
    if snapshot["version"] != VERSION or digest(snapshot) != prepared["revision"]:
        raise ValueError("relation snapshot changed")
    frames = {f["scene_id"]: f for f in snapshot["frames"]}
    tasks = []
    for plan in snapshot["plans"]:
        if digest({k: v for k, v in plan.items() if k != "revision"}) != plan["revision"]:
            raise ValueError("scene plan changed")
        frame = frames[plan["scene_id"]]
        if bool(plan["action_task"]) != bool(frame["action"]):
            raise ValueError("action task must match current pin")
        for stage in ("space", "action"):
            value = plan[stage + "_task"]
            if value is None:
                continue
            task = WriterTask.model_validate(value)
            if task.stage != stage or task.scene_id != plan["scene_id"]:
                raise ValueError("writer task belongs to another stage or scene")
            if stage == "action" and (
                task.payload["recorded_action"] != frame["action"]["recorded_action"]
                or task.payload["pin_at"] != frame["anchor"]["event_at"]
                or task.payload.get("movement_context") != frame["action"].get("movement_context")
            ):
                raise ValueError("action task must use current pin only")
            tasks.append(task)
    if len({t.id for t in tasks}) != len(tasks):
        raise ValueError("duplicate writing tasks")
    return tasks


async def write_relational_diary(prepared, *, send=None):
    from google.genai.errors import APIError

    frozen = deepcopy(prepared)
    tasks = validate_prepared(frozen)
    send = send or generate_relation_part
    semaphore = asyncio.Semaphore(4)
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "maxLength": 220},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text", "evidence_ids"],
    }

    async def run(task):
        record = {
            "task_id": task.id,
            "scene_id": task.scene_id,
            "stage": task.stage,
            "revision": task.revision,
        }
        async with semaphore:
            start = time.monotonic()
            try:
                raw = await send(task.stage, task.payload, schema)
                record["raw_text"] = raw
                answer = WriterAnswer.model_validate(json.loads(raw))
                refs = set(answer.evidence_ids)
                allowed = {m["id"] for m in task.payload["current_space"]}
                road = task.payload.get("road_reference")
                if road:
                    allowed.add(road["id"])
                if task.stage == "space":
                    allowed.update(r["id"] for r in task.payload["relations"])
                    required = set(task.payload["required_relation_ids"])
                else:
                    required = {task.payload["recorded_action"]["id"]}
                    allowed.update(required)
                    motion = task.payload.get("movement_context")
                    if motion:
                        allowed.add(motion["id"])
                if not answer.text.strip() or not required <= refs <= allowed:
                    raise ValueError("invalid writer references")
                record.update(status="returned", answer=answer.model_dump(mode="json"))
            except (
                APIError,
                httpx.HTTPError,
                TimeoutError,
                ValueError,
                KeyError,
                TypeError,
            ) as exc:
                record.update(status="failed", error_type=type(exc).__name__)
            record["elapsed_s"] = round(time.monotonic() - start, 2)
        return record

    results = await asyncio.gather(*(run(t) for t in tasks))
    return {
        "snapshot_revision": frozen["revision"],
        "model": policy.MODEL,
        "prompt_revision": digest(PROMPTS),
        "results": results,
        "semantic_validation": "not_performed; references do not prove meaning",
    }
