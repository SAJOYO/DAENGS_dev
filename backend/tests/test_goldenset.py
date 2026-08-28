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

from daengs_life.rag.core import io
from daengs_life.rag.stages import goldenset

# 2026-08-24 실측. 라벨을 고치면 여기서 먼저 깨지도록 박아 둔다.
# 2026-08-27 갱신 — 지자체 지원 3문항(S1~S3) 추가 (RAG-033 ⑥). hand 7 → 10, 필수 43 → 46.
# 2026-08-28 갱신 — 보조금24 2문항(S4·S5) 추가 (RAG-034). hand 10 → 12, 필수 46 → 49.
ITEMS = 20
MUST_TOTAL = 49
BY_ORIGIN = {"hand": 12, "easylaw": 8}


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
    assert collections.Counter(i.origin for i in gs.items) == BY_ORIGIN


def test_item_ids_unique(gs: goldenset.GoldenSet) -> None:
    """문항 id 는 6단계 점수표의 행 이름이다. 겹치면 한 문항이 다른 문항을 덮는다."""
    dup = [k for k, v in collections.Counter(i.id for i in gs.items).items() if v > 1]
    assert not dup, f"문항 id 중복: {dup}"


def test_every_item_has_a_must(gs: goldenset.GoldenSet) -> None:
    """`must` 가 빈 문항은 Recall 이 0/0 이라 채점이 정의되지 않는다.

    질문 7 이 이 자리에 걸릴 뻔했다 — 정답이 미수집 시행령에만 있다고 봤으나, easylaw ※박스가
    답과 조항 인용을 함께 담고 있어 그것이 필수가 됐다 (RAG-022 ⑤).
    """
    empty = [i.id for i in gs.items if not i.must]
    assert not empty, f"필수 라벨이 없는 문항: {empty}"


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
        assert not [a for a in item.must if "-qna#" in a], f"{item.id}: qa 청크가 must 에 있다"
        assert [a for a in item.nice if "-qna#" in a], f"{item.id}: 자기 qa 청크가 nice 에 없다"


def test_unavailable_is_recorded_not_dropped(gs: goldenset.GoldenSet) -> None:
    """코퍼스 밖 참조는 지우지 않고 남긴다 — 그 법령을 수집하면 `must` 로 올라가야 한다 (RAG-022 ③).

    부분 보유 2문항(유기견 신고 · 병원 사체처리)과 질문 7 이 여기 걸린다.
    """
    have = {i.id for i in gs.items if i.unavailable}
    assert have == {"Q7", "QA6", "QA8"}, f"분모 제외를 가진 문항이 달라졌다: {sorted(have)}"


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
    thin = [(i.id, a) for i in gs.items for a in i.must
            if len(by_id[a]["content"].strip()) < 30]
    assert not thin, f"본문이 사실상 빈 필수 청크: {thin}"


def test_q4_label_covers_the_confirmed_breed_list(gs: goldenset.GoldenSet, index: dict) -> None:
    """질문 4 는 법 조문만으로는 인용이 부실하다 — 시행규칙이 함께 있어야 한다 (RAG-022 ⑤).

    법 제2조제5호가목은 "도사견, 핏불테리어, 로트와일러 **등** ... 농림축산식품부령으로 정하는 개"
    라 로트와일러가 예시로만 나온다. 확정 목록은 시행규칙 제2조 제5호다.
    """
    q4 = next(i for i in gs.items if i.id == "Q4")
    assert len(q4.must) == 2
    texts = []
    for path in io.chunk_files():
        for row in io.read_chunks(path):
            if goldenset.logical(row["chunk_id"]) in q4.must:
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
            if goldenset.logical(row["chunk_id"]) in q3.must:
                texts.append(row["content"])
    assert any("50만원 이하의 과태료" in t for t in texts), "필수 라벨이 과태료 금액에 닿지 않는다"
