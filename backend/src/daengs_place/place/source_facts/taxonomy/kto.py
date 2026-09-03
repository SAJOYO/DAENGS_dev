"""KTO 신분류 코드의 구조와 broad purpose.

`lclsSystmCode2`가 공식 label 권위다. 현재 저장 레코드는 코드만 보유하므로 label을 이름에서
추측하지 않는다. PR1은 계층 코드를 손실 없이 보존하고, 공식 코드 snapshot을 붙일 자리를
고정한다.
"""

from itertools import pairwise

from daengs_place.place.source_catalog import KTO_KINDS
from daengs_place.place.source_facts.contract import TaxonomyNode

KTO_TAXONOMY_VERSION = "kto-lcls-codes/1"


def purpose_for(content_type_id: str | None) -> str | None:
    value = (content_type_id or "").strip()
    return KTO_KINDS.get(value)


def path_from(item: dict) -> tuple[TaxonomyNode, ...]:
    """빈 레벨을 제외하되 원천 코드를 label로 위장하지 않는다."""

    result = []
    for field in ("lclsSystm1", "lclsSystm2", "lclsSystm3"):
        value = str(item.get(field) or "").strip()
        if value:
            result.append(TaxonomyNode(code=value))
    return tuple(result)


def hierarchy_is_valid(path: tuple[TaxonomyNode, ...]) -> bool:
    """KTO 코드는 L2가 L1, L3가 L2 접두를 포함한다."""

    return all(child.code.startswith(parent.code) for parent, child in pairwise(path))
