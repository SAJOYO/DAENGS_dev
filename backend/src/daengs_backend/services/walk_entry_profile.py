"""Record counts and evidence projection shared by v1 and v2 content contracts."""

import hashlib
import json
from datetime import UTC, datetime

from daengs_backend.schemas.walk_entry import EntryContent


def build_profile(spec, walks, rows, *, content_type=EntryContent):
    behaviors = {
        code: {"entry_count": 0, "walks_with_entries": 0}
        for code in ("sniffing", "excretion", "barking")
    }
    distinct = {code: set() for code in behaviors}
    evidence = []
    unassigned = 0
    for row in rows:
        if not row.payload:
            continue
        content = content_type.model_validate(row.payload)
        if content.kind != "behavior":
            continue
        if content.pet_id is None:
            unassigned += 1
            continue
        if content.pet_id != spec.pet_id:
            continue
        code = content.behavior_code
        behaviors[code]["entry_count"] += 1
        distinct[code].add(row.walk_id)
        evidence.append(
            {
                "entry_id": str(row.id),
                "entry_revision": row.revision,
                "walk_id": str(row.walk_id),
                **content.model_dump(mode="json"),
                "context_status": "not_requested",
                "context_refs": [],
            }
        )
    for code, values in behaviors.items():
        values["walks_with_entries"] = len(distinct[code])
    revision = hashlib.sha256(
        json.dumps(
            {
                "spec": spec.model_dump(mode="json"),
                "walks": sorted(str(w.id) for w in walks),
                "entries": sorted((str(r.walk_id), str(r.id), r.revision) for r in rows),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "profile_version": "walk-record-profile-v0",
        "vocabulary_version": "walk-behavior-v1",
        "pet_id": str(spec.pet_id),
        "period": {"since": spec.since, "until": spec.until},
        "generated_at": datetime.now(UTC),
        "source_revision": revision,
        "walk_count": len(walks),
        "unassigned_entry_count": unassigned,
        "behaviors": behaviors,
        "evidence": evidence,
    }
