"""Raw-reference verification; SQL storage quantizes coordinates to six decimal places."""

from decimal import Decimal

from daengs_backend.repositories import walk_entry_v2 as repo
from daengs_backend.schemas.walk_entry_v2 import ContentV2, Pin
from daengs_backend.services.walk_chunk import decode_chunk
from daengs_backend.services.walk_entry import EntryInvalid

POLICY = "action-pin-policy-v1"
ALGORITHM = "action-pin-local-v1"


def same_point(point, raw):
    quantum = Decimal("0.000001")
    return all(
        Decimal(str(getattr(point, field))).quantize(quantum)
        == Decimal(str(getattr(raw, field))).quantize(quantum)
        for field in ("lat", "lng")
    )


async def validate_sources(session, walk_id, content: ContentV2, pin: Pin | None):
    if pin is None and content.location is None:
        return
    points = [
        point
        for chunk in await repo.raw_chunks(session, walk_id)
        for point in decode_chunk(chunk.payload)
    ]
    by_seq = {point.client_seq: point for point in points}
    if len(by_seq) != len(points):
        raise EntryInvalid("원본 GPS 순서가 중복되었습니다.")
    if content.location is not None:
        location = content.location
        if not any(
            p.at == location.captured_at
            and same_point(location, p)
            and p.accuracy_m == location.accuracy_m
            for p in points
        ):
            raise EntryInvalid("원본 위치가 업로드한 GPS와 일치하지 않습니다.")
    if pin is None:
        return
    if pin.state == "unlocated" and any(
        not p.is_mock
        and p.at
        <= min(pin.computed_at, pin.resolve_by, pin.observation_cutoff_at or pin.computed_at)
        for p in points
    ):
        raise EntryInvalid("사용 가능한 원본 좌표가 있으면 근거 없음으로 종료할 수 없습니다.")
    if pin.target_at != content.recorded_at:
        raise EntryInvalid("핀 대상 시각은 행동 시각과 같아야 합니다.")
    sources = []
    for ref in pin.source_refs:
        raw = by_seq.get(ref.client_seq)
        if raw is None or raw.chain_index != ref.chain_index or raw.at != ref.at or raw.is_mock:
            raise EntryInvalid("핀 근거가 같은 산책의 원본 GPS와 일치하지 않습니다.")
        sources.append(raw)
    if pin.method in {"last_known", "observed"} and not same_point(pin.point, sources[0]):
        raise EntryInvalid("복사한 위치가 원본 GPS 좌표와 다릅니다.")
    if pin.method == "observed":
        if (
            content.location is None
            or pin.point.model_dump() != {"lat": content.location.lat, "lng": content.location.lng}
            or content.location.captured_at != pin.source_refs[0].at
        ):
            raise EntryInvalid("관측 핀은 원본 위치와 시각을 유지해야 합니다.")
        if (
            pin.uncertainty_basis == "provider_accuracy"
            and pin.uncertainty_m != sources[0].accuracy_m
        ):
            raise EntryInvalid("provider accuracy가 원본과 다릅니다.")


def validate_new_pin(content, pin):
    if content.kind == "note":
        if pin is not None:
            raise EntryInvalid("메모는 행동 핀을 만들지 않습니다.")
        return
    if pin is None:
        raise EntryInvalid("행동에는 위치 상태가 필요합니다.")
    if (pin.policy_version, pin.algorithm_version) != (POLICY, ALGORITHM):
        raise EntryInvalid("지원하지 않는 핀 정책/알고리즘입니다.")


def validate_transition(previous, incoming: Pin, expected_pin_revision, actual_pin_revision):
    from daengs_backend.services.walk_entry import EntryConflict

    if (
        expected_pin_revision != actual_pin_revision
        or previous is None
        or previous["state"] != "provisional"
    ):
        raise EntryConflict("이미 확정되었거나 핀 revision이 달라졌습니다.")
    old = Pin.model_validate(previous)
    for field in (
        "resolution_id",
        "target_at",
        "resolve_by",
        "policy_version",
        "algorithm_version",
    ):
        if getattr(old, field) != getattr(incoming, field):
            raise EntryInvalid("핀의 식별자·시각·기한·버전은 변경할 수 없습니다.")
    if incoming.state == "provisional" or incoming.computed_at < old.computed_at:
        raise EntryInvalid("핀은 한 번만 종료할 수 있습니다.")
    if old.point is not None and incoming.point is None:
        raise EntryInvalid("이미 보존한 좌표를 근거 없음으로 버릴 수 없습니다.")
