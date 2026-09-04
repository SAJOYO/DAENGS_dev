"""원천 레코드 → 내부 canonical fact 후보.

이 패키지는 외부 Place API 계약이 아니다. KCISA/KTO 원문을 같은 어휘로 읽을 수 있는지
검증하는 순수 projection 층이며, DB·HTTP·현재 resolver에 의존하지 않는다.
"""

from daengs_place.place.source_facts.bundle import CandidateFactBundle, SourceFactKey
from daengs_place.place.source_facts.contract import SourceFactProjection
from daengs_place.place.source_facts.kcisa import project_kcisa
from daengs_place.place.source_facts.kto import project_kto

__all__ = [
    "CandidateFactBundle",
    "SourceFactKey",
    "SourceFactProjection",
    "project_kcisa",
    "project_kto",
]
