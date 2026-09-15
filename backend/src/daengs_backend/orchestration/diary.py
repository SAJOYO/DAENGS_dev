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
from daengs_backend.services.walk_diary import contracts
from daengs_backend.services.walk_diary.collection.application import collect_for_writing
from daengs_backend.services.walk_diary.deadline import publication_deadline
from daengs_backend.services.walk_diary.model_input import normalize
from daengs_backend.services.walk_diary.writing import assembly, policy
from daengs_backend.services.walk_diary.writing import jobs as card_jobs
from daengs_walk.diary.board.activity import require_activity_transfer
from daengs_walk.diary.board.output import PublishedBoard, publish_board
from daengs_walk.diary.contracts.input import digest


class DiaryState(TypedDict, total=False):
    source: Any
    base: Any
    prepared: Any
    collection: Any
    collection_receipt: Any
    space_results: list
    action_results: dict
    cards: list
    jobs: list
    result: contracts.CardWritingResult


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
        budget = policy.TIMEOUT_SECONDS
        outer = publication_deadline.get()
        if outer is not None:
            budget = min(budget, max(0, (outer - datetime.now(UTC)).total_seconds() - 0.15))
        self.end = loop.time() + budget
        self.bodies_end = self.end - min(policy.TITLE_RESERVE_SECONDS, max(0, budget / 3))
        self.cache = {}
        for raw in base.cached_jobs:
            previous = contracts.WritingJob.model_validate(raw)
            if not previous.accepted:
                continue
            # Reuse only requests made under this model and strategy, including whole-board titles.
            payload = {k: v for k, v in previous.request.items() if k != "request_revision"}
            if previous.request_revision != card_jobs.job(previous.stage, payload).request_revision:
                continue
            previous = card_jobs.validate_output(previous, previous.accepted)
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
        if len(json.dumps(model.payload, ensure_ascii=False).encode()) > policy.MAX_INPUT_BYTES:
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
        return card_jobs.validate_output(item, restored)

    async def actions(self, state):
        base = state["base"]
        jobs = [j for s in base.board.scenes if (j := card_jobs.action_job(base, s)) is not None]
        results = await asyncio.gather(*(self.execute(j, self.bodies_end) for j in jobs))
        return {"action_results": {r.request["card_id"]: r for r in results}}

    async def space(self, state):
        base, receipt = state["base"], None
        prepared = base
        if self.collector:
            prepared, receipt = await collect_for_writing(
                base,
                self.collector,
                self.collection_executor,
                min(self.bodies_end, asyncio.get_running_loop().time() + 4.5),
            )
        inputs = [
            card_jobs.space_job(prepared, s, stamp)
            for s, stamp in zip(prepared.board.scenes, prepared.slots.stamps, strict=True)
        ]
        results = await asyncio.gather(*(self.execute(j, self.bodies_end) for j in inputs))
        return {
            "prepared": prepared,
            "collection": prepared.scene_backgrounds if self.collector else None,
            # Successful acquisition is already preserved in scene_backgrounds. Add a
            # diagnostic receipt only for degraded runs, leaving historical bytes intact.
            "collection_receipt": receipt
            if receipt
            and (receipt.status != "completed" or receipt.application_status != "applied")
            else None,
            "space_results": results,
        }

    async def freeze(self, state):
        prepared = state["prepared"]
        public = publish_board(prepared.board, prepared.plan, prepared.slots)
        cards = [
            assembly.frozen_card(s, stamp, result, state["action_results"].get(s.id))
            for s, stamp, result in zip(
                public.scenes, prepared.slots.stamps, state["space_results"], strict=True
            )
        ]
        return {
            "cards": cards,
            "jobs": [*state["space_results"], *state["action_results"].values()],
        }

    async def titles(self, state):
        batches = card_jobs.title_jobs(state["cards"])
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
        public = publish_board(prepared.board, prepared.plan, prepared.slots)
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
            "result": contracts.CardWritingResult(
                input_revision=state["source"].revision(),
                plan_revision=prepared.plan.revision(),
                slot_revision=prepared.slots.revision(),
                writer_version=digest(policy.writing_version()),
                bundle=bundle,
                jobs=tuple(jobs),
                scene_backgrounds=state["collection"],
                collection_receipt=state["collection_receipt"],
            )
        }
