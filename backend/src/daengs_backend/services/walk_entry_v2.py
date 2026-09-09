"""v2 transactions: owner lock, deletion first, lifetime idempotency, then CAS and validation."""

import hashlib
import json

from daengs_backend.config import settings
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_v2 import WalkEntryMutation, WalkEntryPin
from daengs_backend.repositories import walk_entry as entries
from daengs_backend.repositories import walk_entry_v2 as repo
from daengs_backend.schemas.walk_entry_v2 import ContentV2, EntryResponseV2, Tombstone
from daengs_backend.services.walk_entry import (
    EntryConflict,
    EntryInvalid,
    EntryNotFound,
    build_profile,
)
from daengs_backend.services.walk_entry_pin import (
    POLICY,
    validate_new_pin,
    validate_sources,
    validate_transition,
)


class EntryDeleted(Exception):
    pass


class EntryWritesDisabled(Exception):
    pass


class EntryUpgradeRequired(Exception):
    pass


def require_enabled():
    if not settings.walk_entry_v2_enabled:
        raise EntryNotFound


def capabilities():
    enabled = settings.walk_entry_v2_enabled
    writing = enabled and settings.walk_entry_v2_write_enabled
    return {
        "read_versions": ["walk-entry-v1"] + (["walk-entry-v2"] if enabled else []),
        "write_versions": ["walk-entry-v1"] + (["walk-entry-v2"] if writing else []),
        "active_policy_versions": [POLICY] if writing else [],
    }


async def guard_v1(session, walk_ids, *, entry_id=None):
    if settings.walk_entry_v2_enabled and await repo.contains_v2(
        session, walk_ids, entry_id=entry_id
    ):
        raise EntryUpgradeRequired


def legacy_pin(row):
    content = ContentV2.model_validate(row.payload)
    if content.kind == "note":
        return None
    location = content.location
    return {
        "resolution_id": str(row.id),
        "state": "resolved",
        "method": "observed",
        "target_at": content.recorded_at.isoformat().replace("+00:00", "Z"),
        "point": {"lat": location.lat, "lng": location.lng},
        "computed_at": content.recorded_at.isoformat().replace("+00:00", "Z"),
        "resolve_by": content.recorded_at.isoformat().replace("+00:00", "Z"),
        "policy_version": "legacy-v1",
        "algorithm_version": "legacy-v1",
        "source_refs": [],
        "uncertainty_m": None,
        "uncertainty_basis": "unknown",
        "reason": "direct_fix",
    }


def response(row, sidecar):
    if row.payload is None:
        return Tombstone(id=row.id, revision=row.revision, mutation_id=row.mutation_id).model_dump(
            mode="json"
        )
    return EntryResponseV2(
        id=row.id,
        revision=row.revision,
        pin_revision=sidecar.pin_revision if sidecar else 0,
        mutation_id=row.mutation_id,
        content=ContentV2.model_validate(row.payload),
        pin=sidecar.payload if sidecar else legacy_pin(row),
    ).model_dump(mode="json")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


async def list_entries(session, owner, walk_id):
    require_enabled()
    if await entries.owned_walk(session, owner, walk_id) is None:
        raise EntryNotFound
    rows = await entries.entries(session, [walk_id])
    sidecars = {(p.walk_id, p.entry_id): p for p in await repo.pins(session, [walk_id])}
    values = [response(row, sidecars.get((walk_id, row.id))) for row in rows]
    return {"contract_version": "walk-entry-v2", "revision": digest(values), "entries": values}


async def locked(session, owner, walk_id, entry_id):
    require_enabled()
    walk = await entries.owned_walk(session, owner, walk_id, lock=True)
    if walk is None:
        raise EntryNotFound
    row = await entries.get_entry(session, walk_id, entry_id)
    return walk, row


async def replay(session, row, mutation, request_hash):
    if row is None:
        return None
    if row.payload is None:
        raise EntryDeleted
    saved = await repo.receipt(session, row.walk_id, row.id, mutation)
    if saved is not None:
        if saved.request_hash != request_hash:
            raise EntryConflict("같은 요청 ID의 연산 또는 내용이 달라졌습니다.")
        return saved.response
    # A v1 mutation has no v2 receipt: it cannot be silently reused by v2.
    if row.mutation_id == mutation:
        raise EntryConflict("기존 요청 ID를 다른 연산에 재사용할 수 없습니다.")
    return None


def compare_revision(row, expected):
    if expected != (row.revision if row else 0):
        raise EntryConflict("기록이 변경되었습니다. 다시 읽어 주세요.")


async def store(session, row, sidecar, request_hash):
    session.add(row)
    # Parent before sidecar/receipt; still the same transaction and walk lock.
    await session.flush()
    session.add(sidecar)
    value = response(row, sidecar)
    session.add(
        WalkEntryMutation(
            walk_id=row.walk_id,
            entry_id=row.id,
            mutation_id=row.mutation_id,
            request_hash=request_hash,
            response=value,
        )
    )
    await session.commit()
    return value


async def write(session, owner, walk_id, entry_id, body):
    walk, row = await locked(session, owner, walk_id, entry_id)
    request_hash = digest(
        {
            "operation": "content",
            "pin_supplied": "pin" in body.model_fields_set,
            **body.model_dump(mode="json"),
        }
    )
    replayed = await replay(session, row, body.mutation_id, request_hash)
    if replayed is not None:
        return replayed
    compare_revision(row, body.expected_revision)
    content = body.content
    if not walk.started_at <= content.recorded_at <= walk.ended_at:
        raise EntryInvalid("기록 시각이 산책 범위 밖입니다.")
    if content.pet_id is not None and (
        content.pet_id not in walk.pet_ids
        or not await entries.owns_pet(session, owner, content.pet_id)
    ):
        raise EntryInvalid("동행한 내 강아지만 선택할 수 있습니다.")
    if row is None:
        if not settings.walk_entry_v2_write_enabled:
            raise EntryWritesDisabled
        if "pin" not in body.model_fields_set:
            raise EntryInvalid("생성 시 pin 필드는 필수입니다.")
        validate_new_pin(content, body.pin)
        await validate_sources(session, walk_id, content, body.pin)
        row = WalkEntry(walk_id=walk_id, id=entry_id, revision=0)
        sidecar = WalkEntryPin(
            walk_id=walk_id,
            entry_id=entry_id,
            pin_revision=1 if body.pin else 0,
            payload=body.pin.model_dump(mode="json") if body.pin else None,
        )
    else:
        if "pin" in body.model_fields_set:
            raise EntryInvalid("내용 정정에서 핀을 변경할 수 없습니다.")
        previous = ContentV2.model_validate(row.payload)
        if any(
            getattr(previous, name) != getattr(content, name)
            for name in ("kind", "recorded_at", "location")
        ):
            raise EntryInvalid("종류·기록 시각·원본 위치는 변경할 수 없습니다.")
        sidecar = await repo.pin(session, walk_id, entry_id)
        if sidecar is None:
            sidecar = WalkEntryPin(
                walk_id=walk_id, entry_id=entry_id, pin_revision=0, payload=legacy_pin(row)
            )
    row.payload = content.model_dump(mode="json")
    row.revision += 1
    row.mutation_id = body.mutation_id
    return await store(session, row, sidecar, request_hash)


async def finalize_pin(session, owner, walk_id, entry_id, body):
    _, row = await locked(session, owner, walk_id, entry_id)
    if row is None:
        raise EntryNotFound
    request_hash = digest({"operation": "pin", **body.model_dump(mode="json")})
    replayed = await replay(session, row, body.mutation_id, request_hash)
    if replayed is not None:
        return replayed
    compare_revision(row, body.expected_revision)
    sidecar = await repo.pin(session, walk_id, entry_id)
    if sidecar is None:
        raise EntryConflict("이전 기록의 위치는 자동 변경할 수 없습니다.")
    validate_transition(sidecar.payload, body.pin, body.expected_pin_revision, sidecar.pin_revision)
    await validate_sources(session, walk_id, ContentV2.model_validate(row.payload), body.pin)
    sidecar.payload = body.pin.model_dump(mode="json")
    sidecar.pin_revision += 1
    row.revision += 1
    row.mutation_id = body.mutation_id
    return await store(session, row, sidecar, request_hash)


async def remove(session, owner, walk_id, entry_id, expected, mutation_id):
    _, row = await locked(session, owner, walk_id, entry_id)
    if row is not None and row.payload is None:
        return response(row, None)
    if row is not None and (
        row.mutation_id == mutation_id
        or await repo.receipt(session, walk_id, entry_id, mutation_id) is not None
    ):
        raise EntryConflict("기존 요청 ID를 삭제에 재사용할 수 없습니다.")
    compare_revision(row, expected)
    if row is None:
        row = WalkEntry(walk_id=walk_id, id=entry_id, revision=0)
    row.payload = None
    row.revision += 1
    row.mutation_id = mutation_id
    session.add(row)
    await (
        session.flush()
    )  # DB trigger purges pin/receipts, even if other code paths delete content.
    if await repo.pin(session, walk_id, entry_id) is None:
        session.add(WalkEntryPin(walk_id=walk_id, entry_id=entry_id, pin_revision=0, payload=None))
    await session.commit()
    return response(row, None)


async def profile(session, owner, spec):
    require_enabled()
    if not await entries.owns_pet(session, owner, spec.pet_id):
        raise EntryNotFound
    walks = await entries.profile_walks(session, owner, spec)
    walk_ids = [w.id for w in walks]
    rows = await entries.entries(session, walk_ids)
    sidecars = {(p.walk_id, p.entry_id): p for p in await repo.pins(session, walk_ids)}
    result = build_profile(spec, walks, rows, content_type=ContentV2)
    by_id = {
        (str(row.walk_id), str(row.id)): response(row, sidecars.get((row.walk_id, row.id)))
        for row in rows
    }
    for evidence in result["evidence"]:
        value = by_id[(evidence["walk_id"], evidence["entry_id"])]
        evidence.update(pin=value["pin"], pin_revision=value["pin_revision"])
    return {"contract_version": "walk-entry-v2", **result}
