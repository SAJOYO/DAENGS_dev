"""골든셋 검증 — `backend/rag/stages/goldenset.yaml` 과 `data/processed/chunks/` 를 맞춰 본다 (RAG-022).

**검문소①(`test_chunk.py`)과 같은 역할을 5단계에서 한다.** 검문소①은 "정답 청크가 코퍼스에
존재하나"를 물었고, 여기는 "골든셋이 가리키는 주소가 실재하나"를 묻는다. 둘은 다르다 —
RAG-022 ⑤ 가 실제로 두 군데(질문 3 의 과태료 조항, 질문 6 의 조문 수)에서 어긋난 것을 찾아냈다.

없는 주소를 가리키는 `must` 는 그 문항의 Recall 을 영원히 0 으로 만들고, 증상은 6단계에서
"이 모델이 유독 못한다" 로만 나타난다. 그러면 라벨 오타가 승자를 정하게 된다.

`goldenset.yaml` 은 git 추적이지만 `data/` 는 미추적이라(RAG-017) 다른 PC 에는 청크가 없다.
청크가 필요한 테스트만 skip 하고, YAML 자체를 보는 테스트는 어디서나 돈다.
"""
from __future__ import annotations

import collections

import pytest
from pydantic import ValidationError

from daengs_life.rag.core import io
from daengs_life.rag.stages import goldenset

# 2026-08-24 실측. 라벨을 고치면 여기서 먼저 깨지도록 박아 둔다.
# 2026-08-27 갱신 — 지자체 지원 3문항(S1~S3) 추가 (RAG-033 ⑥). hand 7 → 10, 필수 43 → 46.
# 2026-08-28 갱신 — 보조금24 2문항(S4·S5) 추가 (RAG-034). hand 10 → 12, 필수 46 → 49.
# 2026-08-28 갱신 — 운송 3문항(T1~T3) 추가 (RAG-036). hand 12 → 15, 필수 49 → 53.
# T1 만 필수가 둘인 것은 **서울교통공사가 1~8호선·9호선 2·3단계 두 약관**이기 때문이다.
# 2026-08-30 갱신 — 펫보험 5문항(I1~I5) + 항공 2문항(T4·T5) 추가 (RAG-049). hand 15 → 22, 필수 53 → 62.
# 필수가 둘인 것은 T4·T5 — T1 과 같은 이유로 **두 항공사의 수치가 다르다**. 보험 문항은 전부 하나다:
# 질문이 "펫보험" 일반이라 한 회사의 행으로 답이 성립한다 (RAG-049 ②).
# 2026-09-03 갱신 — 경계 6문항(B1~B6) 추가 + `must` OR (RAG-055). hand 22 -> 28, 문항 30 -> 36.
# **필수는 62 -> 64 만 늘었다** — B1 이 요구 둘을 갖고, 나머지 다섯은 정답 근거가 없는 문항이다.
# Q2·T1·I1·I5 에 더한 대안 넷은 **요구를 안 늘린다**. 그것이 OR 을 넣은 이유이고 아래에서 따로 붙잡는다.
ITEMS = 38
MUST_TOTAL = 67          # 요구의 수
MUST_ADDRESSES = 75      # 주소의 수 — 대안을 늘리면 이쪽만 늘어야 한다
BY_ORIGIN = {"hand": 30, "easylaw": 8}
BY_EXPECT = {"answer": 33, "abstain": 2, "refuse": 3}


@pytest.fixture(scope="module")
def gs() -> goldenset.GoldenSet:
    return goldenset.load()


@pytest.fixture(scope="module")
def index() -> dict[str, str]:
    """현재 청크의 {논리 주소: 실제 chunk_id}."""
    if not io.chunk_files():
        pytest.skip("data/processed/chunks 가 비었다 — `python -m rag chunk` 먼저")
    return goldenset.corpus_index()


# ---------------------------------------------------------------- YAML 자체 (청크 불필요)
def test_shape(gs: goldenset.GoldenSet) -> None:
    assert len(gs.items) == ITEMS
    assert gs.must_total == MUST_TOTAL
    assert sum(len(i.must_flat) for i in gs.items) == MUST_ADDRESSES
    assert collections.Counter(i.origin for i in gs.items) == BY_ORIGIN
    assert collections.Counter(i.expect for i in gs.items) == BY_EXPECT


def test_item_ids_unique(gs: goldenset.GoldenSet) -> None:
    """문항 id 는 6단계 점수표의 행 이름이다. 겹치면 한 문항이 다른 문항을 덮는다."""
    dup = [k for k, v in collections.Counter(i.id for i in gs.items).items() if v > 1]
    assert not dup, f"문항 id 중복: {dup}"


def test_every_answer_item_has_a_must(gs: goldenset.GoldenSet) -> None:
    """`must` 가 빈 **답변 문항**은 Recall 이 0/0 이라 채점이 정의되지 않는다.

    질문 7 이 이 자리에 걸릴 뻔했다 — 정답이 미수집 시행령에만 있다고 봤으나, easylaw ※박스가
    답과 조항 인용을 함께 담고 있어 그것이 필수가 됐다 (RAG-022 ⑤).

    **기권·거절 문항은 반대로 비어 있어야 한다** (RAG-055). 둘 다 스키마가 이미 막지만,
    막는 쪽이 조용히 풀려도 여기서 잡히게 양쪽을 다 센다.
    """
    empty = [i.id for i in gs.scored_items if not i.must]
    assert not empty, f"필수 라벨이 없는 답변 문항: {empty}"

    labelled = [i.id for i in gs.items if i.expect != "answer" and (i.must or i.nice)]
    assert not labelled, f"기권·거절인데 정답 근거를 가진 문항: {labelled}"


def test_labels_carry_no_collection_date(gs: goldenset.GoldenSet) -> None:
    """라벨은 **날짜를 뺀 논리 주소**여야 한다 (RAG-022 ⑥B).

    `chunk_id` 에는 수집일이 박혀 있고(`crawler/core/store.py`), `data/` 가 미추적이라
    새 PC 에서는 재수집이 정상 경로다. 날짜가 붙은 라벨은 그 순간 전부 깨진다.
    """
    dated = [a for _, _, a in gs.labels() if goldenset.logical(a) != a]
    assert not dated, f"수집 날짜가 붙은 라벨: {dated}"


def test_easylaw_items_do_not_label_their_own_qa_chunk(gs: goldenset.GoldenSet) -> None:
    """RAG-022 ② 의 핵심이 무너지지 않았는지 본다.

    qa 청크는 자기 질문을 첫 줄에 그대로 담고 있어 세 모델이 전부 1위로 찾는다. 그것이 `must` 로
    올라가면 그 문항은 아무것도 가르지 못한다. `nice` 로만 있어야 한다.
    """
    for item in gs.items:
        if item.origin != "easylaw":
            continue
        assert not [a for a in item.must_flat if "-qna#" in a], f"{item.id}: qa 청크가 must 에 있다"
        assert [a for a in item.nice if "-qna#" in a], f"{item.id}: 자기 qa 청크가 nice 에 없다"


def test_unavailable_is_recorded_not_dropped(gs: goldenset.GoldenSet) -> None:
    """코퍼스 밖 참조는 지우지 않고 남긴다 — 그 법령을 수집하면 `must` 로 올라가야 한다 (RAG-022 ③).

    부분 보유 2문항(유기견 신고 · 병원 사체처리)과 질문 7 이 여기 걸린다.
    T1·T3 이 더해졌다 — 지역 도시철도공사와 코레일이 아직 코퍼스 밖이다 (RAG-036).
    I1(DB손보 약관 — RAG-048 ① 이 뺐다)·T4·T5(대형 항공사 — RAG-046) 가 더해졌다 (RAG-049).

    B3 은 **분모 제외만 있고 must 가 없는 첫 문항**이다 — 가입 나이 상한이 코퍼스 밖이라
    물러서는 것이 정답이고, 그 사실을 적어 두는 자리가 `unavailable` 이다 (RAG-055).
    """
    have = {i.id for i in gs.items if i.unavailable}
    assert have == {"Q7", "QA6", "QA8", "T1", "T3", "I1", "T4", "T5", "B3", "DP1"}, \
        f"분모 제외를 가진 문항이 달라졌다: {sorted(have)}"


# ---------------------------------------------------------------- 스키마 자체 (RAG-055)
# 위 테스트들이 "이 골든셋이 옳은가"를 본다면, 아래는 **스키마가 무엇을 막는가**를 본다.
# 데이터 없이 도는 것이 의도다 — 라벨을 고치다 이 규칙을 함께 무너뜨리면 여기서 먼저 깨진다.

def _item(**over) -> dict:
    base = {"id": "X1", "added_on": "2026-09-03", "origin": "hand",
            "question": "질문", "must": ["a#1"]}
    return base | over


def _load(items: list[dict]) -> goldenset.GoldenSet:
    return goldenset.GoldenSet.model_validate({
        "schema_version": 2,
        "corpus": {"collected_on": "2026-08-30", "chunker_version": 1, "chunk_count": 1},
        "items": items,
    })


def test_a_bare_string_is_a_one_alternative_requirement() -> None:
    """**옛 라벨이 그대로 읽혀야 한다.** 23문항이 전부 문자열 목록이라, 이 한 줄이 없으면
    스키마를 바꾸는 순간 골든셋을 통째로 다시 써야 한다 (RAG-055).
    """
    item = _load([_item(must=["a#1", ["b#1", "c#1"]])]).items[0]
    assert item.must == [["a#1"], ["b#1", "c#1"]]
    assert item.must_flat == ["a#1", "b#1", "c#1"]


def test_alternatives_do_not_grow_the_requirement_count() -> None:
    """**OR 을 넣은 이유 그 자체다.** 대안을 늘려도 Recall 분모가 안 커져야 `lap1`~`lap14` 와
    옛 6단계 덤프가 그대로 비교된다. 이것이 무너지면 "고쳤더니 점수가 내려갔다"가 라벨 때문인지
    검색 때문인지 영원히 안 갈린다.
    """
    one = _load([_item(must=["a#1"])])
    two = _load([_item(must=[["a#1", "b#1"]])])
    assert one.must_total == two.must_total == 1
    assert len(one.items[0].must_flat) == 1
    assert len(two.items[0].must_flat) == 2


def test_must_flat_drops_a_repeated_alternative() -> None:
    """T1 처럼 **한 해설이 요구 둘을 다 대신하는** 모양에서 같은 주소가 두 요구에 나온다.
    검증·`tier_of` 는 그것을 한 번만 봐야 한다 — 두 번 보면 없는 라벨이 둘로 세어진다.
    """
    item = _load([_item(must=[["a#1", "해설#1"], ["b#1", "해설#1"]])]).items[0]
    assert item.must_flat == ["a#1", "해설#1", "b#1"]


@pytest.mark.parametrize("over, why", [
    ({"expect": "refuse"}, "refuse 인데 코드가 없다"),
    ({"expect": "abstain", "refusal_code": "medical_boundary"}, "abstain 인데 코드가 있다"),
    ({"expect": "answer", "must": []}, "answer 인데 must 가 없다"),
    ({"expect": "abstain", "must": ["a#1"]}, "abstain 인데 must 가 있다"),
    ({"expect": "abstain", "must": [], "nice": ["a#1"]}, "abstain 인데 nice 가 있다"),
    ({"must": [[]]}, "대안이 하나도 없는 빈 요구"),
    ({"must": [["a#1", "a#1"]]}, "한 요구 안에 같은 주소가 둘"),
    ({"expect": "handoff"}, "능력이 낼 수 없는 기대 — contracts 에 필드가 없다"),
])
def test_schema_rejects_incoherent_items(over: dict, why: str) -> None:
    """**모양이 어긋난 문항은 로드에서 죽는다** (RAG-055).

    조용히 통과하면 증상이 전부 "모델이 못한다"로만 나타난다 — 기권 문항에 `must` 가 있으면
    그것을 인용한 답변이 옳아 보이고, 답변 문항에 `must` 가 없으면 영원히 0 점이다.
    """
    with pytest.raises(ValidationError):
        _load([_item(**over)])


def test_scored_items_exclude_the_boundary_questions(gs: goldenset.GoldenSet) -> None:
    """6단계 3파전은 `expect: answer` 만 돈다 (RAG-055).

    기권·거절 문항은 정답 청크가 없어 지표가 전부 0 이고, 문항 균등 평균(RAG-024 ③)에 넣으면
    세 모델의 점수가 나란히 내려가면서 **차이만 희석된다.** `unavailable` 을 분모에서 뺀
    RAG-022 ③ 과 같은 이유다.
    """
    scored = {i.id for i in gs.scored_items}
    assert scored == {i.id for i in gs.items if i.expect == "answer"}
    assert not scored & {"B2", "B3", "B4", "B5", "B6"}
    assert "B1" in scored, "B1 은 답해야 하는 문항이다 — 기권 신호가 과하게 켜지는지 재는 자다"


def test_refusal_codes_are_the_two_the_adapter_emits(gs: goldenset.GoldenSet) -> None:
    """`refusal_code` 는 계약이다 — 어댑터가 `refusal.code` 로 그대로 내보낸다.

    여기서 이름을 지어내면 앱까지 따라가므로, 로드맵 §2 가 정한 둘 말고는 못 쓰게 막는다.
    """
    codes = {i.refusal_code for i in gs.items if i.expect == "refuse"}
    assert codes == {"medical_boundary", "emergency_boundary"}


# ---------------------------------------------------------------- 코퍼스와 대조 (청크 필요)
def test_every_label_exists(gs: goldenset.GoldenSet, index: dict) -> None:
    """**이 파일의 본체.** 43개 필수 + 15개 보강이 전부 실재하는 청크를 가리켜야 한다."""
    problems, _ = goldenset.verify(gs, index)
    assert not problems, "실재하지 않는 청크를 가리키는 라벨:\n" + "\n".join(
        f"  {p.item_id} {p.tier} {p.address}" for p in problems)


def test_snapshot_matches(gs: goldenset.GoldenSet, index: dict) -> None:
    """스냅샷이 어긋나면 경고가 뜬다. 지금 이 PC 에서는 뜨지 않아야 한다.

    다른 PC 에서 재수집하면 이 테스트가 깨지는데, 그것이 의도다 — 조문 번호는 그대로여도
    개정으로 내용이 바뀌었을 수 있으니 라벨을 눈으로 다시 보라는 신호다.
    """
    _, warnings = goldenset.verify(gs, index)
    assert not warnings, "\n".join(warnings)


def test_must_chunks_are_not_empty(gs: goldenset.GoldenSet, index: dict) -> None:
    """빈 청크를 가리키는 라벨은 실재하지만 쓸모가 없다. 파서·청커 회귀를 여기서도 잡는다."""
    by_id = {}
    for path in io.chunk_files():
        for row in io.read_chunks(path):
            by_id[goldenset.logical(row["chunk_id"])] = row
    thin = [(i.id, a) for i in gs.items for a in i.must_flat
            if len(by_id[a]["content"].strip()) < 30]
    assert not thin, f"본문이 사실상 빈 필수 청크: {thin}"


def test_q4_label_covers_the_confirmed_breed_list(gs: goldenset.GoldenSet, index: dict) -> None:
    """질문 4 는 법 조문만으로는 인용이 부실하다 — 시행규칙이 함께 있어야 한다 (RAG-022 ⑤).

    법 제2조제5호가목은 "도사견, 핏불테리어, 로트와일러 **등** ... 농림축산식품부령으로 정하는 개"
    라 로트와일러가 예시로만 나온다. 확정 목록은 시행규칙 제2조 제5호다.
    """
    q4 = next(i for i in gs.items if i.id == "Q4")
    assert len(q4.must) == 2          # 요구 둘 — 법 조문과 시행규칙은 서로의 대안이 아니다
    texts = []
    for path in io.chunk_files():
        for row in io.read_chunks(path):
            if goldenset.logical(row["chunk_id"]) in q4.must_flat:
                texts.append(row["content"])
    assert any("로트와일러와 그 잡종의 개" in t for t in texts), "확정 견종 목록이 라벨 밖이다"


def test_q3_label_reaches_the_fine_amount(gs: goldenset.GoldenSet, index: dict) -> None:
    """질문 3 은 "얼마"를 묻는다. 검문소① 라벨에는 과태료 조항이 없었다 (RAG-022 ⑤).

    금액에 닿지 못하는 라벨이면 금액을 못 찾는 모델이 만점을 받는다.
    """
    q3 = next(i for i in gs.items if i.id == "Q3")
    texts = []
    for path in io.chunk_files():
        for row in io.read_chunks(path):
            if goldenset.logical(row["chunk_id"]) in q3.must_flat:
                texts.append(row["content"])
    assert any("50만원 이하의 과태료" in t for t in texts), "필수 라벨이 과태료 금액에 닿지 않는다"
