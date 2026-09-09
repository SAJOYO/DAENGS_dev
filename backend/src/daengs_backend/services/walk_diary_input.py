"""Read existing storage into DiaryInput under one walk lock; no generation or lookup.

The transaction is owned by the caller, so the same reader can be used when reserving
and completing the existing storyboard generation. Public routes do not accept this
snapshot as proof of ownership.
"""

from dataclasses import dataclass
from uuid import UUID

from daengs_backend.config import settings
from daengs_backend.repositories import walk_entry as entries
from daengs_backend.repositories import walk_entry_context as contexts
from daengs_backend.repositories import walk_entry_v2 as pins
from daengs_backend.repositories import walk_photo as photos
from daengs_backend.repositories import walk_storyboard as storyboards
from daengs_backend.schemas.walk_entry_v2 import Pin
from daengs_backend.schemas.walk_photo import PhotoRecord
from daengs_walk.diary_input import (
    Anchor,
    Behavior,
    DiaryInput,
    Note,
    Photo,
    RecordRef,
    RouteVersion,
    SavedBackground,
    UserRecord,
    material_ref,
)


@dataclass(frozen=True)
class InputAssembly:
    source: DiaryInput
    # Do not log provider payload or user text; stable envelope IDs and reasons suffice.
    excluded_backgrounds: tuple[dict[str, str], ...]


def entry_anchor(event_at, location, raw_pin=None):
    if raw_pin is not None and raw_pin.get("policy_version") == "legacy-v1":
        # v2 content edits preserve this server-created v1 sidecar without source refs.
        # Its "observed" label does not turn the old sample into an observation at tap time.
        anchor = entry_anchor(event_at, location)
        if anchor.point is None:
            raise ValueError("legacy pin requires its original location")
        at = anchor.event_at.isoformat().replace("+00:00", "Z")
        expected = {
            "resolution_id": str(UUID(raw_pin["resolution_id"])),
            "state": "resolved",
            "method": "observed",
            "target_at": at,
            "point": anchor.point.model_dump(),
            "computed_at": at,
            "resolve_by": at,
            "policy_version": "legacy-v1",
            "algorithm_version": "legacy-v1",
            "source_refs": [],
            "uncertainty_m": None,
            "uncertainty_basis": "unknown",
            "reason": "direct_fix",
        }
        if raw_pin != expected:
            raise ValueError("legacy pin differs from its stored v1 origin")
        return anchor
    if raw_pin is not None:
        pin = Pin.model_validate(raw_pin)
        source_fixes = pin.model_dump(mode="json")["source_refs"]
        location_at = None
        if pin.method in {"observed", "last_known"}:
            location_at = max((r.at for r in pin.source_refs), default=None)
            if location_at is None and location is not None:
                location_at = location["captured_at"]
        anchor = Anchor(
            event_at=event_at,
            time_basis="recorded_at",
            point=pin.point.model_dump() if pin.point else None,
            location_at=location_at,
            position_state=pin.state,
            method=pin.method,
            source_fixes=source_fixes,
        )
        if anchor.event_at != pin.target_at:
            raise ValueError("pin target differs from the original event")
    else:
        anchor = Anchor(
            event_at=event_at,
            time_basis="recorded_at",
            point={"lat": location["lat"], "lng": location["lng"]} if location else None,
            location_at=location["captured_at"] if location else None,
            accuracy_m=location.get("accuracy_m") if location else None,
            position_state="legacy" if location else "unlocated",
            method="last_known" if location else "none",
        )
    return anchor


def entry_record(row, sidecar=None):
    payload = row.payload
    ref = RecordRef(
        store="walk_entry",
        id=str(row.id),
        version=str(row.revision),
        version_kind="revision",
        pin_revision=sidecar.pin_revision if sidecar else None,
    )
    if payload is None:
        return UserRecord(ref=ref, deleted=True, content=None, anchor=None)
    raw_pin = sidecar.payload if sidecar else None
    if (
        raw_pin
        and raw_pin.get("policy_version") == "legacy-v1"
        and (sidecar.pin_revision != 0 or raw_pin.get("resolution_id") != str(row.id))
    ):
        raise ValueError("legacy pin differs from its original record")
    anchor = entry_anchor(payload["recorded_at"], payload.get("location"), raw_pin)
    content = (
        Behavior(
            kind="behavior",
            code=payload["behavior_code"],
            pet_id=str(payload["pet_id"]) if payload.get("pet_id") else None,
        )
        if payload["kind"] == "behavior"
        else Note(kind="note", text=payload["note"])
    )
    return UserRecord(ref=ref, content=content, anchor=anchor, pin_payload=raw_pin)


def photo_record(raw):
    row = PhotoRecord.model_validate(raw)
    ref = RecordRef(
        store="walk_photo", id=str(row.id), version=str(row.revision), version_kind="revision"
    )
    if row.content is None:
        return UserRecord(ref=ref, deleted=True, content=None, anchor=None)
    photo = row.content
    return UserRecord(
        ref=ref,
        content=Photo(kind="photo", media_ref=f"app-private-photo:{row.id}"),
        anchor=Anchor(
            event_at=photo.captured_at,
            time_basis="photo_capture",
            point=photo.point,
            location_at=photo.location_captured_at,
            accuracy_m=photo.accuracy_m,
            position_state="legacy",
            method="last_known",
        ),
    )


def _context_pin(raw):
    if raw is None or raw.get("policy_version") == "legacy-v1":
        return raw  # entry_anchor checks the complete legacy shape against the original location.
    return Pin.model_validate(raw).model_dump(mode="json")


def saved_background(envelope, record, walk_id):
    """Reuse the saved source version, including the v2 pin's identity and location basis."""
    schema = envelope["schema_version"]
    expected_schema = (
        contexts.PIN_POLICY if record.ref.pin_revision is not None else contexts.POLICY
    )
    if schema != expected_schema:
        raise ValueError("unsupported_context_schema")
    target = envelope["target"]
    if (
        record.deleted
        or target["store"] != "walk_entry"
        or str(target["walk_id"]) != str(walk_id)
        or str(target["id"]) != record.ref.id
        or str(target["revision"]) != record.ref.version
    ):
        raise ValueError("stale_context_target")
    raw_pin = None
    provenance = envelope["provenance"]
    if schema == contexts.PIN_POLICY:
        raw_pin = target["pin"]
        if target["pin_revision"] != record.ref.pin_revision:
            raise ValueError("stale_context_pin_revision")
        pin = _context_pin(raw_pin)
        original = _context_pin(record.pin_payload)
        if pin != original or (pin and pin["state"] == "provisional"):
            raise ValueError("stale_context_pin")
        if provenance["location_basis"] != (pin["method"] if pin else "original_location"):
            raise ValueError("invalid_context_location_basis")
    target_anchor = entry_anchor(target["event_at"], target.get("location"), raw_pin)
    if target_anchor != record.anchor:
        raise ValueError("stale_context_location")
    if provenance["policy_version"] != schema:
        raise ValueError("invalid_context_policy")
    if not envelope["tags"] or not set(envelope["tags"]) <= {
        "space.facility",
        "space.park",
        "space.river",
        "environment.weather",
    }:
        raise ValueError("unsupported_context_tags")
    tags = tuple(
        sorted({"space" if t.startswith("space.") else "environment" for t in envelope["tags"]})
    )
    return SavedBackground(
        id=envelope["id"],
        target=material_ref(record),
        provider=provenance["provider"],
        payload_schema=envelope["schema_version"],
        policy_version=provenance["policy_version"],
        query_point=record.anchor.point,
        tags=tags,
        status=envelope["status"],
        reason=envelope.get("reason"),
        retrieved_at=provenance.get("retrieved_at"),
        temporal_basis=provenance["temporal_basis"],
        payload=envelope.get("payload"),
        payload_sha256=envelope.get("payload_sha256"),
    )


def assemble_input(walk, analysis, entry_rows, pin_rows, photo_manifest, envelopes):
    pin_map = {r.entry_id: r for r in pin_rows}
    records = [entry_record(row, pin_map.get(row.id)) for row in entry_rows]
    if photo_manifest is not None:
        records.extend(photo_record(r) for r in photo_manifest.records)
    by_id = {r.ref.id: r for r in records if r.ref.store == "walk_entry"}
    backgrounds, excluded = [], []
    for envelope in envelopes:
        try:
            record = by_id.get(str(envelope["target"]["id"]))
            if record is None:
                raise ValueError("missing_entry")
            backgrounds.append(saved_background(envelope, record, walk.id))
        except (ValueError, KeyError, TypeError):
            excluded.append(
                {"id": str(envelope.get("id", "unknown")), "reason": "invalid_or_stale_context"}
            )
    route = (
        RouteVersion(
            status="ready",
            analysis_id=str(analysis.id),
            input_fingerprint=analysis.input_fingerprint.removeprefix("sha256:"),
            calculation_version=analysis.calculation_version,
        )
        if analysis
        else RouteVersion(
            status="unavailable",
            analysis_id=None,
            input_fingerprint=None,
            calculation_version=None,
            reason="analysis_not_finalized",
        )
    )
    source = DiaryInput(
        owner_id=str(walk.app_user_id),
        walk_id=str(walk.id),
        client_session_id=str(walk.client_session_id),
        started_at=walk.started_at,
        ended_at=walk.ended_at,
        pet_ids=tuple(str(p) for p in walk.pet_ids),
        evidence_origin="unknown",
        route=route,
        records=tuple(records),
        photos_status="complete" if photo_manifest is not None else "not_available",
        photo_manifest={
            "publisher_id": str(photo_manifest.publisher_id),
            "revision": photo_manifest.revision,
        }
        if photo_manifest is not None
        else None,
        backgrounds=tuple(backgrounds),
        # Current, validated envelopes are eligible inputs. The stamp selector separately
        # projects them and decides which pieces fit each scene's background slots.
        selected_background_ids=tuple(
            b.id for b in backgrounds if b.status in {"known", "partial"}
        ),
        scene_policy_version="records-first-v1",
        writing_policy_version="diary-background-v1",
    )
    source.revision()
    return InputAssembly(source, tuple(excluded))


async def read_input(session, owner, walk_id):
    if session.new or session.dirty or session.deleted:
        raise ValueError("flush source changes before preparing a diary snapshot")
    # AsyncSession is reused across the existing generation's commits; expire_on_commit=False.
    # A second read must not reuse its pre-LLM identity map after another request edits a pin.
    session.expire_all()
    walk = await entries.owned_walk(session, owner, walk_id, lock=True)
    if walk is None:
        raise LookupError("walk not found")
    rows = await entries.entries(session, [walk_id])
    pin_rows = await pins.pins(session, [walk_id]) if settings.walk_entry_v2_enabled else []
    photo_manifest = (
        await photos.current(session, walk_id) if settings.walk_photo_metadata_enabled else None
    )
    envelopes = []
    if settings.walk_entry_context_enabled:
        pin_ids = {r.entry_id for r in pin_rows}
        for row in rows:
            if row.payload is None:
                continue
            policy = contexts.PIN_POLICY if row.id in pin_ids else contexts.POLICY
            _, latest = await contexts.current(session, row, policy=policy)
            envelopes.extend(r.envelope for r in latest.values())
    return assemble_input(
        walk,
        await storyboards.latest_analysis(session, walk_id),
        rows,
        pin_rows,
        photo_manifest,
        envelopes,
    )
