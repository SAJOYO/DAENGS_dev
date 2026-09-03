"""산책 측정과 공간 일기를 조립하는 제품 기능 패키지.

현재 공개 표면은 DB와 HTTP를 모르는 측정 커널이다. 패키지 전체를 별도 런타임으로
격리하지는 않는다. 이후 응용부는 Place·Journey 능력을 소비할 수 있지만, 그 구현과
데이터 저장소를 복제하거나 우회하지 않는다 (D-045).
"""

from daengs_walk.cellophane import Cellophane, build_cellophane
from daengs_walk.capsule import WalkCapsuleArtifacts, build_walk_capsule
from daengs_walk.contracts import WalkEvidencePoint
from daengs_walk.evidence import WalkEvidenceBundle, analyze_walk

__all__ = [
    "Cellophane",
    "WalkCapsuleArtifacts",
    "WalkEvidenceBundle",
    "WalkEvidencePoint",
    "analyze_walk",
    "build_cellophane",
    "build_walk_capsule",
]
