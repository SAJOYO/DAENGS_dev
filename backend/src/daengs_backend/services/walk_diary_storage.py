"""Private JSONB receipt for reading a completed diary after background enrichment.

No original text/GPS snapshot is duplicated. Only backgrounds are excluded from the
source fingerprint; records, pins, photos, route and selection/writing policies remain
binding. This receipt is never returned as an App bundle.
"""

from typing import Literal

from pydantic import model_validator

from daengs_backend.services.walk_diary_writing import writing_version
from daengs_walk.diary_input import DiaryContract, Digest, digest
from daengs_walk.diary_output import DiaryBundle


class StoredDiary(DiaryContract):
    format: Literal["walk-diary-storage-v1"] = "walk-diary-storage-v1"
    generation_revision: Digest
    source_revision: Digest
    policy_revision: Digest
    bundle_sha256: Digest
    bundle: DiaryBundle
    preparation_counts: dict[str, int]
    preparation_limits: tuple[str, ...]

    @model_validator(mode="after")
    def intact_bundle(self):
        if digest(self.bundle) != self.bundle_sha256:
            raise ValueError("stored diary content changed without its receipt")
        return self


def source_revision(prepared):
    source = prepared.input.source
    return source.model_copy(update={"backgrounds": (), "selected_background_ids": ()}).revision()


def policy_revision(prepared):
    return digest(
        {
            "preparation": prepared.prepared.policy.model_dump(mode="json"),
            "writer": writing_version(),
        }
    )


def store_diary(prepared, bundle, revision):
    source, plan = prepared.input.source, prepared.prepared.plan
    bundle = DiaryBundle.model_validate(bundle)
    if (
        bundle.input_revision != source.revision()
        or bundle.plan_revision != plan.revision()
        or bundle.client_session_id != source.client_session_id
        or bundle.photos_status != source.photos_status
    ):
        raise ValueError("stored diary binding mismatch")
    return StoredDiary(
        generation_revision=revision,
        source_revision=source_revision(prepared),
        policy_revision=policy_revision(prepared),
        bundle_sha256=digest(bundle),
        bundle=bundle,
        preparation_counts=prepared.prepared.counts,
        preparation_limits=prepared.prepared.limits,
    ).model_dump(mode="json")


def read_diary(prepared, row, revision):
    """Return a compatible receipt, None for stale source, or raise for corrupt data.

    Bare v1 bundles predate the receipt. Upgrade only while their entire input and
    plan still match, under the caller's Walk lock. A previously stale bare bundle
    cannot prove that only background changed and is never grandfathered in.
    """
    raw = row.bundle
    if isinstance(raw, dict) and raw.get("format") == "walk-diary-storage-v1":
        stored = StoredDiary.model_validate(raw)
        if (
            stored.generation_revision != row.input_revision
            or stored.bundle.client_session_id != prepared.input.source.client_session_id
        ):
            raise ValueError("stored diary receipt belongs to another generation/session")
        if stored.source_revision != source_revision(
            prepared
        ) or stored.policy_revision != policy_revision(prepared):
            return None
        return stored
    if row.input_revision != revision:
        return None
    upgraded = store_diary(prepared, raw, revision)
    row.bundle = upgraded
    return StoredDiary.model_validate(upgraded)
