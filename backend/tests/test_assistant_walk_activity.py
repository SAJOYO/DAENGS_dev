"""D-072 — 기록된 산책이 비서 프롬프트까지 가는 길, 그리고 **못 잴 때 무엇을 말하는가**.

DB 는 안 씁니다. `test_assistant_care_log.py` 와 같은 꼴로 리포지토리를 가짜로 바꿉니다.
여기서 보는 것은 **규칙**입니다 — 좌표가 한 칸도 안 넘어가는가, 측정 안 된 산책이 합계에
안 섞이는가, 기록이 없는 요청의 프롬프트가 이 카드 전과 글자까지 같은가.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from daengs_backend.orchestration.contracts import GeneralPayload, WalkActivityContext
from daengs_backend.orchestration.redirects import DISTANCE_FROM_RECORDED_WALKS_ONLY
from daengs_backend.repositories.walk import WalkActivitySums, _measured_stmt, _walked_stmt


def _compiled(stmt) -> str:
    """리터럴을 박아 컴파일한 SQL, 공백을 한 칸으로 접어 소문자로.

    `inspect.getsource` 대신 이것을 쓴다 — 소스 텍스트 검사는 독스트링까지 함께
    읽어서, 조인 조건을 `WalkAnalysis.walk_id == Walk.id` 로 되돌려도 독스트링에
    `ActivityWalkHead` 한 마디만 남아 있으면 통과해 버린다. 컴파일된 SQL 은
    실제로 어떤 조인이 나가는지를 본다 — DB·픽스처·네트워크 없이 이 venv 안에서
    즉시 만들 수 있다(`select().compile(dialect=...)`).
    """
    compiled = stmt.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    return re.sub(r"\s+", " ", str(compiled)).strip().lower()


def test_disclosure_names_the_record_and_refuses_the_described_route() -> None:
    """D-051 ⑤ 의 고지와 같은 성질 — 못 하는 사실과 **그 이유**를 같이 말한다."""
    assert "기록된 산책" in DISTANCE_FROM_RECORDED_WALKS_ONLY
    assert "말씀" in DISTANCE_FROM_RECORDED_WALKS_ONLY
    # 되묻는 문장으로 읽히면 안 된다 — `VET_CONTACT_LOCATION_UNKNOWN` 과 같은 규칙
    assert "?" not in DISTANCE_FROM_RECORDED_WALKS_ONLY


def test_walk_activity_context_carries_no_coordinate() -> None:
    """좌표는 이 카드에서 한 칸도 안 넘어간다 (Global Constraint 2)."""
    for forbidden in ("lat", "lon", "lng", "polyline", "points", "path"):
        assert forbidden not in WalkActivityContext.model_fields, forbidden


def test_measured_walks_never_exceed_recorded_walks() -> None:
    """측정된 산책이 기록된 산책보다 많을 수 없다 — 조인이 중복 합산하면 여기서 걸린다."""
    with pytest.raises(ValidationError):
        WalkActivityContext(
            day="2026-09-12", walk_count=1, measured_walk_count=2,
            distance_m=100, moving_s=60,
        )


def test_measured_walks_equal_to_recorded_walks_is_allowed() -> None:
    """경계값 — 전부 측정됐으면(`==`) 통과해야 한다.

    검증기는 `measured_walk_count > walk_count` 만 거부한다(`>`). 나중에 이 비교가
    `>=` 로 잘못 바뀌면 이 테스트가 그 자리에서 실패한다 — Task 1 리뷰가 남긴 미결.
    """
    WalkActivityContext(
        day="2026-09-12", walk_count=2, measured_walk_count=2,
        distance_m=1_200, moving_s=900,
    )


def test_general_payload_takes_walk_activity_and_life_does_not() -> None:
    """`care_log` 와 같은 규칙 — 폴백에만 간다."""
    from daengs_backend.orchestration.contracts import LifePayload

    activity = WalkActivityContext(
        day="2026-09-12", walk_count=2, measured_walk_count=1,
        distance_m=1_200, moving_s=900, last_started_at="08:30",
    )
    assert GeneralPayload(question="q", walk_activity=activity).walk_activity == activity
    with pytest.raises(ValidationError):
        LifePayload(question="q", walk_activity=activity)


def test_measured_stmt_joins_through_the_head_never_walk_analyses_directly() -> None:
    """컴파일된 SQL 로 조인 사슬을 본다 — `walk_analyses` 직결을 막는 것이 이 카드의 요점.

    `walk_analyses` 는 (walk_id, fingerprint, 버전 4개) 로 유니크라 한 산책에 여러 세대가
    쌓인다. `activity_walk_heads` 를 경유하지 않고 `WalkAnalysis.walk_id == Walk.id` 로
    바로 이으면 거리가 세대 수만큼 불어나는데, 예외도 경고도 안 난다 — 소스 텍스트 검사로는
    이 실패를 못 잡는다(독스트링에 이름만 한 번 나와도 통과한다). 그래서 실제로 나갈 SQL을
    본다.
    """
    pet_id = uuid.uuid4()
    start = datetime(2026, 9, 12, tzinfo=UTC)
    end = start + timedelta(days=1)

    walked_sql = _compiled(_walked_stmt(pet_id, start, end))
    measured_sql = _compiled(_measured_stmt(pet_id, start, end))

    # 1) activity_walk_heads 를 walks.id 로 잇는 조인이 있다
    assert "join activity_walk_heads on activity_walk_heads.walk_id = walks.id" in measured_sql
    # 2) walk_analyses 를 activity_walk_heads.analysis_id 로 잇는다
    assert "join walk_analyses on walk_analyses.id = activity_walk_heads.analysis_id" in measured_sql
    # 3) walk_analyses.walk_id = walks.id 직결이 없다 — 이 카드가 막으려는 그 실패
    assert "walk_analyses.walk_id = walks.id" not in measured_sql

    # 4) WHERE 절이 동일하다 — 다르면 measured_walk_count 가 walk_count 를 넘어설 수 있고,
    #    그러면 WalkActivityContext 의 검증기가 런타임에 터진다.
    walked_where = walked_sql[walked_sql.index(" where "):]
    measured_where = measured_sql[measured_sql.index(" where "):]
    assert walked_where == measured_where


def test_unmeasured_walks_count_but_do_not_add_distance() -> None:
    """봉인 안 된 산책은 건수에는 들어가고 합계에는 안 들어간다 — 계약이 그 조합을 받아준다."""
    sums = WalkActivitySums(
        walk_count=3, measured_walk_count=2, distance_m=1_200, moving_s=900,
        last_started_at=datetime(2026, 9, 12, 8, 30, tzinfo=UTC),
    )
    # "3건 중 2건만 계산됐어요" 를 말하려면 이 조합이 검증을 통과해야 한다
    WalkActivityContext(
        day="2026-09-12", walk_count=sums.walk_count,
        measured_walk_count=sums.measured_walk_count,
        distance_m=sums.distance_m, moving_s=sums.moving_s,
    )
