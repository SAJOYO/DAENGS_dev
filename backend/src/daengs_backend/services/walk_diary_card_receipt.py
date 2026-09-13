"""Stored orchestration receipt; historical slot receipts remain readable unchanged."""

from typing import Literal

from pydantic import JsonValue, model_validator

from daengs_backend.services.walk_diary_card_writing import CardWritingResult
from daengs_walk.diary_input import DiaryContract, Digest, digest


class StoredCardWriting(DiaryContract):
    format: Literal["stored-card-writing-v1"] = "stored-card-writing-v1"
    generation_revision: Digest
    writer: dict[str, JsonValue]
    result: CardWritingResult

    @model_validator(mode="after")
    def intact(self):
        if digest(self.writer) != self.result.writer_version:
            raise ValueError("stored card writer version changed")
        for item in self.result.jobs:
            payload = {k: v for k, v in item.request.items() if k != "request_revision"}
            expected = digest(
                {
                    "strategy": self.writer["prompts"][item.stage],
                    "model": self.writer["model"],
                    "input": payload,
                }
            )
            if (
                expected != item.request_revision
                or item.request.get("request_revision") != expected
            ):
                raise ValueError("stored job request changed")
            if item.accepted and item.failure_code:
                raise ValueError("failed job cannot carry accepted output")
        return self

    def require_bundle(self, bundle, generation_revision):
        if (
            generation_revision != self.generation_revision
            or bundle != self.result.bundle
            or bundle.input_revision != self.result.input_revision
            or bundle.plan_revision != self.result.plan_revision
        ):
            raise ValueError("stored card writing belongs to another publication")
        spaces = {j.request["card_id"]: j for j in self.result.jobs if j.stage == "space"}
        actions = {j.request["card_id"]: j for j in self.result.jobs if j.stage == "action"}
        title_jobs = [j for j in self.result.jobs if j.stage == "title"]
        if len(spaces) != len(bundle.scenes) or len(spaces) + len(actions) + len(title_jobs) != len(
            self.result.jobs
        ):
            raise ValueError("missing/duplicate writing job")
        titles = {t["card_id"]: t for j in title_jobs if j.accepted for t in j.accepted["titles"]}
        title_inputs = {c["card_id"]: c for j in title_jobs for c in j.request["cards"]}
        for scene in bundle.scenes:
            parts = scene.writing
            if parts is None or scene.id not in spaces:
                raise ValueError("missing card parts")
            space = spaces[scene.id]
            if parts.space.origin == "generated" and (
                not space.accepted or parts.space.text != space.accepted["text"].strip()
            ):
                raise ValueError("space differs from its accepted job")
            if bool(parts.actions) != (scene.id in actions):
                raise ValueError("action job and behavior pin differ")
            for action in parts.actions:
                result = actions[scene.id]
                source = result.request["action"]
                if action.action_id != source["id"] or action.actor_id != source["actor"]["id"]:
                    raise ValueError("stored actor changed")
                if action.origin == "generated" and (
                    not result.accepted or action.text != result.accepted["text"].strip()
                ):
                    raise ValueError("action differs from its accepted job")
            title = titles.get(scene.id)
            if parts.title_origin == "generated" and (
                not title
                or title["text"].strip() != scene.title
                or title["content_revision"] != parts.content_revision
            ):
                raise ValueError("title differs from its accepted job")
            supplied = title_inputs.get(scene.id)
            if supplied and (
                supplied["content_revision"] != parts.content_revision
                or supplied["space"] != parts.space.model_dump(mode="json")
                or supplied["actions"] != [a.model_dump(mode="json") for a in parts.actions]
                or supplied["place_reference"]
                != [p.model_dump(mode="json") for p in scene.place_reference]
            ):
                raise ValueError("title did not read the adopted card bodies")
