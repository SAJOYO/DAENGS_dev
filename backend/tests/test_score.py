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
    # `scored`·`boundary` 는 RAG-062 가 더한 칸이다. `ckinds` 없이 부르면 경계를 가릴 길이
    # 없으므로 **아무것도 빼지 않는다** — 그래서 `scored == n` 이고 `boundary == 0` 이다.
    assert s == {"n": 3, "scored": 3, "boundary": 0, "cited": 2, "grounded": 1}


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


# ------------------------------------------------------------------ 기대 채점 (RAG-055)
# A0 스모크가 남긴 두 문장을 그대로 픽스처로 쓴다 (`assistant-life-gcp-smoke.md` §3).
# **후보 신호를 재는 것이 목적이므로, 픽스처가 실물 말투를 흉내 내는 것이 곧 테스트다.**

A0_PROSE = ("제시해주신 [참고자료]에는 목줄 미착용에 대한 과태료 규정이 포함되어 있지 않습니다. "
            "다만 관련된 과태료로는 [1] 보험 미가입 등이 있습니다.")
A0_REINTERPRET = ("질문하신 '우주선'은 반려동물 운송 용기를 의미하는 것으로 이해하여 답변드립니다. "
                  "[1] 동물보호법 제16조에 따라 …")
PLAIN = "[1] 「동물보호법」 제101조제4항에 따라 50만원 이하의 과태료가 부과됩니다."


def _scored_row(qid: str, text: str, top: float, cited: list[str] | None = None) -> dict:
    hit = _dump_hit("c1", "-") | {"score": top}
    return {"id": qid, "question": "q", "text": text, "hits": [hit], "cited": cited or []}


def test_self_report_needs_both_no_citation_and_the_prose() -> None:
    """`cited == []` 단독으로 걸면 **비법령 소스가 전부 기권된다** — 약관·항공 문서는 조항
    번호가 없어 비어 있는 것이 정상이다 (카드 #177 컨텍스트 메모). 자기보고와 묶어서 본다.
    """
    assert score.ABSTAIN_POLICIES["selfreport"](_scored_row("Q", A0_PROSE, 0.7))
    # 조항을 들었으면 물러선 것이 아니다
    assert not score.ABSTAIN_POLICIES["selfreport"](
        _scored_row("Q", A0_PROSE, 0.7, cited=["제16조"]))
    # 인용이 없어도 물러섰다고 말하지 않으면 아니다 — 약관 답변이 여기 걸린다
    assert not score.ABSTAIN_POLICIES["selfreport"](
        _scored_row("Q", "이동장에 넣어 탑승하면 됩니다.", 0.7))


def test_a_negation_topic_answer_is_not_a_retreat() -> None:
    """**lap15 가 실물로 남긴 오작동이다** (RAG-055). I4 는 *"보험금을 지급하지 않는 경우"* 를
    묻는 문항이라 **좋은 답변이 통째로 부정문**이다 — *"약관에 따라 다음과 같은 경우 보험금이
    지급되지 않습니다"*. 물러선 것이 아니라 그것이 답이다.

    문장으로 물러섬을 읽는 신호가 **주제가 부정문인 문항에서 새는** 자리이고, 지금 코퍼스에는
    면책·금지·제한을 묻는 문항이 여럿이라(I4 · Q7 · QA7) 좁은 자리가 아니다. 그래서 부정이
    **자료를 주어로** 걸릴 때만 본다 — 이 테스트가 그 좁힘을 붙잡는다.
    """
    i4 = ("제시된 참고자료를 바탕으로 펫보험에서 보험금을 지급하지 않는 사유를 안내해 드립니다. "
          "각 보험 상품의 약관에 따라 다음과 같은 경우 보험금이 지급되지 않습니다. "
          "위생관리 및 미모를 위한 성형수술, 선천적 기형 등이 이에 해당합니다.")
    assert not score.says_no_evidence(i4)
    assert not score.ABSTAIN_POLICIES["selfreport"](_scored_row("I4", i4, 0.68))


def test_the_no_evidence_wordings_lap15_actually_produced() -> None:
    """랩이 실제로 낸 네 가지 말투. **픽스처가 실물이라는 것이 이 테스트의 값이다** —
    검출기를 좁힐 때 어느 것도 잃지 않았음을 여기서 붙잡는다 (RAG-055).
    """
    for text in [
        "제공해주신 [참고자료]에는 목줄 미착용에 관한 과태료 규정이 포함되어 있지 않아 답변을 드릴 수 없습니다.",   # B1
        "제공해주신 [참고자료]에는 목줄 미착용에 대한 과태료 규정이 포함되어 있지 않습니다.",                       # Q3
        "보험별로 가입 가능한 최대 연령에 관한 구체적인 수치는 기재되어 있지 않습니다.",                            # B3
        "제공된 자료만으로는 원인이나 질병에 대해 확인하거나 안내해 드릴 수 없습니다.",                             # B4
    ]:
        assert score.says_no_evidence(text), text


def test_reinterpretation_catches_what_the_citation_signal_misses() -> None:
    """A0 ③ 이 이 자리다 — `cited == ["제16조"]` 라 자기보고 신호로는 안 걸린다 (§3-2).
    후보가 하나로는 부족하다는 것이 이 테스트가 남기는 사실이다.
    """
    row = _scored_row("B2", A0_REINTERPRET, 0.62, cited=["제16조"])
    assert not score.ABSTAIN_POLICIES["selfreport"](row)
    assert score.ABSTAIN_POLICIES["reinterpret"](row)
    assert score.ABSTAIN_POLICIES["selfreport+reinterpret"](row)


def test_top_score_reads_the_cosine_not_the_rank() -> None:
    """`hits[].score` 는 코사인이다 — RRF 가 순서를 정하지만 점수 칸의 뜻은 안 바뀌었다
    (`search.py`). 그래서 랩끼리 같은 자로 비교되고 문턱을 숫자로 적을 수 있다.
    """
    row = {"hits": [_dump_hit("a", "-") | {"score": 0.41},
                    _dump_hit("b", "-") | {"score": 0.58}]}
    assert score.top_score(row) == 0.58
    assert score.top_score({"hits": []}) == 0.0
    assert score.ABSTAIN_POLICIES["score<0.60"](row)
    assert not score.ABSTAIN_POLICIES["score<0.55"](row)


def test_grade_expect_counts_the_two_directions_separately() -> None:
    """**한 수로 합치면 정반대의 정책이 같은 점수를 받는다** (RAG-055).

    아무것도 기권 안 하는 정책과 전부 기권하는 정책이 그렇다. 그래서 오기권과 놓친 기권을
    갈라 센다 — 신호가 과하게 켜졌는지 안 켜졌는지는 다른 고침을 부른다.
    """
    rows = [_scored_row("Q3", PLAIN, 0.71, cited=["제101조"]),   # 답해야 하고, 답했다
            _scored_row("B1", A0_PROSE, 0.70),                   # 답해야 하는데 물러섰다
            _scored_row("B2", A0_PROSE, 0.62)]                   # 기권해야 하고, 물러섰다
    expects = {"Q3": "answer", "B1": "answer", "B2": "abstain"}

    g = score.grade_expect(rows, expects, "selfreport")
    assert (g["answer_n"], g["abstain_n"]) == (2, 1)
    assert g["false_abstain"] == 1 and g["missed_abstain"] == 0
    assert g["passed"] == 2 and g["gradable"] == 3
    assert ("B1", "답해야 하는데 기권") in g["failures"]

    # 기준선은 정반대로 틀린다 — 하나도 기권하지 않으므로 오기권 0, 놓친 기권 1
    base = score.grade_expect(rows, expects, "none")
    assert (base["false_abstain"], base["missed_abstain"]) == (0, 1)
    assert base["passed"] == 2


def test_refuse_items_are_counted_as_unmeasurable_not_failed() -> None:
    """**못 재는 것을 0으로 세지 않는다** (RAG-055).

    증상·응급 거절은 검색 결과가 아니라 질문을 보고 갈라야 하고, 그 분류는 생성 앞단에서
    일어난다 — 랩 덤프에는 흔적이 없다. 실패로 세면 어떤 정책을 골라도 점수가 같이 깎여
    정책 비교가 흐려진다.
    """
    rows = [_scored_row("B4", "슬개골 탈구가 의심됩니다.", 0.66, cited=["제3조"])]
    g = score.grade_expect(rows, {"B4": "refuse"}, "selfreport")
    assert g["unmeasurable"] == 1
    assert g["gradable"] == 0 and g["passed"] == 0
    assert g["failures"] == []


def test_rows_the_goldenset_no_longer_has_are_skipped() -> None:
    """골든셋에서 지워진 옛 문항은 채점하지 않는다 — `lap1`~`lap14` 를 이 표로 읽을 때
    없는 문항이 실패로 세어지면 소급 비교가 통째로 어긋난다.
    """
    g = score.grade_expect([_scored_row("사라진문항", PLAIN, 0.7)], {"Q3": "answer"}, "none")
    assert g["gradable"] == 0 and g["unmeasurable"] == 0


def io_has_data() -> bool:
    from daengs_life.rag.core import config
    return config.ANSWER_DIR is not None and config.ANSWER_DIR.exists()


# ------------------------------------------------------------------ ⑧ 종류별 슬라이스 (RAG-060)
def _chunk(chunk_id: str, **fields: str) -> dict:
    """청크 jsonl 의 콘텐츠 행 모양. 슬라이스가 보는 것은 `chunk_id` 와 축 하나뿐이다."""
    return {"type": "chunk", "chunk_id": chunk_id, "content": "…", **fields}


def test_corpus_kinds_strips_the_collection_date() -> None:
    """골든셋 라벨은 날짜를 뗀 주소(`logical`)를 쓰고 청크는 날짜를 달고 있다.

    이 한 줄이 없으면 **모든 문항이 `(코퍼스 밖)`** 이 된다 — 조인이 통째로 어긋나는데
    표에는 칸 하나로만 보여서 알아채기 어렵다.
    """
    kinds = score.corpus_kinds([_chunk("law-a__20260827#제1조", trust_level="law")])
    assert kinds == {"law-a#제1조": "law"}


def test_corpus_kinds_reads_whichever_axis_it_is_given() -> None:
    """축은 고르는 것이다 — `trust_level` 로는 안 보이는 것이 `source_id` 로는 보인다 (RAG-060)."""
    rows = [_chunk("x__20260827#c", trust_level="official", source_id="benefit24-services")]
    assert score.corpus_kinds(rows, "source_id") == {"x#c": "benefit24-services"}


def test_question_kind_joins_every_kind_an_or_group_touches() -> None:
    """`must` 는 요구 목록이고 그 안이 OR 이다. #217 이 FW1 을 *"시행령 조문 OR 해설"* 로
    넓히면서 **한 문항이 두 종류에 걸치는 자리**가 실제로 생겼다 — 어느 한쪽으로 몰아 세면
    그 문항이 어느 칸에서도 정직하지 않다.
    """
    kinds = {"decree#제11조": "law", "easylaw#h2-5": "official"}
    assert score.question_kind([["decree#제11조", "easylaw#h2-5"]], kinds) == "law+official"


def test_question_kind_separates_no_label_from_label_off_corpus() -> None:
    """*"잴 것이 없다"* 와 *"잴 것이 있는데 코퍼스에 없다"* 는 다른 자리다.

    한 칸으로 뭉치면 `expect: abstain`·`refuse` 문항(라벨이 없는 것이 정상)과 라벨이 낡아
    조인이 깨진 문항이 같이 앉는다 — 뒤쪽은 고쳐야 할 것인데 앞쪽에 섞여 안 보인다.
    """
    assert score.question_kind([], {"a": "law"}) == score.NO_MUST
    assert score.question_kind([["없는라벨"]], {"a": "law"}) == score.OFF_CORPUS


def test_slice_rows_sums_back_to_the_totals() -> None:
    """**모든 칸을 더하면 `score_rows` 와 정확히 같다.** 이 표는 지표를 *분해*하는 것이지
    새로 *계산*하는 것이 아니라서, 총계가 한 칸이라도 움직이면 그것은 버그다.

    ⚠ 이것은 **`ckinds` 없이 부른 총계**와의 약속이다. 경계 문항을 빼는 총계와의 약속은
    아래 `test_slice_rows_sums_back_to_the_scored_total` 이 따로 고정한다 (RAG-062).
    """
    rows = [
        _row("[1] 근거입니다", [_dump_hit("law-a#제1조", "must")], cited=["제1조"]),
        _row("[1] 근거입니다", [_dump_hit("guide-b#h1", "must")], cited=[]),
        _row("모르겠습니다", [_dump_hit("law-a#제1조", "-")], cited=["제9조"]),
    ]
    for row, qid in zip(rows, ["A", "B", "C"]):
        row["id"] = qid
    qkinds = {"A": "law", "B": "official", "C": "law"}

    sliced = score.slice_rows(rows, qkinds)
    total = score.score_rows(rows)
    for key in ("n", "cited", "grounded"):
        assert sum(cell[key] for cell in sliced.values()) == total[key]
    assert sliced["law"] == {"n": 2, "cited": 2, "grounded": 1}
    assert sliced["official"] == {"n": 1, "cited": 0, "grounded": 1}


def test_slice_rows_sums_back_to_the_scored_total() -> None:
    """**RAG-062 로 바뀐 불변식** — `(must 없음)` 칸을 뺀 나머지의 합이 총계와 같다.

    예전 불변식(*"모든 칸의 합 == 총계"*)은 `score_rows` 가 경계 문항을 빼면서 더는 참이
    아니다. **경계 칸을 표에서 지워서 옛 불변식을 지키는 길도 있었지만 고르지 않았다** —
    지우면 이 표를 더해도 총계가 안 나오는 이유가 사라져, 분모가 왜 줄었는지를 다음 사람이
    못 읽는다. 그래서 **칸은 남기고 불변식을 다시 썼다.**
    """
    rows = [
        _row("[1] 근거입니다", [_dump_hit("law-a#제1조", "must")], cited=["제1조"]),
        _row("수의사에게 가세요", [_dump_hit("law-a#제1조", "-")], cited=["제10조"]),
    ]
    for row, qid in zip(rows, ["A", "경계"]):
        row["id"] = qid
    ckinds = {"A": score.CITABLE, "경계": score.NO_MUST}

    sliced = score.slice_rows(rows, {"A": "law", "경계": score.NO_MUST})
    total = score.score_rows(rows, ckinds)

    # 경계 문항은 총계에서 빠지고, 뺀 수는 버려지지 않는다.
    assert total["n"] == 2 and total["scored"] == 1 and total["boundary"] == 1
    # 거절문이 "제10조" 를 물고 있어도 `cited` 로 세지 않는다 — lap22 `B6` 이 그 모양이었다.
    assert total["cited"] == 1
    for key in ("cited", "grounded"):
        assert sum(cell[key] for kind, cell in sliced.items()
                   if kind != score.NO_MUST) == total[key]
    assert sum(cell["n"] for kind, cell in sliced.items()
               if kind != score.NO_MUST) == total["scored"]


def test_score_rows_without_ckinds_reproduces_the_old_numbers() -> None:
    """`ckinds` 없이 부르면 **예전 그대로**다.

    소급 대조표가 "옛 표기 19/33 = 새 표기 17/28" 을 말하려면, 옛 수를 그 자리에서 다시
    낼 수 있어야 한다. 그것이 이 인자가 선택인 이유다.
    """
    rows = [
        _row("[1] 근거입니다", [_dump_hit("law-a#제1조", "must")], cited=["제1조"]),
        _row("수의사에게 가세요", [_dump_hit("law-a#제1조", "-")], cited=["제10조"]),
    ]
    for row, qid in zip(rows, ["A", "경계"]):
        row["id"] = qid

    old = score.score_rows(rows)
    assert old["n"] == 2 and old["cited"] == 2 and old["boundary"] == 0
    assert old["scored"] == old["n"]


def test_citable_kind_reads_the_article_from_the_must_anchor() -> None:
    """축은 **골든셋만으로** 정해진다 — 코퍼스도 DB 도 안 본다 (RAG-062).

    `question_kind` 는 청크 행의 `trust_level` 이 있어야 하지만 이쪽은 `must` 라벨의 앵커만
    읽는다. 그래서 `score_rows` 의 "랩 파일만 있으면 돈다"는 약속이 안 깨진다.
    """
    assert score.citable_kind([["law-drf-api-animal-protection-act#제101조③"]]) == score.CITABLE
    assert score.citable_kind([["srt-terms-pet#h2-0"]]) == score.UNCITABLE
    assert score.citable_kind([]) == score.NO_MUST
    # 별표는 조 번호와 같은 자리에서 같은 일을 한다.
    assert score.citable_kind([["law-drf-api-animal-protection-decree#별표 4-2-라"]]) == score.CITABLE
    # OR 그룹 중 하나만 조 번호를 가져도 인용이 성립할 길이 있다 — 골든셋 S5 가 그 모양이다.
    assert score.citable_kind(
        [["ordinance-search-2253349#제4조", "benefit24-services-374000000596#지원내용"]]
    ) == score.CITABLE


def test_slice_rows_keeps_questions_the_goldenset_dropped() -> None:
    """골든셋에서 지워진 옛 문항을 **조용히 빼지 않는다.**

    `grade_expect` 는 그것을 건너뛰지만(채점할 기대가 없어서다) 여기서 건너뛰면 랩마다 다른
    만큼 분모가 줄어드는데 표에는 그 사실이 안 나온다 — 그러면 이 표는 자기가 고치려던 병
    (총계가 무엇을 감추는가)을 그대로 반복한다.
    """
    row = _row("[1] 근거입니다", [_dump_hit("law-a#제1조", "must")], cited=["제1조"])
    row["id"] = "사라진문항"
    sliced = score.slice_rows([row], {"Q3": "law"})
    assert sliced == {score.OFF_GOLDENSET: {"n": 1, "cited": 1, "grounded": 1}}


def test_kind_order_puts_real_kinds_before_the_bracketed_ones() -> None:
    kinds = [score.NO_MUST, "official", score.OFF_CORPUS, "law"]
    assert sorted(kinds, key=score.kind_order) == [
        "law", "official", score.NO_MUST, score.OFF_CORPUS]
