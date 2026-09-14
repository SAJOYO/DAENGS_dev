"""Internal writing jobs -> small model requests -> invocation-bound internal results."""

import json
from dataclasses import dataclass

from pydantic import Field

from daengs_backend.services.walk_diary.model_materials import location, material
from daengs_walk.diary.board.action_context import require_action
from daengs_walk.diary.board.activity import activity_projection
from daengs_walk.diary.contracts.input import DiaryContract

VERSION = "diary-prose-input-v6"


class SpaceAnswer(DiaryContract):
    text: str = Field(max_length=220)
    evidence_ids: tuple[str, ...] = Field(max_length=17)


class ActionAnswer(DiaryContract):
    text: str = Field(min_length=1, max_length=140)


class ActivityAnswer(DiaryContract):
    text: str = Field(min_length=1, max_length=220)
    evidence_ids: tuple[str, ...]


class TitleAnswer(DiaryContract):
    id: str
    text: str = Field(min_length=1, max_length=80)


class TitlesAnswer(DiaryContract):
    titles: tuple[TitleAnswer, ...] = Field(max_length=12)


class FinalTitlesAnswer(DiaryContract):
    titles: tuple[TitleAnswer, ...]


class WholeTitleAnswer(DiaryContract):
    title: str = Field(min_length=1, max_length=80)


@dataclass(frozen=True)
class ModelRequest:
    stage: str
    payload: dict
    internal: dict
    references: dict

    @property
    def schema(self):
        if self.stage == "action" and self.internal.get("movement"):
            return ActivityAnswer.model_json_schema()
        return {
            "space": SpaceAnswer,
            "action": ActionAnswer,
            "title": TitlesAnswer,
            "scene_titles": FinalTitlesAnswer,
            "whole_title": WholeTitleAnswer,
        }[self.stage].model_json_schema()

    def restore(self, raw):
        if isinstance(raw, str):
            if len(raw.encode()) > 64_000:
                raise ValueError("response exceeds budget")
            raw = json.loads(raw)
        if self.stage == "whole_title":
            answer = WholeTitleAnswer.model_validate(raw)
            return {"input_revision": self.internal["input_revision"], "title": answer.title}
        if self.stage == "scene_titles":
            answer = FinalTitlesAnswer.model_validate(raw)
            if [t.id for t in answer.titles] != list(self.references):
                raise ValueError("final titles changed the scene set or order")
            return {
                "input_revision": self.internal["input_revision"],
                "titles": [
                    {"scene_id": self.references[t.id], "title": t.text} for t in answer.titles
                ],
            }
        if self.stage == "title":
            if not isinstance(raw, dict) or set(raw) != {"titles"}:
                raise ValueError("invalid title envelope")
            entries = raw["titles"]
            if not isinstance(entries, (list, tuple)) or len(entries) > 12:
                raise ValueError("invalid title batch")
            # Keep partial-batch adoption: one invalid/duplicate sibling must not erase others.
            counts = {}
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    counts[entry["id"]] = counts.get(entry["id"], 0) + 1
            titles = []
            for entry in entries:
                try:
                    answer = TitleAnswer.model_validate(entry)
                except (ValueError, TypeError):
                    continue
                if counts[answer.id] != 1 or answer.id not in self.references:
                    continue
                card = self.references[answer.id]
                titles.append(
                    {
                        "card_id": card["card_id"],
                        "content_revision": card["content_revision"],
                        "text": answer.text,
                    }
                )
            return {"titles": titles}
        result = {k: self.internal[k] for k in ("card_id", "request_revision")}
        if self.stage == "action":
            if self.internal.get("movement"):
                answer = ActivityAnswer.model_validate(raw)
                refs = set(answer.evidence_ids)
                if len(refs) != len(answer.evidence_ids) or not refs <= self.references.keys():
                    raise ValueError("unknown or duplicate activity citation")
                action = self.internal.get("action")
                if action and "a1" not in refs:
                    raise ValueError("activity omitted recorded action")
                return {
                    **result,
                    "action_id": action["id"] if action else None,
                    "text": answer.text,
                    "movement_ids": sorted(
                        {
                            ref
                            for k in answer.evidence_ids
                            if k != "a1"
                            for ref in self.references[k]
                        }
                    ),
                }
            answer = ActionAnswer.model_validate(raw)
            return {**result, "action_id": self.internal["action"]["id"], "text": answer.text}
        answer = SpaceAnswer.model_validate(raw)
        if len(set(answer.evidence_ids)) != len(answer.evidence_ids):
            raise ValueError("duplicate material citation")
        if not set(answer.evidence_ids) <= self.references.keys():
            raise ValueError("unknown material citation")
        return {
            **result,
            "text": answer.text,
            "evidence_ids": [self.references[key] for key in answer.evidence_ids],
        }


def normalize(stage, request):
    """No raw dictionary unpacking into the wire payload: every field is deliberate."""
    references = {}
    if stage == "space":
        materials = []
        for index, item in enumerate(request["materials"], 1):
            projected = material(item)
            if projected is None:
                continue
            key = f"m{index}"
            references[key] = item["id"]
            materials.append({"id": key, **projected})
        payload = {"materials": materials}
    elif stage == "action":
        require_action(request)
        if request.get("movement"):
            payload, references = activity_projection(request)
        else:
            action = request["action"]
            payload = {"actor": action["actor"].get("name"), "action": action["material"]["무엇을"]}
    elif stage == "title":
        cards = []
        context = request.get("context", [])
        aliases = {c["card_id"]: f"c{i}" for i, c in enumerate(context, 1)}
        for index, card in enumerate(request["cards"], 1):
            key = aliases.get(card["card_id"], f"c{index}")
            references[key] = card
            value = {
                "id": key,
                "space": card["space"]["text"],
                "actions": [a["text"] for a in card["actions"]],
                "location": [
                    value for p in card["place_reference"] if (value := location(p["facts"]))
                ],
            }
            if card.get("observation"):
                value["observation"] = {
                    "text": card["observation"]["text"],
                    "subject": "기록 기기 동선 관측. 강아지 행동·정지 판정은 아님",
                }
            cards.append(value)
        payload = {"cards": cards}
        if context:
            # Bodies occur once, in the whole-board context. Targets only need aliases.
            payload["cards"] = [{"id": c["id"]} for c in cards]
            payload["context"] = [
                {
                    "id": aliases[c["card_id"]],
                    "order": c["order"],
                    "event_at": c["event_at"],
                    "body": c["body"],
                    "location": [value for p in c["location"] if (value := location(p["facts"]))],
                }
                for c in context
            ]
    elif stage in {"scene_titles", "whole_title"}:
        scenes = []
        for index, scene in enumerate(request["scenes"], 1):
            key = f"c{index}"
            references[key] = scene["id"]
            value = {
                "id": key,
                "order": scene["order"],
                "event_at": scene["event_at"],
                "body": scene["body"],
            }
            if scene.get("boundary") in {"start", "end"}:
                value["boundary"] = scene["boundary"]
            scenes.append(value)
        payload = {"scenes": scenes}
    else:
        raise ValueError("unsupported diary writer stage")
    return ModelRequest(stage, payload, request, references)
