"""Explicit local artifact adapter for the skeleton, not the production database."""

import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile

from daengs_walk.diary.relational.contracts import VERSION
from daengs_walk.diary.relational.publication import PUBLICATION_VERSION, validate_publication
from daengs_walk.value_contracts import digest


def save_skeleton(path, result):
    body = deepcopy(result)
    version = body["receipt"]["version"]
    if version not in {VERSION, PUBLICATION_VERSION}:
        raise ValueError("unsupported receipt")
    if version == PUBLICATION_VERSION:
        from daengs_backend.services.walk_diary.writing.relational import validate_prepared
        from daengs_walk.diary.relational.assembly import assemble_receipt

        validate_prepared(body["prepared"])
        expected = assemble_receipt(body["prepared"], body["receipt"]["writing"])
        if any(body["receipt"].get(k) != v for k, v in expected.items()):
            raise ValueError("receipt differs from frozen preparation and accepted writing")
    document = {"format": version, "payload": body, "digest": digest(body)}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory staging keeps publication on the same filesystem. Linking
    # the complete inode is atomic and refuses an existing target (unlike replace).
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_skeleton(path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        document["format"]
        not in {
            "relational-diary-skeleton-v2",
            "relational-diary-skeleton-v3",
            "relational-diary-skeleton-v4",
            "relational-diary-skeleton-v5",
            VERSION,
            PUBLICATION_VERSION,
        }
        or digest(document["payload"]) != document["digest"]
    ):
        raise ValueError("stored skeleton changed")
    # Reader uses only the saved receipt, never current facts, plans or LLM.
    receipt = document["payload"]["receipt"]
    if document["format"] != receipt["version"]:
        raise ValueError("stored format differs from receipt version")
    if receipt["version"] == PUBLICATION_VERSION:
        validate_publication(receipt)
    return receipt
