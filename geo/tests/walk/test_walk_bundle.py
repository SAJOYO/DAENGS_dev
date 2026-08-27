"""`scripts/verify/walk_bundle.py` 의 순수 부분. adb·HTTP 는 여기서 안 돈다.

push 의 실동작(멱등 재전송)은 서버 계약 테스트(`test_walk_store.py` 의 duplicates)와
기기 export 계약(`WalkSessionExporterTest`)이 각각 지킨다 — 이 파일은 그 둘을 잇는
변환이 어긋나지 않는지만 본다.
"""

import pytest

from scripts.verify.walk_bundle import (
    BATCH,
    PushUnavailable,
    batches,
    facts_summary,
    needs_push,
    session_id_of,
)


def test_batches_respect_the_server_cap():
    fixes = [{"client_seq": i} for i in range(BATCH * 2 + 5)]

    got = batches(fixes)

    assert [len(b) for b in got] == [BATCH, BATCH, 5]
    assert got[0][0]["client_seq"] == 0 and got[-1][-1]["client_seq"] == BATCH * 2 + 4


def test_session_id_reads_the_export_shape():
    export = {"session": {"id": "s-1", "dog_id": "halmae"}, "fixes": []}
    assert session_id_of(export) == "s-1"


def test_facts_summary_says_when_there_are_no_facts_yet():
    assert "사실 없음" in facts_summary({"facts": None})


def test_facts_summary_carries_the_numbers_a_field_note_needs():
    derived = {
        "facts": {"duration_s": 280, "moving_distance_m": 343, "stop_count": 1,
                  "fix_count": 58, "evidence_origin": "device"},
        "encounters": [{}, {}],
    }
    line = facts_summary(derived)
    for token in ("280s", "343m", "정지 1회", "fix 58", "device", "조우 2건"):
        assert token in line, f"요약에 {token} 이 없다"


# ---------------------------------------------------------------- push 판정
# 기준은 "서버에 세션이 있나" 가 아니라 "finish 까지 가서 사실이 확정됐나" 다.
# 존재만 보면 가장 흔한 실패(부분 업로드)를 건너뛴다 — 이 도구가 복구해야 할 바로 그것.

def test_a_session_the_server_never_saw_is_pushed():
    assert needs_push(404, None) is True


def test_a_partly_uploaded_session_is_pushed_again():
    """세션 행은 있고 fix 일부만 올라간 뒤 끊긴 상태. 200 이지만 사실이 없다."""
    open_session = {"session": {"state": "open", "fix_count": 20}, "facts": None}

    assert needs_push(200, open_session) is True


def test_a_finished_session_is_left_alone():
    done = {"session": {"state": "purged"}, "facts": {"duration_s": 280}}

    assert needs_push(200, done) is False


def test_an_unhealthy_server_is_not_read_as_missing():
    """500 을 '미업로드' 로 읽고 재전송하면 아픈 서버를 더 때린다."""
    with pytest.raises(PushUnavailable):
        needs_push(500, None)
