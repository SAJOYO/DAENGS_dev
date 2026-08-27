"""alembic 도입 전부터 쓰던 DB 가 **실제로** 어디까지 적용됐는지 판별한다.

일괄 `stamp` 를 금지하기 위해 있다. 기존 initdb 방식은 볼륨 최초 생성 때만 돌아서, 그 뒤에
추가된 마이그레이션은 각 DB 마다 적용됐을 수도 아닐 수도 있다. 그런 DB 를 head 로 stamp 하면
뒤처진 스키마를 최신이라고 위장하게 되고, 이 러너를 넣은 이유 자체가 첫날 무너진다.

판별은 스키마 지표로 한다 — 각 마이그레이션이 **처음** 만드는 테이블이나 컬럼 하나.

**데이터만 바꾸는 리비전**(예: 0016, 어휘에서 빠진 태그 정리)은 지표가 없다. 그런 리비전은
판별에서 **투명**하다 — 앞뒤 지표만 보고 지나간다. 그래도 되는 이유: 데이터 전용 리비전은
alembic 도입 **뒤에만** 생기고, 그 시대의 DB 는 `alembic_version` 이 권위라 스키마 모양으로
판별할 일이 없다. 이관 대상인 옛 DB 는 0012 이하에서 멈춰 있으므로 `upgrade head` 가 실제로
실행한다. 지표를 지어내서 채우지 않는 이유는 이 모듈이 존재하는 이유와 같다 — 틀린 지표는
없는 지표보다 나쁘다.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class LegacyMarker:
    """리비전 하나와, 그게 적용됐는지 알려주는 스키마 지표."""

    revision: str
    source: str
    table: str | None = None      # None = 스키마를 안 바꾸는 리비전. 판별에서 투명하다
    column: str | None = None
    constraint: str | None = None

    def __post_init__(self) -> None:
        if self.column is not None and self.constraint is not None:
            raise ValueError("a marker cannot be both a column and a constraint")
        if self.table is None and (self.column is not None or self.constraint is not None):
            raise ValueError("a column or constraint marker requires a table")

    @property
    def detectable(self) -> bool:
        return self.table is not None

    def __str__(self) -> str:
        if self.table is None:
            return "스키마 지표 없음 (데이터 전용)"
        if self.column:
            return f"{self.table}.{self.column}"
        if self.constraint:
            return f"{self.table}.{self.constraint} 제약"
        return f"{self.table} 테이블"


# alembic/versions 의 체인과 **같은 순서**여야 한다. 011 이 두 개인 것은 파일명 정렬이 아니라
# main 에 들어온 순서를 따른다 — walk_fix_chain 이 먼저, anchor 가 PR #46 으로 나중.
LEGACY_MARKERS: tuple[LegacyMarker, ...] = (
    LegacyMarker("0001", "001_init.sql", "place"),
    LegacyMarker("0002", "002_tags_scale.sql", "place", "tags"),
    LegacyMarker("0003", "003_mois_ingest.sql", "ingest_state"),
    LegacyMarker("0004", "004_kcisa_facility.sql", "facility"),
    LegacyMarker("0005", "005_facility_link_multi_source.sql", "facility_link"),
    LegacyMarker("0006", "006_facility_source_ref.sql", "facility", "source_ref"),
    LegacyMarker("0007", "007_walk_sessions.sql", "walk_session"),
    LegacyMarker("0008", "008_walk_collection_hardening.sql", "walk_session", "state"),
    LegacyMarker("0009", "009_walk_encounter.sql", "walk_encounter"),
    LegacyMarker("0010", "010_walk_encounter_occurrence.sql", "walk_encounter",
                 "occurrence_version"),
    LegacyMarker("0011", "011_walk_fix_chain.sql", "walk_fix", "chain_index"),
    LegacyMarker("0012", "011_anchor.sql", "anchor"),
    # 0013 부터는 alembic 도입 **뒤에** 만든 변경이다. 이관 전 DB 에 있을 수 없으니 판별에는
    # 안 걸리지만, 지표를 같이 넣어야 HEAD 가 최신 리비전을 가리키고 판별이 "여기까지 왔다"를
    # 정직하게 말한다. 지표 없이 리비전만 늘면 up_to_date 가 뒤처진 DB 를 최신이라고 한다.
    LegacyMarker("0013", "0013_facility_pet_axes.py", "facility", "pet_allowed"),
    LegacyMarker("0014", "0014_encounter_bands_10_15_20.py", "walk_encounter", "dwell_s_15m"),
    LegacyMarker("0015", "0015_walk_session_curve.py", "walk_facts", "curve"),
    LegacyMarker("0016", "0016_drop_specialty_tags.py"),   # 데이터 전용 — 위 설명 참고
    LegacyMarker(
        "0017", "0017_split_goods_kinds.py", "facility",
        constraint="facility_kind_not_legacy_goods",
    ),
)

HEAD = LEGACY_MARKERS[-1].revision


@dataclass(frozen=True)
class Detection:
    """`stamp` 해도 되는 지점, 또는 왜 안 되는지."""

    stamp_at: str | None
    """연속으로 적용된 마지막 리비전. None 이면 빈 DB 라 stamp 없이 upgrade 하면 된다."""

    missing: tuple[LegacyMarker, ...]
    """아직 적용되지 않은 것들. upgrade 가 채운다."""

    out_of_order: tuple[LegacyMarker, ...]
    """빠진 것 **뒤에** 있는데 이미 존재하는 것. 있으면 자동 판별을 신뢰할 수 없다."""

    @property
    def safe(self) -> bool:
        return not self.out_of_order

    @property
    def up_to_date(self) -> bool:
        return self.stamp_at == HEAD


def detect(present: Callable[[LegacyMarker], bool]) -> Detection:
    """지표 존재 여부를 묻는 함수 하나만 받는다. DB 접속은 호출자 몫이라 테스트가 쉽다.

    연속으로 존재하는 앞부분까지가 `stamp_at` 이다. 그 뒤에도 존재하는 게 섞여 있으면
    (`out_of_order`) 손으로 만졌거나 revert 된 흔적이므로 판별을 포기한다 — 틀린 stamp 는
    stamp 를 안 한 것보다 나쁘다.
    """
    applied: list[LegacyMarker] = []
    for marker in LEGACY_MARKERS:
        if marker.detectable and not present(marker):
            break
        applied.append(marker)          # 지표 없는 리비전은 묻지 않고 지나간다

    rest = LEGACY_MARKERS[len(applied):]
    return Detection(
        stamp_at=applied[-1].revision if applied else None,
        missing=tuple(rest),
        out_of_order=tuple(m for m in rest if m.detectable and present(m)),
    )
