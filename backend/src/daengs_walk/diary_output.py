"""Contract checks for prepared stamps and split narration, without a selector/LLM.

The domain prepares facts; a writer returns only text and citations. Source actions
and map anchors are copied by assembly, never accepted from the writer response.
"""

from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, JsonValue, field_validator, model_validator

from daengs_walk.diary_input import (
    Anchor,
    DiaryContract,
    DiaryInput,
    Digest,
    Identifier,
    MaterialRef,
    MovementObservation,
    RecordContent,
    digest,
    material_ref,
)


class BackgroundPiece(DiaryContract):
    id: Identifier
    background_id: Identifier
    kind: Literal["place_reference", "space_relation", "environment", "time"]
    # Domain-owned dictionary projection, not a model-authored interpretation.
    schema_version: Identifier
    facts: dict[str, JsonValue]


class SceneStamp(DiaryContract):
    id: Identifier
    core: MaterialRef
    background: tuple[BackgroundPiece, ...] = Field(default=(), max_length=16)


class DiaryPlan(DiaryContract):
    format: Literal["walk-diary-plan-v1"] = "walk-diary-plan-v1"
    input_revision: Digest
    target_scene_count: int = Field(ge=1, le=50)
    # The domain records all slot/separation parameters, including unused ones, for replay.
    preparation_policy: dict[str, JsonValue] | None = None
    scenes: tuple[SceneStamp, ...] = Field(max_length=600)

    def revision(self) -> str:
        return digest(DiaryPlan.model_validate(self.model_dump(mode="json")))

    def validate_against(self, source: DiaryInput) -> None:
        if self.input_revision != source.revision():
            raise ValueError("plan targets a different input revision")
        if source.photos_status == "pending":
            raise ValueError("photo snapshot is still changing")
        records = {material_ref(r).identity: r for r in source.records if not r.deleted}
        observations = {material_ref(o).identity: o for o in source.observations}
        materials = records | observations
        identities = [s.core.identity for s in self.scenes]
        if len(set(identities)) != len(identities) or len({s.id for s in self.scenes}) != len(
            self.scenes
        ):
            raise ValueError("duplicate scene/core")
        if not records.keys() <= set(identities):
            raise ValueError("live user records cannot be displaced by observations")
        supplements = set(identities) - records.keys()
        if len(supplements) > max(0, self.target_scene_count - len(records)):
            raise ValueError("observations may only supplement a scene deficit")
        backgrounds = {b.id: b for b in source.backgrounds}
        chronology = []
        for scene in self.scenes:
            core = materials.get(scene.core.identity)
            if core is None or material_ref(core) != scene.core:
                raise ValueError("unknown/deleted/stale scene core")
            chronology.append((core.anchor.event_at, scene.core.identity))
            if len({p.id for p in scene.background}) != len(scene.background):
                raise ValueError("duplicate scene evidence")
            for piece in scene.background:
                saved = backgrounds.get(piece.background_id)
                tag = {
                    "place_reference": "space",
                    "space_relation": "space",
                    "environment": "environment",
                    "time": "time",
                }[piece.kind]
                if (
                    saved is None
                    or saved.id not in source.selected_background_ids
                    or saved.target != scene.core
                    or saved.status not in {"known", "partial"}
                    or tag not in saved.tags
                    or not piece.facts
                ):
                    raise ValueError("piece requires selected background for this exact core")
        if chronology != sorted(chronology):
            raise ValueError("scene order is event time, then source identity")


class SceneText(DiaryContract):
    scene_id: Identifier
    text: str | None = Field(min_length=1, max_length=180)
    evidence_ids: tuple[Identifier, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def grounded_or_omitted(self):
        if self.text is not None and not self.text.strip():
            raise ValueError("use null to omit prose")
        if (self.text is None) != (not self.evidence_ids):
            raise ValueError("prose requires evidence; omitted prose has no citations")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("duplicate citation")
        return self


class DiaryWriting(DiaryContract):
    """Model response body. The service binds the plan revision outside this body."""

    title: str = Field(min_length=1, max_length=80)
    scenes: tuple[SceneText, ...] = Field(max_length=600)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank title")
        return value


class WritingReceipt(DiaryContract):
    """Server-owned binding; never trust a plan hash echoed by the provider."""

    plan_revision: Digest
    writing: DiaryWriting


class SceneNarration(DiaryContract):
    status: Literal["generated", "omitted", "no_background", "not_requested", "unavailable"]
    text: str | None
    evidence_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "generated":
            SceneText(scene_id="check", text=self.text, evidence_ids=self.evidence_ids)
            if self.text is None:
                raise ValueError("generated narration requires text")
        elif self.text is not None or self.evidence_ids:
            raise ValueError("absent narration carries no text or citations")
        return self


class DiaryScene(DiaryContract):
    id: Identifier
    order: int = Field(ge=1)
    core: MaterialRef
    anchor: Anchor
    user_record: RecordContent | None
    observation: MovementObservation | None
    place_reference: tuple[BackgroundPiece, ...]
    narration: SceneNarration

    @model_validator(mode="after")
    def one_core(self):
        if (self.user_record is None) == (self.observation is None):
            raise ValueError("a scene displays either an original record or a device observation")
        return self


class DiaryBundle(DiaryContract):
    # Opt-in format; not StoryboardBundle v4 and not yet an HTTP response variant.
    format: Literal["walk-diary-bundle-v1"] = "walk-diary-bundle-v1"
    client_session_id: Identifier
    input_revision: Digest
    plan_revision: Digest
    title: str = Field(min_length=1, max_length=80)
    title_origin: Literal["model", "system"]
    model_status: Literal["accepted", "not_requested", "unavailable"]
    semantic_status: Literal["not_evaluated"] = "not_evaluated"
    failure_code: Literal["provider_failed", "invalid_response", "interrupted"] | None
    photos_status: Literal["complete", "not_available"]
    scenes: tuple[DiaryScene, ...]

    @model_validator(mode="after")
    def consistent(self):
        if not self.title.strip():
            raise ValueError("blank diary title")
        if (self.model_status == "accepted") != (self.title_origin == "model"):
            raise ValueError("title origin must identify the actual writer")
        if (self.model_status == "unavailable") != (self.failure_code is not None):
            raise ValueError("model failure requires a distinct failure code")
        if [s.order for s in self.scenes] != list(range(1, len(self.scenes) + 1)) or len(
            {s.id for s in self.scenes}
        ) != len(self.scenes):
            raise ValueError("scene numbers must be unique and sequential")
        return self


def assemble_diary(
    source: DiaryInput,
    plan: DiaryPlan,
    receipt: WritingReceipt | None,
    *,
    failure_code: Literal["provider_failed", "invalid_response", "interrupted"] | None = None,
) -> DiaryBundle:
    """Validate references and copy originals. No selection or semantic verification."""
    # Reparse nested dicts: frozen Pydantic models do not make dict values immutable.
    source = DiaryInput.model_validate(source.model_dump(mode="json"))
    plan = DiaryPlan.model_validate(plan.model_dump(mode="json"))
    plan.validate_against(source)
    prose = {}
    if receipt is not None:
        receipt = WritingReceipt.model_validate(receipt.model_dump(mode="json"))
        if failure_code is not None or receipt.plan_revision != plan.revision():
            raise ValueError("writing belongs to a different prepared plan")
        prose = {s.scene_id: s for s in receipt.writing.scenes}
        # Absolute address is displayed separately; do not require the writer to repeat it.
        writable = {
            s.id for s in plan.scenes if any(p.kind != "place_reference" for p in s.background)
        }
        if not writable or len(prose) != len(receipt.writing.scenes) or set(prose) != writable:
            raise ValueError("each writable scene must appear exactly once")
    materials = {material_ref(m).identity: m for m in (*source.records, *source.observations)}
    scenes = []
    for order, stamp in enumerate(plan.scenes, 1):
        core = materials[stamp.core.identity]
        evidence = {p.id for p in stamp.background if p.kind != "place_reference"}
        written = prose.get(stamp.id)
        if written is not None and not set(written.evidence_ids) <= evidence:
            raise ValueError("cross-scene or unknown citation")
        status = (
            ("generated" if written.text is not None else "omitted")
            if written
            else (
                "no_background"
                if not evidence
                else "unavailable"
                if failure_code
                else "not_requested"
            )
        )
        observed = isinstance(core, MovementObservation)
        scenes.append(
            DiaryScene(
                id=stamp.id,
                order=order,
                core=stamp.core,
                anchor=core.anchor,
                user_record=None if observed else core.content,
                observation=core if observed else None,
                place_reference=tuple(p for p in stamp.background if p.kind == "place_reference"),
                narration=SceneNarration(
                    status=status,
                    text=written.text if written else None,
                    evidence_ids=written.evidence_ids if written else (),
                ),
            )
        )
    return DiaryBundle(
        client_session_id=source.client_session_id,
        input_revision=source.revision(),
        plan_revision=plan.revision(),
        title=receipt.writing.title
        if receipt
        else source.started_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d 산책"),
        title_origin="model" if receipt else "system",
        model_status="accepted" if receipt else "unavailable" if failure_code else "not_requested",
        failure_code=failure_code,
        photos_status=source.photos_status,
        scenes=tuple(scenes),
    )
