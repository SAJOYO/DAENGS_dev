"""원천이 뭐라고 부르는지 → 우리가 뭐라고 부르는지. **분류 정책의 유일한 자리.**

결정 #65 가 "원천 category 에서 정규화한 `kind` 가 검색 입구"라고 정한 이상 그 변환
규칙의 주인은 `place` 다. 원래는 `ingest/{mois,kcisa,kto}.py` 안에 있었다 — argparse ·
DB 세션 · HTTP 클라이언트가 들어찬 batch 파일에 도메인 정책이 얹혀 있었고, `place` 가
그걸 가지러 응용 층을 거꾸로 import 했다 (결정 #67 §2).

방향이 뒤집혔다. 이제 batch 가 카탈로그를 읽는다 — `ingest → place`, 응용 → 도메인.

**`KIND_MAPPING_VERSION` 문자열은 DB `provenance` 에 그대로 기록된다.** 값을 바꾸면 이미
적재된 행의 해석이 어긋나므로, 매핑을 고칠 때만 버전을 올리고 그 변경에서 전체를 재분류한다
(결정 #65).
"""

from dataclasses import dataclass
from typing import Literal

# ------------------------------------------------------------------ MOIS (의료)
MoisKind = Literal["hospital", "pharmacy"]

# 병원/약국이 서로 다른 원천 endpoint 라는 사실을 canonical kind 로 옮기는 첫 명시 버전.
MOIS_KIND_MAPPING_VERSION = "mois-source/1"


@dataclass(frozen=True)
class MoisSource:
    """한 MOIS 원천의 정의. 세 필드를 batch 와 도메인이 나눠 쓴다.

        kind    canonical kind                     — 도메인
        slug    원천 endpoint 경로이자 source_category — 양쪽
        source  provenance 에 남는 원천 식별자        — 도메인
    """

    kind: MoisKind
    slug: str
    source: str


MOIS_SOURCES: dict[MoisKind, MoisSource] = {
    "hospital": MoisSource(
        kind="hospital",
        slug="animal_hospitals",
        source="public:mois:animal_hospital",
    ),
    "pharmacy": MoisSource(
        kind="pharmacy",
        slug="animal_pharmacies",
        source="public:mois:animal_pharmacy",
    ),
}

# ------------------------------------------------------------------ KCISA (비의료)
# v1은 KCISA/KTO 용품을 모두 goods로 접었던 규칙. v2부터 원천 category를 보존해 pet_shop이다.
KCISA_KIND_MAPPING_VERSION = "kcisa-category3/2"

# 카테고리3 → kind 슬러그. 새 값이 나타나면 'etc'로 눕히고 category3 원문으로 추적한다.
KCISA_KINDS = {
    "동물병원": "hospital",
    "동물약국": "pharmacy",
    "반려동물용품": "pet_shop",
    "미용": "grooming",
    "여행지": "travel",
    "박물관": "museum",
    "미술관": "gallery",
    "문예회관": "arts_center",
    "카페": "cafe",
    "식당": "restaurant",
    "펜션": "pension",
    "호텔": "hotel",
    "위탁관리": "boarding",
}

# ------------------------------------------------------------------ KTO (관광)
# v1은 KCISA/KTO 용품을 모두 goods로 접었던 규칙. v2부터 contenttypeid=38은 shopping이다.
KTO_KIND_MAPPING_VERSION = "kto-contenttypeid/2"

# contenttypeid → kind. KCISA와 다른 분류 체계라 겹치는 슬러그만 겹치게 맞춘다.
KTO_KINDS = {
    "12": "travel",
    "14": "culture",
    "28": "leisure",
    "32": "stay",
    "38": "shopping",
    "39": "restaurant",
}
