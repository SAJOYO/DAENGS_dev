"""CAS snapshots from the photo publisher; deletion tombstones cannot be resurrected."""

from datetime import UTC, datetime

from daengs_backend.config import settings
from daengs_backend.models.walk_photo import WalkPhotoManifest
from daengs_backend.repositories import walk_entry as walks
from daengs_backend.repositories import walk_photo as repo
from daengs_backend.schemas.walk_photo import PhotoManifestResponse, PhotoManifestWrite, PhotoRecord
from daengs_walk.diary_input import digest


class PhotoNotFound(LookupError):
    pass


class PhotoConflict(ValueError):
    pass


def capabilities():
    return {
        "write_versions": ["walk-photo-metadata-v1"]
        if settings.walk_photo_metadata_enabled
        else [],
        "max_records": 200,
    }


async def owned(session, owner, walk_id):
    if not settings.walk_photo_metadata_enabled:
        raise PhotoNotFound
    walk = await walks.owned_walk(session, owner, walk_id, lock=True)
    if walk is None:
        raise PhotoNotFound
    return walk


def response(walk, row):
    return PhotoManifestResponse(
        client_session_id=walk.client_session_id,
        status="complete" if row is not None else "not_available",
        publisher_id=row.publisher_id if row else None,
        revision=row.revision if row else 0,
        records=tuple(PhotoRecord.model_validate(r) for r in row.records) if row else (),
    )


def transition(row, request: PhotoManifestWrite, walk) -> tuple[list[dict], str]:
    raw = request.model_dump(mode="json")
    raw["photos"].sort(key=lambda p: p["id"])
    request_hash = digest(raw)
    if row is not None:
        if row.publisher_id != request.publisher_id:
            raise PhotoConflict("이 사진 목록은 다른 촬영 기기에서 관리하고 있어요.")
        # A lost ACK retries the exact durable request, including the original expected revision.
        if row.revision == request.revision and row.request_hash == request_hash:
            return row.records, request_hash
        if row.revision != request.expected_revision or request.revision <= row.revision:
            raise PhotoConflict("사진 목록 버전이 변경됐어요.")
    elif request.expected_revision != 0:
        raise PhotoConflict("사진 목록을 처음부터 확인해 주세요.")
    previous = {r["id"]: PhotoRecord.model_validate(r) for r in row.records} if row else {}
    incoming = {str(p.id): p for p in request.photos}
    if len(previous.keys() | incoming.keys()) > 200:
        raise PhotoConflict("한 산책은 삭제 기록을 포함해 사진 200개까지 지원합니다.")
    records = []
    for id in sorted(previous.keys() | incoming.keys()):
        old, photo = previous.get(id), incoming.get(id)
        if photo is not None and not walk.started_at <= photo.captured_at <= walk.ended_at:
            raise PhotoConflict("촬영 시각이 산책 범위 밖에 있어요.")
        if old and old.content is None and photo is not None:
            raise PhotoConflict("삭제된 사진 ID를 다시 사용할 수 없어요.")
        revision = old.revision + (old.content != photo) if old else 1
        records.append(PhotoRecord(id=id, revision=revision, content=photo).model_dump(mode="json"))
    return records, request_hash


async def get(session, owner, walk_id):
    walk = await owned(session, owner, walk_id)
    result = response(walk, await repo.current(session, walk_id))
    await session.commit()
    return result


async def put(session, owner, walk_id, request):
    walk = await owned(session, owner, walk_id)
    row = await repo.current(session, walk_id)
    records, request_hash = transition(row, request, walk)
    if row is None:
        row = WalkPhotoManifest(walk_id=walk_id, publisher_id=request.publisher_id)
        session.add(row)
    row.revision, row.request_hash, row.records = request.revision, request_hash, records
    row.updated_at = datetime.now(UTC)
    result = response(walk, row)
    await session.commit()
    return result
