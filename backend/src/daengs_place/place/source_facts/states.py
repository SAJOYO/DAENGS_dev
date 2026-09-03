"""값과 독립적인 원천 사실 상태."""

from enum import StrEnum


class FactState(StrEnum):
    """`false` 같은 값과 그 값을 얻은 상태를 섞지 않는다."""

    KNOWN = "known"
    NOT_PROVIDED = "not_provided"
    NOT_FETCHED = "not_fetched"
    NOT_APPLICABLE = "not_applicable"
    PARSE_FAILED = "parse_failed"
    UNKNOWN = "unknown"


class EvidenceCertainty(StrEnum):
    """원천의 직접 진술인지, 결정론적 해석인지."""

    SOURCE = "source"
    DERIVED = "derived"


class ProjectionState(StrEnum):
    """레코드 전체 projection의 상태. 개별 사실 상태와 별개다."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class DetailAcquisitionState(StrEnum):
    """목록과 별도인 source detail을 실제로 어떻게 획득했는가."""

    NOT_APPLICABLE = "not_applicable"
    NOT_FETCHED = "not_fetched"
    FETCHED = "fetched"
    NO_DATA = "no_data"
    FETCH_FAILED = "fetch_failed"
    # 이 테이블 이전의 `{}`처럼 과거 저장만으로 원인을 복원할 수 없는 경우다.
    UNKNOWN = "unknown"


def acquisition_fact_state(state: DetailAcquisitionState) -> FactState:
    """획득 lifecycle을 값 evidence 상태로 옮기는 유일한 매핑."""

    return {
        DetailAcquisitionState.NOT_APPLICABLE: FactState.NOT_APPLICABLE,
        DetailAcquisitionState.NOT_FETCHED: FactState.NOT_FETCHED,
        DetailAcquisitionState.FETCHED: FactState.KNOWN,
        DetailAcquisitionState.NO_DATA: FactState.NOT_PROVIDED,
        DetailAcquisitionState.FETCH_FAILED: FactState.UNKNOWN,
        DetailAcquisitionState.UNKNOWN: FactState.UNKNOWN,
    }[state]
