"""Production card DAG: prepare space || write actions -> freeze bodies -> title batch.

No writer reads another body's draft. Only the title strategy sees adopted bodies.
The existing generation lease owns publication; this module cannot publish or retry it.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, JsonValue

from daengs_backend.services.walk_diary_base_board import with_scene_backgrounds
from daengs_backend.services.walk_diary_card_prompts import PROMPTS
from daengs_backend.services.walk_diary_deadline import publication_deadline
from daengs_walk.diary_board_output import PublishedBoard, publish_board
from daengs_walk.diary_card_narrative import CardNarrative, CardPart, content_revision
from daengs_walk.diary_input import DiaryContract, Digest, Identifier, digest
from daengs_walk.diary_output import BackgroundPiece
from daengs_walk.diary_scene_backgrounds import SceneBackgroundSnapshot
from daengs_walk.diary_scene_input import action_anchor
from daengs_walk.diary_space_slots import writing_facts

MODEL = "gemini-3.1-flash-lite"
MAX_CARDS = 12
MAX_INPUT_BYTES = 32_000
TIMEOUT_SECONDS = 15.0
TITLE_RESERVE_SECONDS = 3.0
_late_tasks = set()
LAND_WORDS = {
    "도로": "길",
    "자연초지": "풀밭",
    "기타초지": "풀밭",
    "하천": "물길",
    "해양수": "바다",
    "활엽수림": "숲",
    "침엽수림": "숲",
    "혼효림": "숲",
    "내륙습지": "습지",
    "호소": "호수·저수지",
    "기타나지": "드러난 땅",
}


def writing_version():
    return {
        "policy": "independent-card-writing-v1",
        "model": MODEL,
        "prompts": {key: digest(value) for key, value in PROMPTS.items()},
        "timeout_s": TIMEOUT_SECONDS,
        "title_reserve_s": TITLE_RESERVE_SECONDS,
        "card_limit": MAX_CARDS,
        "input_bytes": MAX_INPUT_BYTES,
        "concurrency": 4,
        "land_words": LAND_WORDS,
    }


class SpaceProse(DiaryContract):
    card_id: Identifier
    request_revision: Digest
    text: str = Field(max_length=220)
    evidence_ids: tuple[Identifier, ...] = Field(max_length=17)


class ActionProse(DiaryContract):
    card_id: Identifier
    request_revision: Digest
    text: str = Field(min_length=1, max_length=140)
    action_id: Identifier


class CardTitle(DiaryContract):
    card_id: Identifier
    content_revision: Digest
    text: str = Field(min_length=1, max_length=80)


class CardTitles(DiaryContract):
    titles: tuple[CardTitle, ...] = Field(max_length=MAX_CARDS)


class WritingJob(DiaryContract):
    stage: Literal["space", "action", "title"]
    request_revision: Digest
    request: dict[str, JsonValue]
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    # Only accepted output is retained; failures never persist raw provider errors.
    accepted: dict[str, JsonValue] | None = None
    failure_code: Literal["provider_failed", "invalid_response", "budget_exceeded"] | None = None


class CardWritingResult(DiaryContract):
    format: Literal["diary-card-writing-v1"] = "diary-card-writing-v1"
    input_revision: Digest
    plan_revision: Digest
    slot_revision: Digest
    writer_version: Digest
    bundle: PublishedBoard
    jobs: tuple[WritingJob, ...]
    scene_backgrounds: SceneBackgroundSnapshot | None = None


def job(stage, payload):
    # Only this strategy's actual dependencies belong in its revision, never the whole board.
    revision = digest({"strategy": digest(PROMPTS[stage]), "model": MODEL, "input": payload})
    return WritingJob(
        stage=stage, request_revision=revision, request={**payload, "request_revision": revision}
    )


def common_context(base):
    names = dict(base.input.pet_names)
    return {
        "record_kind": "guardian_walk_diary",
        "companions": [
            {"id": pet_id, "name": names.get(pet_id)} for pet_id in base.input.source.pet_ids
        ],
    }


def action_job(base, scene):
    anchor = action_anchor(scene)
    if anchor is None:
        return None
    pet_id = scene.core.record.content.pet_id
    if pet_id is not None and pet_id not in base.input.source.pet_ids:
        raise ValueError("action actor is outside this walk")
    actor = {"id": pet_id, "name": dict(base.input.pet_names).get(pet_id)}
    return job(
        "action",
        {
            "card_id": scene.id,
            "event_at": scene.anchor.event_at.isoformat(),
            "walk_context": common_context(base),
            "action": {**anchor, "actor": actor},
        },
    )


def space_job(base, scene, stamp):
    # Motion/actor/notes are not spatial observations. They stay in their source records.
    materials, evidence = [], {}
    for e in stamp.materials():
        if e.part not in {"space", "environment"}:
            continue
        facts = {k: v for k, v in writing_facts(e).items() if k != "retrieved_at"}
        if e.facts.get("source") == "land_cover" and isinstance(facts.get("material"), dict):
            facts["material"] = {k: LAND_WORDS.get(v, v) for k, v in facts["material"].items()}
        identity = "material:" + digest([e.role, facts])
        materials.append({"id": identity, "role": e.role, "facts": facts})
        evidence[identity] = e.model_dump(mode="json")
    materials.sort(key=lambda material: material["id"])
    known = {e.role for e in stamp.materials()}
    backgrounds = [
        b
        for b in (
            *base.input.source.backgrounds,
            *(base.scene_backgrounds.backgrounds if base.scene_backgrounds else ()),
        )
        if b.target == scene.core_ref
    ]

    def state(providers, available):
        matching = [b for b in backgrounds if b.provider in providers]
        return "known" if available else (matching[-1].status if matching else "unavailable")

    def ground(material):
        relation = material["facts"].get("relation")
        return isinstance(relation, dict) and relation.get("kind") == "land_cover_at_query_point"

    value = job(
        "space",
        {
            "card_id": scene.id,
            "walk_context": common_context(base),
            "anchor": scene.anchor.model_dump(mode="json"),
            "sources": {
                "sgis": state({"sgis"}, "scene_address_reference" in known),
                "egis": state(
                    {"public-normalized-land_cover"},
                    any(e.facts.get("source") == "land_cover" for e in stamp.materials()),
                ),
                "environment": "known"
                if any(e.part == "environment" for e in stamp.materials())
                else "unavailable",
            },
            "materials": materials,
            "scene_structure": {
                "current_ground": [m["id"] for m in materials if ground(m)],
                "administrative_location": [
                    m["id"] for m in materials if m["role"] == "scene_address_reference"
                ],
                "local_details": [
                    m["id"]
                    for m in materials
                    if m["role"] != "scene_address_reference" and not ground(m)
                ],
            },
        },
    )
    return value.model_copy(update={"evidence": evidence})


async def generate_card_prose(stage, payload, schema):
    from google import genai
    from google.genai import types

    from daengs_backend.config import settings

    key = settings.gemini_api_key.get_secret_value().strip()
    if not key:
        raise ValueError("diary_writer_not_configured")
    async with genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            timeout=int(TIMEOUT_SECONDS * 1000), retry_options=types.HttpRetryOptions(attempts=1)
        ),
    ).aio as client:
        response = await client.models.generate_content(
            model=MODEL,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=PROMPTS[stage],
                temperature=0,
                candidate_count=1,
                max_output_tokens=2048 if stage == "title" else 512,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        return response.text


def discard(task):
    if task.done():
        if not task.cancelled():
            task.exception()
        return
    _late_tasks.add(task)

    def consume(done):
        _late_tasks.discard(done)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(consume)
    task.cancel()


async def until(awaitable, deadline):
    """A cancellation-resistant provider has no authority after this cutoff."""
    if asyncio.get_running_loop().time() >= deadline:
        awaitable.close()
        raise TimeoutError
    task = asyncio.create_task(awaitable)
    try:
        left = max(0, deadline - asyncio.get_running_loop().time())
        done, _ = await asyncio.wait({task}, timeout=left)
        if not done or asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError
        return task.result()
    finally:
        discard(task)


async def execute(item, generate, semaphore, deadline):
    schema = {"space": SpaceProse, "action": ActionProse, "title": CardTitles}[item.stage]

    async def call():
        async with semaphore:
            return await generate(item.stage, item.request, schema.model_json_schema())

    try:
        if len(json.dumps(item.request, ensure_ascii=False).encode()) > MAX_INPUT_BYTES:
            raise TimeoutError
        raw = await until(call(), deadline)
    except TimeoutError:
        return item.model_copy(update={"failure_code": "budget_exceeded"})
    except Exception:  # noqa: BLE001 - provider exception text must not enter storage
        return item.model_copy(update={"failure_code": "provider_failed"})
    try:
        if isinstance(raw, str):
            if len(raw.encode()) > 64_000:
                raise ValueError("response exceeds budget")
            raw = json.loads(raw)
        output = schema.model_validate(raw)
        if item.stage != "title":
            if (
                output.card_id != item.request["card_id"]
                or output.request_revision != item.request_revision
            ):
                raise ValueError("writing result belongs to another request")
            if item.stage == "action":
                if output.action_id != item.request["action"]["id"] or not output.text.strip():
                    raise ValueError("action changed")
            else:
                refs = set(output.evidence_ids)
                if (
                    len(refs) != len(output.evidence_ids)
                    or not refs <= {e["id"] for e in item.request["materials"]}
                    or bool(output.text.strip()) != bool(refs)
                ):
                    raise ValueError("space citation changed")
                names = [c["name"] for c in item.request["walk_context"]["companions"] if c["name"]]
                if any(name in output.text for name in names):
                    raise ValueError("companion name leaked into space")
        else:
            expected = {c["card_id"]: c["content_revision"] for c in item.request["cards"]}
            if len({t.card_id for t in output.titles}) != len(output.titles) or any(
                expected.get(t.card_id) != t.content_revision or not t.text.strip()
                for t in output.titles
            ):
                raise ValueError("title changed card identity/content")
        return item.model_copy(update={"accepted": output.model_dump(mode="json")})
    except (ValueError, TypeError, KeyError):
        return item.model_copy(update={"failure_code": "invalid_response"})


def places_for(stamp, previous):
    reference = stamp.location_reference
    if reference is None:
        return previous
    # Reuse the existing SGIS normalization. APP deliberately displays facts.dong only.
    return (
        BackgroundPiece(
            id=reference.id,
            background_id=reference.source_id,
            kind="place_reference",
            schema_version="sgis-dong-v1",
            facts=reference.facts,
        ),
    )


def frozen_card(scene, stamp, space_result, action_result):
    places = places_for(stamp, scene.place_reference)
    dong = next((p.facts.get("dong") for p in places if p.facts.get("dong")), None)
    default = (
        f"{dong}에 남긴 산책 기록이다."
        if dong
        else ("산책 중 기록한 장소다." if scene.anchor.point else "위치 정보가 없는 산책 기록이다.")
    )
    generated = space_result.accepted if space_result else None
    space = CardPart(
        text=generated["text"].strip() if generated and generated["text"].strip() else default,
        origin="generated" if generated and generated["text"].strip() else "fallback",
    )
    actions = ()
    if action_result:
        record = action_result.request["action"]
        accepted = action_result.accepted
        actions = (
            CardPart(
                text=accepted["text"].strip()
                if accepted
                else f"{record['material']['무엇을']} 행동을 기록했다.",
                origin="generated" if accepted else "fallback",
                action_id=record["id"],
                actor_id=record["actor"]["id"],
            ),
        )
    original = (
        scene.body if scene.user_record and scene.user_record.kind in {"note", "photo"} else None
    )
    revision = content_revision(
        scene.id,
        scene.anchor.model_dump(mode="json"),
        [p.model_dump(mode="json") for p in places],
        space.model_dump(mode="json"),
        [a.model_dump(mode="json") for a in actions],
        original,
    )
    narrative = CardNarrative(
        content_revision=revision,
        space=space,
        actions=actions,
        original_text=original,
        title_origin="fallback",
        title_based_on_content_revision=revision,
    )
    return scene.model_copy(
        update={"body": narrative.body(), "writing": narrative, "place_reference": places}
    )


async def write_cards(source, base, *, generate=None, collector=None):
    generate = generate or generate_card_prose
    if source.revision() != base.board.input_revision:
        raise ValueError("card writer requires its prepared source")
    loop = asyncio.get_running_loop()
    budget = TIMEOUT_SECONDS
    outer = publication_deadline.get()
    if outer:
        budget = min(budget, max(0, (outer - datetime.now(UTC)).total_seconds() - 0.15))
    end = loop.time() + budget
    bodies_end = end - min(TITLE_RESERVE_SECONDS, max(0, budget / 3))
    semaphore = asyncio.Semaphore(4)
    cache = {
        j.request_revision: j
        for raw in base.cached_jobs
        if (j := WritingJob.model_validate(raw)).accepted
    }

    async def run(item, deadline):
        previous = cache.get(item.request_revision)
        if previous and previous.stage == item.stage and previous.request == item.request:
            # Rebind current evidence/provenance; cached prose is never an extra model input.
            return item.model_copy(update={"accepted": previous.accepted})
        return await execute(item, generate, semaphore, deadline)

    actions = {s.id: action_job(base, s) for s in base.board.scenes}
    tasks = {
        key: asyncio.create_task(run(item, bodies_end)) for key, item in actions.items() if item
    }
    prepared = base
    collection = None
    try:
        if collector:
            try:
                collection = await until(collector(base.board), min(bodies_end, loop.time() + 4.5))
                prepared = with_scene_backgrounds(base, collection)
            except (TimeoutError, ValueError):
                collection = None
            except Exception:  # noqa: BLE001 - collection failure does not cancel action writing
                collection = None
        space_inputs = [
            space_job(prepared, s, stamp)
            for s, stamp in zip(prepared.board.scenes, prepared.slots.stamps, strict=True)
        ]
        space_results = await asyncio.gather(*(run(item, bodies_end) for item in space_inputs))
        action_results = {key: await task for key, task in tasks.items()}
        for key, item in actions.items():
            if item and key not in action_results:
                action_results[key] = item.model_copy(update={"failure_code": "budget_exceeded"})
    finally:
        for task in tasks.values():
            discard(task)
    public = publish_board(prepared.board, prepared.plan)
    cards = [
        frozen_card(scene, stamp, written, action_results.get(scene.id))
        for scene, stamp, written in zip(
            public.scenes, prepared.slots.stamps, space_results, strict=True
        )
    ]
    jobs = [*space_results, *action_results.values()]
    title_inputs = [
        job(
            "title",
            {
                "cards": [
                    {
                        "card_id": c.id,
                        "content_revision": c.writing.content_revision,
                        "space": c.writing.space.model_dump(mode="json"),
                        "actions": [a.model_dump(mode="json") for a in c.writing.actions],
                        "place_reference": [p.model_dump(mode="json") for p in c.place_reference],
                        "event_at": c.anchor.event_at.isoformat(),
                    }
                    for c in cards[start : start + MAX_CARDS]
                ]
            },
        )
        for start in range(0, len(cards), MAX_CARDS)
    ]
    title_results = await asyncio.gather(*(run(item, end) for item in title_inputs))
    jobs.extend(title_results)
    titles = {
        t["card_id"]: t
        for result in title_results
        if result.accepted
        for t in result.accepted["titles"]
    }
    cards = [
        c.model_copy(
            update={
                "title": titles[c.id]["text"].strip(),
                "writing": c.writing.model_copy(update={"title_origin": "generated"}),
            }
        )
        if c.id in titles
        else c
        for c in cards
    ]
    accepted = any(
        c.writing.title_origin == "generated"
        or c.writing.space.origin == "generated"
        or any(a.origin == "generated" for a in c.writing.actions)
        for c in cards
    )
    bundle = PublishedBoard.model_validate(
        {
            **public.model_dump(mode="json"),
            "scenes": [c.model_dump(mode="json") for c in cards],
            "model_status": "accepted" if accepted else "unavailable",
            "failure_code": None
            if accepted
            else next((j.failure_code for j in jobs if j.failure_code), "provider_failed"),
        }
    )
    return CardWritingResult(
        input_revision=source.revision(),
        plan_revision=prepared.plan.revision(),
        slot_revision=prepared.slots.revision(),
        writer_version=digest(writing_version()),
        bundle=bundle,
        jobs=tuple(jobs),
        scene_backgrounds=collection,
    )


def complete_cards(prepared, output):
    value = CardWritingResult.model_validate(
        output.model_dump(mode="json") if isinstance(output, CardWritingResult) else output
    )
    base = prepared.board
    if (
        value.input_revision != base.board.input_revision
        or value.plan_revision != base.plan.revision()
        or value.slot_revision != base.slots.revision()
        or value.writer_version != digest(writing_version())
    ):
        raise ValueError("card writing returned another snapshot")
    public = publish_board(base.board, base.plan)
    if [s.id for s in value.bundle.scenes] != [s.id for s in public.scenes]:
        raise ValueError("writer changed selected cards")
    for old, new in zip(public.scenes, value.bundle.scenes, strict=True):
        unchanged = {"title", "body", "writing", "place_reference"}
        if old.model_dump(exclude=unchanged) != new.model_dump(exclude=unchanged):
            raise ValueError("writer changed record identity")
        expected_original = (
            old.body if old.user_record and old.user_record.kind in {"note", "photo"} else None
        )
        if new.writing is None or new.writing.original_text != expected_original:
            raise ValueError("writer changed original text")
    return value.bundle
