"""순수 Walk Capsule 계약을 versioned WalkAnalysis의 1:1 저장 행으로 옮긴다."""

from daengs_backend.models.walk import WalkAnalysis, WalkCapsule
from daengs_walk.capsule import (
    ObservationCapability,
    TrailContextSnapshot,
    WalkCapsuleArtifacts,
    WalkCapsuleManifest,
)


def build_capsule_model(
    analysis: WalkAnalysis,
    artifacts: WalkCapsuleArtifacts,
) -> WalkCapsule:
    """Analysis의 identity와 manifest가 맞는지 확인한 뒤 ORM 자식을 만든다."""

    manifest = artifacts.manifest
    expected = (
        analysis.walk_id,
        analysis.facts_record_version,
        analysis.calculation_version,
        analysis.receipt_version,
        analysis.observation_version,
    )
    actual = (
        manifest.walk_id,
        manifest.facts_record_version,
        manifest.calculation_version,
        manifest.receipt_version,
        manifest.observation_version,
    )
    if actual != expected:
        raise ValueError("capsule manifest가 WalkAnalysis identity와 다릅니다.")

    return WalkCapsule(
        analysis=analysis,
        capsule_version=manifest.capsule_version,
        context_version=artifacts.trail_context.context_version,
        capabilities=[item.model_dump(mode="json") for item in manifest.capabilities],
        trail_context=artifacts.trail_context.model_dump(mode="json"),
        sealed_at=manifest.sealed_at,
    )


def decode_capsule_model(analysis: WalkAnalysis) -> WalkCapsuleArtifacts:
    """저장 payload와 version 컬럼을 검증해 순수 Capsule 계약으로 되돌린다."""

    stored = analysis.capsule
    if stored is None:
        raise ValueError("WalkAnalysis에 Capsule seal이 없습니다.")
    context = TrailContextSnapshot.model_validate(stored.trail_context)
    capabilities = tuple(
        ObservationCapability.model_validate(item) for item in stored.capabilities
    )
    manifest = WalkCapsuleManifest(
        capsule_version=stored.capsule_version,
        walk_id=analysis.walk_id,
        facts_record_version=analysis.facts_record_version,
        calculation_version=analysis.calculation_version,
        receipt_version=analysis.receipt_version,
        observation_version=analysis.observation_version,
        capabilities=capabilities,
        sealed_at=stored.sealed_at,
    )
    if stored.context_version != context.context_version:
        raise ValueError("저장된 context version 컬럼과 payload가 다릅니다.")
    return WalkCapsuleArtifacts(manifest=manifest, trail_context=context)
