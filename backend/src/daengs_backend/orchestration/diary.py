"""Diary LangGraph: independent bodies, frozen content, then card titles.

Uses the assistant's JobExecutor, but neither its semantic router nor chat response.
The existing diary publication reservation supplies the outer deadline and remains the
only owner of persistence, source-version checks and publication.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from daengs_backend.orchestration.execution import JobExecutor
from daengs_backend.services import walk_diary_card_writing as writing
from daengs_backend.services.walk_diary_base_board import with_scene_backgrounds
from daengs_backend.services.walk_diary_deadline import publication_deadline
from daengs_backend.services.walk_diary_llm import normalize
from daengs_walk.diary_activity import require_activity_transfer
from daengs_walk.diary_board_output import PublishedBoard, publish_board
from daengs_walk.diary_input import digest


class DiaryState(TypedDict, total=False):
    source: Any
    base: Any
    prepared: Any
    collection: Any
    space_results: list
    action_results: dict
    cards: list
    jobs: list
    result: writing.CardWritingResult


class DiaryOrchestrationService:
    def __init__(self, *, generate, collector=None):
        self.generate = generate
        self.collector = collector

    async def run(self, source, base):
        if source.revision() != base.board.input_revision:
            raise ValueError("card writer requires its prepared source")
        # Invocation-local execution state: concurrent walks never share caches or clocks.
        run = _DiaryRun(self.generate, self.collector, base)
        final = await run.graph.ainvoke(
            {"source": source, "base": base},
            config={"run_name": "diary_orchestration", "tags": ["diary"]},
        )
        return final["result"]


class _DiaryRun:
    def __init__(self, generate, collector, base):
        self.generate, self.collector = generate, collector
        self.executor = JobExecutor(concurrency=4)
        # Collection must not occupy the LLM slots needed by ready action jobs.
        self.collection_executor = JobExecutor()
        loop = asyncio.get_running_loop()
        budget = writing.TIMEOUT_SECONDS
        outer = publication_deadline.get()
        if outer is not None:
            budget = min(budget, max(0, (outer - datetime.now(UTC)).total_seconds() - 0.15))
        self.end = loop.time() + budget
        self.bodies_end = self.end - min(writing.TITLE_RESERVE_SECONDS, max(0, budget / 3))
        self.cache = {}
        for raw in base.cached_jobs:
            previous = writing.WritingJob.model_validate(raw)
            if not previous.accepted:
                continue
            previous = writing.validate_output(previous, previous.accepted)
            if previous.failure_code:
                continue
            self.cache[previous.request_revision] = previous
        builder = StateGraph(DiaryState)
        builder.add_node("space", self.space)
        builder.add_node("actions", self.actions)
        builder.add_node("freeze_card_content", self.freeze)
        builder.add_node("titles", self.titles)
        builder.add_node("assemble", self.assemble)
        builder.add_edge(START, "space")
        builder.add_edge(START, "actions")
        builder.add_edge(["space", "actions"], "freeze_card_content")
        builder.add_edge("freeze_card_content", "titles")
        builder.add_edge("titles", "assemble")
        builder.add_edge("assemble", END)
        self.graph = builder.compile()

    async def execute(self, item, deadline):
        previous = self.cache.get(item.request_revision)
        if previous and previous.stage == item.stage and previous.request == item.request:
            return item.model_copy(
                update={
                    "accepted": previous.accepted,
                    "reused": True,
                    "llm_request": previous.llm_request,
                }
            )
        try:
            model = normalize(item.stage, item.request)
            if item.stage == "action" and item.request.get("movement"):
                require_activity_transfer(item.request, model.payload, model.references)
        except (ValueError, KeyError, TypeError):
            return item.model_copy(update={"failure_code": "invalid_input"})
        if len(json.dumps(model.payload, ensure_ascii=False).encode()) > writing.MAX_INPUT_BYTES:
            return item.model_copy(update={"failure_code": "budget_exceeded"})

        async def invoke():
            nonlocal item
            item = item.model_copy(update={"llm_request": model.payload})
            return await self.generate(item.stage, model.payload, model.schema)

        outcome = await self.executor.run(
            f"{item.stage}:{item.request_revision}",
            invoke,
            deadline=deadline,
        )
        if outcome.status != "ok":
            return item.model_copy(
                update={
                    "failure_code": (
                        "budget_exceeded" if outcome.status == "timeout" else "provider_failed"
                    )
                }
            )
        try:
            restored = model.restore(outcome.value)
        except (ValueError, TypeError, KeyError):
            return item.model_copy(update={"failure_code": "invalid_response"})
        return writing.validate_output(item, restored)

    async def actions(self, state):
        base = state["base"]
        jobs = [j for s in base.board.scenes if (j := writing.action_job(base, s)) is not None]
        results = await asyncio.gather(*(self.execute(j, self.bodies_end) for j in jobs))
        return {"action_results": {r.request["card_id"]: r for r in results}}

    async def space(self, state):
        base, collection = state["base"], None
        prepared = base
        if self.collector:

            async def collect():
                value = await self.collector(base.board)
                return value, with_scene_backgrounds(base, value)

            outcome = await self.collection_executor.run(
                "diary:space_collection",
                collect,
                deadline=min(self.bodies_end, asyncio.get_running_loop().time() + 4.5),
            )
            if outcome.status == "ok":
                collection, prepared = outcome.value
        inputs = [
            writing.space_job(prepared, s, stamp)
            for s, stamp in zip(prepared.board.scenes, prepared.slots.stamps, strict=True)
        ]
        results = await asyncio.gather(*(self.execute(j, self.bodies_end) for j in inputs))
        return {"prepared": prepared, "collection": collection, "space_results": results}

    async def freeze(self, state):
        prepared = state["prepared"]
        public = publish_board(prepared.board, prepared.plan)
        cards = [
            writing.frozen_card(s, stamp, result, state["action_results"].get(s.id))
            for s, stamp, result in zip(
                public.scenes, prepared.slots.stamps, state["space_results"], strict=True
            )
        ]
        return {
            "cards": cards,
            "jobs": [*state["space_results"], *state["action_results"].values()],
        }

    async def titles(self, state):
        batches = writing.title_jobs(state["cards"])
        results = await asyncio.gather(*(self.execute(j, self.end) for j in batches))
        titles = {t["card_id"]: t for r in results if r.accepted for t in r.accepted["titles"]}
        cards = [
            c.model_copy(
                update={
                    "title": titles[c.id]["text"].strip(),
                    "writing": c.writing.model_copy(update={"title_origin": "generated"}),
                }
            )
            if c.id in titles
            else c
            for c in state["cards"]
        ]
        return {"cards": cards, "jobs": [*state["jobs"], *results]}

    async def assemble(self, state):
        prepared, cards, jobs = state["prepared"], state["cards"], state["jobs"]
        accepted = any(
            c.writing.title_origin == "generated"
            or c.writing.space.origin == "generated"
            or any(a.origin == "generated" for a in c.writing.actions)
            for c in cards
        )
        public = publish_board(prepared.board, prepared.plan)
        bundle = PublishedBoard.model_validate(
            {
                **public.model_dump(mode="json"),
                "scenes": [c.model_dump(mode="json") for c in cards],
                "model_status": "accepted" if accepted else "unavailable",
                "failure_code": None
                if accepted
                else next(
                    (
                        j.failure_code
                        for j in jobs
                        if j.failure_code
                        in {"provider_failed", "invalid_response", "budget_exceeded"}
                    ),
                    "provider_failed",
                ),
            }
        )
        return {
            "result": writing.CardWritingResult(
                input_revision=state["source"].revision(),
                plan_revision=prepared.plan.revision(),
                slot_revision=prepared.slots.revision(),
                writer_version=digest(writing.writing_version()),
                bundle=bundle,
                jobs=tuple(jobs),
                scene_backgrounds=state["collection"],
            )
        }
