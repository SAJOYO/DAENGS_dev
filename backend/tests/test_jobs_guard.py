"""적재 가드 (D-062). DB·파일 없이 숫자만 넣고 판정을 본다."""
from daengs_life.jobs import guard


def test_passes_when_rows_grow():
    v = guard.check(before=9_000, planned=9_800, losing=[])
    assert v.ok and v.reasons == []


def test_passes_on_first_load_when_table_is_empty():
    v = guard.check(before=0, planned=9_800, losing=[])
    assert v.ok


def test_blocks_when_rows_drop_more_than_threshold():
    v = guard.check(before=10_000, planned=7_000, losing=[])
    assert not v.ok
    assert any("30%" in r for r in v.reasons)


def test_allows_drop_within_threshold():
    v = guard.check(before=10_000, planned=8_500, losing=[])
    assert v.ok


def test_threshold_is_configurable():
    assert not guard.check(before=100, planned=95, losing=[], max_drop=0.01).ok
    assert guard.check(before=100, planned=95, losing=[], max_drop=0.10).ok


def test_blocks_when_metadata_key_would_vanish():
    v = guard.check(before=100, planned=100, losing=[("org", 2_592, 0)])
    assert not v.ok
    assert any("org" in r for r in v.reasons)


def test_reports_every_reason_not_just_the_first():
    v = guard.check(before=100, planned=10, losing=[("org", 50, 0)])
    assert len(v.reasons) == 2
