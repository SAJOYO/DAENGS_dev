"""Compose one analysis and its sealed sheet without owning locks or commits."""

from daengs_backend.models.walk import WalkAnalysis
from daengs_backend.services.walk_analysis import build_analysis_model
from daengs_backend.services.walk_artifacts.cellophane import build_cellophane_model
from daengs_backend.services.walk_finalize import PreparedWalkEvidence
from daengs_walk.cellophane import Cellophane
from daengs_walk.evidence import WalkEvidenceBundle


def build_analysis_models(
    prepared: PreparedWalkEvidence, evidence: WalkEvidenceBundle, sheet: Cellophane
) -> WalkAnalysis:
    facts = evidence.facts
    if evidence.receipt.walk_id != facts.walk_id or sheet.walk_id != facts.walk_id:
        raise ValueError("analysis 결과의 walk_id가 서로 다릅니다.")
    if evidence.receipt.received_fix_count != prepared.point_count:
        raise ValueError("measurement receipt가 finalize 입력 점 개수와 다릅니다.")
    if sheet.at != facts.started_at:
        raise ValueError("Cellophane 시각이 canonical 산책 시작 시각과 다릅니다.")

    analysis = build_analysis_model(prepared, evidence)
    analysis.cellophane_sheets.append(build_cellophane_model(sheet))
    return analysis
