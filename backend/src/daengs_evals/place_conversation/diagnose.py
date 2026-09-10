"""Probe current answer facts. For old captured-plan ablations, use commit ac2b062."""

import argparse
import asyncio
import json
from pathlib import Path

from daengs_place.place.conversation.answer import compose_answer
from daengs_place.place.conversation.contract import AnswerRequest, PrepareRequest
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.service import ConversationService

from .fixtures import FixtureSearcher, initial_state
from .runner import DATA, read_cases, write_json


async def evidence_gap(run):
    case = next(c for c in read_cases(DATA / "cases.v1.jsonl") if c["id"] == "PC-E16")
    fixtures = json.loads((DATA / "fixtures.v1.json").read_text(encoding="utf-8"))
    records = []
    for parking in (True, False, None):
        setup = {**case["setup"], "selected": "f81d"}
        variant = json.loads(json.dumps(fixtures))
        variant["standard"][0].update(ref="f81d", name="테스트 장소", parking=parking)
        searcher = FixtureSearcher(setup, variant)
        state = await initial_state(setup, searcher)

        class Explain:
            async def plan(self, request):
                return Interpretation(goal="explain", asked_attributes=("parking", "quiet", "free"))

        class Injected:
            async def answer(self, request):
                raise AssertionError("unverified prose generator was invoked")

        prepared = await ConversationService(Explain(), searcher=searcher).prepare(
            None,
            PrepareRequest(mode="chat", query="여기 주차 돼? 조용하고 무료야?", previous=state),
        )
        answer = await compose_answer(
            AnswerRequest(
                query="여기 주차 돼? 조용하고 무료야?",
                committed_revision=prepared.state.revision,
                prepared=prepared,
            ),
            Injected(),
        )
        records.append(
            {
                "parking": parking,
                "receipt": prepared.receipt.model_dump(mode="json"),
                "answer": answer.model_dump(mode="json"),
            }
        )
    path = run / "policy-evidence-probes.json"
    if path.exists():
        raise ValueError("probe already exists; never overwrite observations")
    write_json(
        path, {"boundary": "controlled opaque-ID facts; no live provider", "records": records}
    )
    print("wrote:", path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--evidence-gap", action="store_true")
    args = parser.parse_args()
    if not args.evidence_gap:
        parser.error(
            "Historical plan replay requires checkout ac2b062; use --evidence-gap for current policy."
        )
    asyncio.run(evidence_gap(args.run))


if __name__ == "__main__":
    main()
