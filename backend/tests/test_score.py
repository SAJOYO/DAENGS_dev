"""검문소④ 새 지표 단위 테스트 — **`data/` 없이 돈다** (RAG-030 ①, RAG-029).

여기서 지키려는 것은 실측(2026-08-28, `lap1`~`lap6`)에서 지금 지표(`cited`/`ungrounded`)가
새는 것으로 확인된 세 자리다. 실물 랩과 같은 모양의 dict 를 손으로 만들어 쓴다 — 소급
채점기(`grounded_from_dump`)가 보는 것이 바로 이 dict 모양(`generate.dump_rows` 가 쓰는 것)
이라, 픽스처를 실물 그대로 흉내 내는 것이 곧 회귀 테스트다.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from daengs_life.rag.stages import score


def _dump_hit(chunk_id: str, tier: str) -> dict:
    """저장된 랩의 `DumpHit.model_dump()` 모양. 채점에 쓰는 것은 `tier` 뿐이다."""
    return {"rank": 1, "score": 0.5, "chunk_id": chunk_id, "logical": chunk_id,
            "citation": chunk_id, "tier": tier}


def _row(text: str, hits: list[dict], cited: list[str] | None = None) -> dict:
    return {"id": "X", "question": "q", "text": text, "hits": hits, "cited": cited or []}


# ------------------------------------------------------------------ ① 근거 번호 파싱
def test_referenced_indices_dedups_and_sorts() -> None:
    assert score.referenced_indices("먼저 [3]을 보고 [1]과 [3]도 봅니다") == [1, 3]


def test_referenced_indices_empty_when_no_refs() -> None:
    assert score.referenced_indices("근거를 안 든 답변") == []


def test_referenced_hits_drops_out_of_range() -> None:
    """모델이 `[9]` 를 지어내도(hits 가 3개뿐이면) 죽지 않고 조용히 버린다."""
    hits = [_dump_hit("a", "-"), _dump_hit("b", "must"), _dump_hit("c", "-")]
    assert [h["chunk_id"] for h in score.referenced_hits("[2]과 [9]", hits)] == ["b"]


def test_referenced_hits_is_one_indexed() -> None:
    hits = [_dump_hit("first", "-"), _dump_hit("second", "must")]
    assert score.referenced_hits("[1]", hits)[0]["chunk_id"] == "first"


# ------------------------------------------------------------------ ② 라이브 채점 (search.Hit)
@dataclass
class _FakeHit:
    """`search.Hit` 을 흉내 낸다 — `grounds_the_answer` 가 보는 건 `chunk_id` 뿐이다."""
    chunk_id: str


def test_grounds_the_answer_true_when_must_is_referenced() -> None:
    hits = [_FakeHit("src-a__20260101#제1조"), _FakeHit("src-b#제2조")]
    must = {"src-a#제1조"}                          # 논리 주소 — 날짜가 빠져 있다
    assert score.grounds_the_answer("근거는 [1]입니다", hits, must)


def test_grounds_the_answer_false_when_must_not_referenced() -> None:
    """must 청크가 컨텍스트에 있어도 답변이 **그것을 지목하지 않으면** False 다."""
    hits = [_FakeHit("src-a#제1조"), _FakeHit("src-b#제2조")]
    must = {"src-a#제1조"}
    assert not score.grounds_the_answer("근거는 [2]입니다", hits, must)


def test_grounds_the_answer_false_when_no_labels() -> None:
    """must 가 빈 집합(자유 질의)이면 무엇을 지목해도 False — 잴 수 없는 것이지 틀린 게 아니다."""
    hits = [_FakeHit("src-a#제1조")]
    assert not score.grounds_the_answer("[1]", hits, set())


# ------------------------------------------------------------------ ③ 실측 다섯 사례의 회귀 테스트
def test_case_lap2_q3_refusal_no_longer_counts() -> None:
    """`lap2 Q3` — *"자료에 포함되어 있지 않습니다"* 라며 물러서면서 무관한 조항 3개를
    나열했다. 지금 지표(`cited`)는 이것을 '인용한 문항'으로 센다. 새 지표는 안 센다 —
    나열한 근거 [1]~[5] 의 tier 가 전부 '-' 다."""
    text = ("제공해주신 참고자료에는 반려동물의 목줄 미착용에 따른 과태료 정보가 "
            "포함되어 있지 않습니다. 제공된 자료([1]~[5])는 『가축전염병 예방법 시행령』에 "
            "따른 사항만 규정하고 있습니다. (근거 법조문: 법 제60조)")
    hits = [_dump_hit(f"livestock-decree#별표{i}", "-") for i in range(1, 6)]
    row = _row(text, hits, cited=["제60조"])
    assert row["cited"]                                          # 현행 지표는 여전히 '인용함'
    assert not score.grounded_from_dump(row)                     # 새 지표는 '아니오'


def test_case_lap6_s4_correct_answer_without_article_number_now_counts() -> None:
    """`lap6 S4` — 보조금24 상세는 `제N조` 가 없다. "마리당 20만원 이내" 로 정확히
    답해도 `cited=[]` 라 지금 지표는 0으로 센다. 새 지표는 `[1]` 이 must 를 가리키면 센다."""
    text = ("필수진료는 마리당 20만 원 이내로 지원하며, 보호자는 1만 원을 부담해야 "
            "합니다 [1]. 선택진료는 필수진료 후 추가 치료에 대해 20만 원 이내로 지원합니다 [1].")
    hits = [_dump_hit("benefit24-services-305000000130#지원내용", "must")]
    row = _row(text, hits, cited=[])                              # 조 번호가 없어 현행 지표는 0
    assert not row["cited"]
    assert score.grounded_from_dump(row)


def test_case_lap4_s3_wrong_jurisdiction_no_longer_counts() -> None:
    """`lap4 S3` — 정답은 동래구 조례 제8조인데 답변이 대전 서구 조례 제6조를 들었다.
    조 번호만 보는 지금 지표는 `제6조` 가 컨텍스트에 있으니 통과시킨다. 새 지표는
    **어느 문서의 제6조인지** 를 본다 — 그 문서의 tier 가 '-' 라 안 센다."""
    hits = [_dump_hit("ordinance-search-daejeon#제6조", "-"),     # 대전 서구 (오답, 답변이 인용함)
            _dump_hit("ordinance-search-dongnae#제8조", "must")]  # 동래구 (정답, 안 인용됨)
    row = _row("대전광역시 서구 조례 제6조에 따라 지원할 수 있습니다 [1].", hits, cited=["제6조"])
    assert row["cited"]                                           # 현행 지표는 '인용함' — 오답인데
    assert not score.grounded_from_dump(row)                      # 새 지표는 '아니오'


def test_case_lap6_s3_fixed_after_hybrid_search_now_counts() -> None:
    """같은 S3, 하이브리드 도입 후(`lap6`)에는 답변이 [2]로 동래구를 정확히 지목한다."""
    hits = [_dump_hit("ordinance-search-jungu#제4조", "-"),
            _dump_hit("ordinance-search-dongnae#제8조", "must")]
    row = _row("부산광역시동래구 조례 제8조에 따라 지원합니다 [2].", hits, cited=["제8조"])
    assert score.grounded_from_dump(row)


# ------------------------------------------------------------------ ④ 강건성 — 옛 형식·빈 값
def test_grounded_from_dump_handles_missing_hits() -> None:
    """`hits` 키 자체가 없는 옛 덤프. 없는 것을 있다고 지어내지 않고 조용히 False."""
    assert not score.grounded_from_dump({"text": "[1]"})


def test_grounded_from_dump_handles_missing_tier() -> None:
    """`tier` 필드가 없는 hit(더 옛 형식). KeyError 없이 그냥 must 가 아닌 것으로 본다."""
    row = _row("[1]", [{"chunk_id": "a"}])                        # tier 키 없음
    assert not score.grounded_from_dump(row)


def test_grounded_from_dump_empty_text() -> None:
    assert not score.grounded_from_dump(_row("", [_dump_hit("a", "must")]))


# ------------------------------------------------------------------ ⑤ 집계
def test_score_rows_matches_dump_read() -> None:
    """`lap6` 세 문항(Q3·S4·S3-old) 을 한 번에 집계 — cited 3/3, grounded 1/3."""
    rows = [
        _row("무관한 조항만 나열 [1]", [_dump_hit("x#제1조", "-")], cited=["제1조"]),
        _row("정확한 답변 [1]", [_dump_hit("y#지원내용", "must")], cited=[]),
        _row("틀린 문서의 같은 조 번호 [1]", [_dump_hit("z-wrong#제6조", "-")], cited=["제6조"]),
    ]
    s = score.score_rows(rows)
    assert s == {"n": 3, "cited": 2, "grounded": 1}


def test_six_laps_retroactive_summary_matches_measured_values() -> None:
    """실측치 고정 — 이 값이 바뀌면 `score.py` 의 판정 로직이 바뀐 것이다.

    저장된 `lap1`~`lap6` 이 없는 PC(=이 테스트를 포함해 `data/` 미추적)에서는 이 테스트
    자체가 못 돈다 — `data/processed/answers/*.jsonl` 을 요구하는 유일한 테스트라 여기서만
    skip 을 허용한다. 나머지 위 테스트는 전부 손으로 만든 dict 라 `data/` 없이도 돈다.
    """
    from daengs_life.rag.core import io

    paths = {p.stem: p for p in (io.answer_files() if io_has_data() else [])}
    expected = {"lap1": (7, 6, 2), "lap2": (7, 7, 3), "lap3": (7, 6, 2),
                "lap4": (12, 8, 6), "lap5": (12, 8, 8), "lap6": (12, 8, 7)}
    missing = [name for name in expected if name not in paths]
    if missing:
        pytest.skip(f"data/processed/answers 에 없음: {missing} — 2026-08-28 세션에서 측정한 값")

    for name, (n, cited, grounded) in expected.items():
        _, rows = io.read_answers(paths[name])
        s = score.score_rows(rows)
        assert (s["n"], s["cited"], s["grounded"]) == (n, cited, grounded), name


def io_has_data() -> bool:
    from daengs_life.rag.core import config
    return config.ANSWER_DIR is not None and config.ANSWER_DIR.exists()
