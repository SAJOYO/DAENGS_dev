"""현재 기록 CRUD와 해석 정책 없는 산책 기록 프로필."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.repositories import walk_entry as repo
from daengs_backend.schemas.walk_entry import (
    EntryContent,
    EntryResponse,
    EntryWrite,
    RecordProfileQuery,
)
from daengs_backend.services.walk_entry_context import reserve


class EntryNotFound(Exception):
    pass


class EntryConflict(Exception):
    pass


class EntryInvalid(Exception):
    pass


def response(row: WalkEntry) -> EntryResponse:
    return EntryResponse(
        id=row.id,
        revision=row.revision,
        mutation_id=row.mutation_id,
        content=EntryContent.model_validate(row.payload) if row.payload else None,
    )


async def list_entries(session, owner, walk_id):
    if await repo.owned_walk(session, owner, walk_id) is None:
        raise EntryNotFound
    from daengs_backend.services.walk_entry_v2 import guard_v1

    await guard_v1(session, [walk_id])
    return [response(row) for row in await repo.entries(session, [walk_id])]


def apply_change(row, walk_id, entry_id, expected, mutation_id, payload):
    """산책 행 잠금 안에서 호출. 삭제 표식은 오래된 생성 요청도 막는다."""
    if row is not None and row.mutation_id == mutation_id:
        if row.payload != payload:
            raise EntryConflict("같은 요청 ID의 내용이 달라졌습니다.")
        return row
    if row is not None and row.payload is None:
        if payload is None:
            return row
        raise EntryConflict("삭제한 기록은 되살릴 수 없습니다.")
    if expected != (row.revision if row else 0):
        raise EntryConflict("다른 기기에서 변경된 기록입니다. 다시 읽어 주세요.")
    if row is None:
        row = WalkEntry(walk_id=walk_id, id=entry_id, revision=0)
    row.revision += 1
    row.mutation_id = mutation_id
    row.payload = payload
    return row


async def write(session: AsyncSession, owner, walk_id, entry_id, body: EntryWrite):
    walk = await repo.owned_walk(session, owner, walk_id, lock=True)
    if walk is None:
        raise EntryNotFound
    from daengs_backend.services.walk_entry_v2 import guard_v1

    await guard_v1(session, [walk_id], entry_id=entry_id)
    content = body.content
    if not walk.started_at <= content.recorded_at <= walk.ended_at:
        raise EntryInvalid("기록 시각이 산책 범위 밖입니다.")
    if content.pet_id is not None and (
        content.pet_id not in walk.pet_ids
        or not await repo.owns_pet(session, owner, content.pet_id)
    ):
        raise EntryInvalid("그 산책에 동행한 내 강아지만 선택할 수 있습니다.")
    previous = await repo.get_entry(session, walk_id, entry_id)
    if previous and previous.payload:
        for field in ("kind", "recorded_at", "location"):
            if previous.payload.get(field) != content.model_dump(mode="json").get(field):
                raise EntryInvalid("기록 종류와 관측 시각·위치는 변경할 수 없습니다.")
    row = apply_change(
        previous,
        walk_id,
        entry_id,
        body.expected_revision,
        body.mutation_id,
        content.model_dump(mode="json"),
    )
    session.add(row)
    await reserve(session, row)
    await session.commit()
    return response(row)


async def remove(session, owner, walk_id, entry_id, expected, mutation_id):
    if await repo.owned_walk(session, owner, walk_id, lock=True) is None:
        raise EntryNotFound
    from daengs_backend.services.walk_entry_v2 import guard_v1

    await guard_v1(session, [walk_id], entry_id=entry_id)
    row = apply_change(
        await repo.get_entry(session, walk_id, entry_id),
        walk_id,
        entry_id,
        expected,
        mutation_id,
        None,
    )
    session.add(row)
    await session.commit()
    return response(row)


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


async def profile(session, owner, spec: RecordProfileQuery):
    if not await repo.owns_pet(session, owner, spec.pet_id):
        raise EntryNotFound
    walks = await repo.profile_walks(session, owner, spec)
    from daengs_backend.services.walk_entry_v2 import guard_v1

    await guard_v1(session, [w.id for w in walks])
    rows = await repo.entries(session, [w.id for w in walks])
    return build_profile(spec, walks, rows)
