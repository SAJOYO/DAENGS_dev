"""Explicit local artifact adapter for the skeleton, not the production database."""

import json
from copy import deepcopy
from pathlib import Path

from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.relational.contracts import VERSION


def save_skeleton(path, result):
    body = deepcopy(result)
    if body["receipt"]["version"] != VERSION:
        raise ValueError("unsupported receipt")
    document = {"format": VERSION, "payload": body, "digest": digest(body)}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Explicit artifact write, no implicit overwrite of an earlier run.
    with target.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)


def read_skeleton(path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document["format"] != VERSION or digest(document["payload"]) != document["digest"]:
        raise ValueError("stored skeleton changed")
    # Reader uses only the saved receipt, never current facts, plans or LLM.
    return document["payload"]["receipt"]
