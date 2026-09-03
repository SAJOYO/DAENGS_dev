"""6단계 채점기 테스트 — **지표 정의와 판정 규칙만** 본다 (RAG-024).

`test_embed.py` 와 같은 이유로 가중치를 로드하지 않는다. 그런데 여기는 이유가 하나 더 있다.

**RAG-024 의 결정은 숫자가 아니라 규칙이다.** "탈락선은 `Hit@5`, 2문항 차, 동률이면 기준선" 이
지켜지는지는 실제 벡터가 필요 없고, 오히려 벡터가 있으면 확인이 어려워진다 — 실물 15문항이
어떻게 나오든 규칙은 같아야 하기 때문이다. 그래서 `item_metrics` 와 `judge` 를 데이터 없이
손계산으로 붙잡는다.

**여기서 깨지면 6단계 점수가 아니라 6단계의 규칙이 무너진 것이다.** 특히
`test_recall_cannot_change_the_winner` 는 RAG-024 ③ 의 마지막 문단(참고 지표가 승자 규칙에
들어오면 ① 이 뒷문으로 무너진다)을 코드로 고정한 것이다.
"""
from __future__ import annotations

import pytest

from daengs_life.rag.stages import embed, evaluate


# ---------------------------------------------------------------- 상수 = 결정 (RAG-024 ②③)
def test_judge_k_is_five() -> None:
    """변별력과 운영 k(검문소③ top-5)가 같은 답을 가리켰다 (RAG-024 ②)."""
    assert evaluate.JUDGE_K == 5
    assert evaluate.JUDGE_K in evaluate.KS


def test_table_carries_every_k() -> None:
    """판정은 k=5 하나로 하되 표에는 전부 싣는다 — 보는 것과 판정하는 것을 나눈 것이다."""
    assert evaluate.KS == (1, 3, 5, 10)


def test_cut_gap_is_two_questions() -> None:
    """15분의 1 = 6.7%p 눈금이라 1문항 차이는 문항 하나의 우연이다 (RAG-024 ③)."""
    assert evaluate.CUT_GAP == 2


def test_preference_starts_at_baseline() -> None:
    """① 기준선 우선. 순서가 `embed.MODELS` 등록 순서와 어긋나면 사전 등록이 흔들린다."""
    assert evaluate.BASELINE == "bge-m3"
    assert evaluate.PREFERENCE == tuple(embed.MODELS)


# ---------------------------------------------------------------- 지표 (RAG-024 ③)
def test_hit_is_binary_any_must() -> None:
    """`Hit` 은 필수가 **하나라도** top-k 면 1이다. 이 이진성이 탈락선의 성격 그 자체다."""
    m = evaluate.item_metrics([4, 300, 900])
    assert m["hit"][5] == 1.0          # 4위 하나로 충분하다
    assert m["hit"][3] == 0.0          # top-3 에는 없다
    assert m["hit"][10] == 1.0


def test_recall_counts_how_many() -> None:
    """`Recall` 은 몇 개를 건졌나. 기록만 하지만 계산은 정확해야 한다."""
    m = evaluate.item_metrics([1, 2, 50, 60])
    assert m["recall"][5] == 0.5       # 4개 중 2개
    assert m["recall"][1] == 0.25
    assert m["recall"][10] == 0.5


def test_hit_and_recall_disagree_on_purpose() -> None:
    """**RAG-024 ③ 이 감수한 대가를 고정한다.**

    Q4(로트와일러)처럼 필수 둘이 다 있어야 인용이 성립하는 문항에서, 하나만 걸린 결과와
    둘 다 걸린 결과가 `Hit` 으로는 구분되지 않는다. 이것은 버그가 아니라 결정이고,
    그래서 `Recall` 을 기록으로 남기며 온전함은 8단계 검문소③이 잡는다.
    """
    half = evaluate.item_metrics([2, 400])       # 하나만 top-5
    full = evaluate.item_metrics([2, 3])         # 둘 다 top-5
    assert half["hit"][5] == full["hit"][5] == 1.0
    assert half["recall"][5] < full["recall"][5]


def test_rr_is_first_must() -> None:
    """표준 MRR — 첫 정답의 역순위. 나머지를 얼마나 건졌나는 Recall 이 본다 (역할 분담)."""
    assert evaluate.item_metrics([3, 1, 7])["rr"] == 1.0
    assert evaluate.item_metrics([4])["rr"] == 0.25


def test_missing_must_scores_zero_not_crash() -> None:
    """골든셋 검증이 먼저 막지만, 0으로 나누지는 않는다."""
    m = evaluate.item_metrics([])
    assert m["hit"][5] == 0.0 and m["recall"][5] == 0.0 and m["rr"] == 0.0


def test_macro_is_question_even() -> None:
    """문항 균등 (RAG-024 ③).

    micro(43쌍 통짜)를 쓰지 않는 이유는 easylaw 가 31/43 = 72% 를 지배하기 때문이고, 그 라벨이
    많은 건 난이도가 아니라 **법제처 목록 차용**(RAG-022 ②)이라 가중치로 바꾸면 안 된다.
    여기서는 필수 1개짜리와 5개짜리가 **같은 무게**인지만 확인한다.
    """
    one = evaluate.item_metrics([1])                    # 필수 1개, 다 맞음
    five = evaluate.item_metrics([1, 2, 3, 900, 901])   # 필수 5개, 3개만
    per_item = [{"hit": one["hit"], "recall": one["recall"]},
                {"hit": five["hit"], "recall": five["recall"]}]
    assert evaluate.macro(per_item, "hit", 5) == 1.0                  # 둘 다 hit
    assert evaluate.macro(per_item, "recall", 5) == pytest.approx(0.8)  # (1.0 + 0.6) / 2


# ---------------------------------------------------------------- 판정 (RAG-024 ①③)
def _hits(bge: int, kure: int, qwen: int) -> dict[str, int]:
    return dict(zip(embed.MODELS, (bge, kure, qwen)))


def test_two_question_gap_eliminates() -> None:
    """정확히 2문항 차이면 탈락이다 (경계는 포함). 1문항은 아래 테스트가 본다."""
    v = evaluate.judge(_hits(bge=14, kure=12, qwen=14))
    assert v.eliminated == ["kure-v1"]                  # 14-12=2 -> 탈락
    assert v.survivors == ["bge-m3", "qwen3-embedding-0.6b"]
    assert v.winner == "bge-m3"                         # 남은 둘의 점수차는 읽지 않는다


def test_one_question_gap_is_noise() -> None:
    """1문항 차이는 읽지 않는다 — 그래서 기준선이 이긴다. **① 의 핵심 줄이다.**"""
    v = evaluate.judge(_hits(bge=13, kure=14, qwen=13))
    assert v.eliminated == []
    assert v.winner == "bge-m3"
    assert "기준선" in v.rule


def test_all_tied_goes_to_baseline() -> None:
    """포화(셋 다 15/15)도 결과다 — 셋 중 뭘 써도 15문항에서는 같다는 뜻이다 (RAG-024 ③).

    여기서 k 를 낮춰 다시 보지 **않는다**. 그 사다리는 "갈릴 때까지 기준을 바꾼다"와 구분되지 않는다.
    """
    v = evaluate.judge(_hits(15, 15, 15))
    assert v.winner == "bge-m3" and v.eliminated == []


def test_only_one_survivor_says_so() -> None:
    """탈락으로 갈렸을 때와 동률일 때를 요약이 구분해야 한다 (RAG-024 ④ 필수 3항)."""
    v = evaluate.judge(_hits(bge=5, kure=15, qwen=4))
    assert v.winner == "kure-v1" and v.survivors == ["kure-v1"]
    assert "탈락으로 갈렸다" in v.rule


def test_baseline_eliminated_is_flagged() -> None:
    """**RAG-024 ① 이 말하지 않은 자리** — 기준선이 탈락하고 둘이 남는 경우.

    사전 순위로 집되 그 사실을 요약이 드러내야 한다. 결과를 보고 규칙을 정하는 자리가 생기면
    사전 등록이 무너지므로, 일어나지 않을 것 같은 경우에도 규칙이 먼저 있어야 한다.
    """
    v = evaluate.judge(_hits(bge=10, kure=15, qwen=14))
    assert v.eliminated == ["bge-m3"]
    assert v.winner == "kure-v1"
    assert "명시하지 않은" in v.rule


def test_recall_cannot_change_the_winner() -> None:
    """**`judge` 는 Hit 문항 수만 받는다.** Recall·MRR 을 넘길 자리가 없다 (RAG-024 ③).

    이 서명이 지켜지는 한 "Hit 은 동률인데 Recall 이 높으니 이쪽" 이 코드에 들어올 수 없다.
    """
    import inspect

    sig = inspect.signature(evaluate.judge)
    assert list(sig.parameters) == ["hits"]


# ---------------------------------------------------------------- 산출물 (RAG-024 ④)
def test_dump_goes_to_untracked_processed() -> None:
    """덤프는 `data/processed/eval/` 이다 — `.gitignore` 가 덮는 자리.

    `data/eval/` 은 **추적되는** 폴더라 여기 쓰면 450행이 커밋된다. `data/README.md` 의 예외는
    "손으로 만드는 것"이지 기계 산출물이 아니다 (RAG-024 ④).
    """
    from daengs_life.rag.core import config, io

    if config.EVAL_DIR is None:
        pytest.skip("DAENGS_DATA_DIR 이 없다")
    path = io.eval_path("bge-m3")
    assert path.parent.name == "eval"
    assert path.parent.parent.name == "processed"


def test_summary_carries_the_four_required_things() -> None:
    """요약 4항 (RAG-024 ④): 점수표 · `Hit@5=0` 문항 id · ①의 어느 줄 · 코퍼스 스냅샷.

    덤프가 미추적이라 이것이 뒤에 남는 전부다. 하나라도 빠지면 "왜 이 모델인가"에 못 답한다.
    """
    from daengs_life.rag.stages import goldenset

    gs = goldenset.load()
    summaries = {
        "bge-m3": {"hit": {k: 0.8 for k in evaluate.KS}, "recall": {k: 0.5 for k in evaluate.KS},
                   "mrr": 0.7, "hit_count": 12, "zero_hit": ["Q7", "QA6"]},
        "kure-v1": {"hit": {k: 0.8 for k in evaluate.KS}, "recall": {k: 0.5 for k in evaluate.KS},
                    "mrr": 0.7, "hit_count": 13, "zero_hit": ["Q7"]},
        "qwen3-embedding-0.6b": {"hit": {k: 0.5 for k in evaluate.KS},
                                 "recall": {k: 0.3 for k in evaluate.KS},
                                 "mrr": 0.4, "hit_count": 9, "zero_hit": ["Q1", "Q7"]},
    }
    v = evaluate.judge({k: s["hit_count"] for k, s in summaries.items()})
    md = evaluate.markdown(summaries, v, gs, "deadbeefdeadbeef00", 1407)

    assert "Hit@5" in md and "Recall@5" in md and "MRR" in md      # ① 점수표
    assert "Q7" in md and "QA6" in md                              # ② Hit@5=0 문항
    assert v.rule in md                                            # ③ 어느 줄이 적용됐나
    assert str(gs.corpus.collected_on) in md and "1407" in md      # ④ 코퍼스 스냅샷
    assert "승자" in md


# ---------------------------------------------------------------- 요구와 대안 (RAG-055)
def _item(must) -> object:
    from daengs_life.rag.stages import goldenset

    return goldenset.Item(id="X1", added_on="2026-09-03", origin="hand",
                          question="질문", must=must)


def test_a_requirement_takes_the_rank_of_its_best_alternative() -> None:
    """**OR 의 정의다** — 어느 대안으로도 답이 성립하므로, 제일 높이 올라온 것이 그 요구가
    실제로 닿은 거리다. 최악이나 평균을 쓰면 대안을 적어 둘수록 문항이 나빠진다.
    """
    index = {"a": "a__1", "b": "b__1"}
    id_to_row = {"a__1": 0, "b__1": 1}
    ranks = [7, 2]

    rows = evaluate.requirement_rows(_item([["a", "b"]]), index, id_to_row, ranks)
    assert len(rows) == 1
    assert rows[0]["rank"] == 2
    assert [a["rank"] for a in rows[0]["alternatives"]] == [7, 2]


def test_alternatives_do_not_change_the_recall_denominator() -> None:
    """**대안을 늘려도 `item_metrics` 가 받는 목록의 길이가 같아야 한다** (RAG-055).

    이것이 무너지면 라벨에 해설을 하나 더 적는 것만으로 그 문항의 Recall 이 떨어지고,
    `lap1`~`lap14` 와의 비교가 조용히 끊긴다 — RAG-049 ④ 가 스키마를 안 고친 이유가 그것이다.
    """
    index = {"a": "a__1", "b": "b__1", "c": "c__1"}
    id_to_row = {"a__1": 0, "b__1": 1, "c__1": 2}
    ranks = [3, 1, 9]

    one = evaluate.requirement_rows(_item(["a"]), index, id_to_row, ranks)
    many = evaluate.requirement_rows(_item([["a", "b", "c"]]), index, id_to_row, ranks)
    assert len(one) == len(many) == 1

    two_requirements = evaluate.requirement_rows(_item(["a", "b"]), index, id_to_row, ranks)
    assert len(two_requirements) == 2
    assert evaluate.item_metrics([r["rank"] for r in many])["recall"][5] == 1.0
    assert evaluate.item_metrics([r["rank"] for r in two_requirements])["recall"][5] == 1.0


def test_a_requirement_whose_alternatives_are_all_missing_drops_out() -> None:
    """코퍼스 밖이거나 벡터가 아직 없는 요구는 `None` 이고 분모에서 빠진다.

    **0위로 세지 않는다** — 없는 것을 "못 찾았다"로 세면 코퍼스를 안 늘린 것이 검색 성능
    저하로 보인다. `unavailable` 을 분모에서 뺀 RAG-022 ③ 과 같은 자리다.
    """
    rows = evaluate.requirement_rows(_item([["없는주소"], ["a"]]), {"a": "a__1"}, {"a__1": 0}, [4])
    assert [r["rank"] for r in rows] == [None, 4]
    assert evaluate.item_metrics([r["rank"] for r in rows if r["rank"] is not None])["rr"] == 0.25


# ---------------------------------------------------------------- 실물 (있으면)
def test_labels_resolve_to_rows() -> None:
    """골든셋 라벨이 전부 parquet 의 행으로 풀리는가.

    `goldenset` 서브커맨드는 라벨이 **청크로** 실재하는지만 본다. 채점은 거기서 한 단계 더 가서
    **벡터 행**을 찾아야 하고, 그 사이에서 어긋나면 그 문항만 조용히 0 점이 된다.

    ⚠ **벡터가 청크보다 낡으면 이 테스트는 아무것도 못 가른다.** parquet 은 `rag embed` 를
    돌린 시점의 청크를 담고 있어서, 그 뒤에 소스가 붙거나 재청킹이 되면(주소가 바뀐다)
    멀쩡한 라벨도 "행이 없다"로 나온다 — 실제로 2026-09-03 에 parquet 6,368행 / 청크 9,451개
    상태에서 항공 라벨과 삼성 약관 라벨이 그렇게 걸렸다. 그건 라벨 문제가 아니라 **재임베딩이
    안 된 것**이라, 두 수가 다르면 여기서 멈춘다. 라벨 실재 여부는 `test_every_label_exists`
    가 청크를 보고 계속 지킨다.
    """
    from daengs_life.rag.stages import goldenset

    ids, matrix = evaluate.load_matrix("bge-m3")
    if ids is None:
        pytest.skip("bge-m3.parquet 이 없다 — `python -m rag embed` 먼저")
    gs = goldenset.load()
    index = goldenset.corpus_index()
    if len(ids) != len(index):
        pytest.skip(f"벡터가 청크보다 낡았다 — parquet {len(ids)}행 / 청크 {len(index)}개. "
                    "`python -m rag embed` 로 다시 만든 뒤에야 라벨을 잴 수 있다")
    rows = {cid: i for i, cid in enumerate(ids)}
    # `must_flat` 이다 (RAG-055) — 요구의 경계는 여기서 상관없고, 대안이든 필수든
    # 벡터 행이 없으면 똑같이 그 문항을 조용히 0 점으로 만든다
    missing = [a for item in gs.items for a in item.must_flat
               if a not in index or index[a] not in rows]
    assert missing == [], f"벡터 행을 못 찾는 필수 라벨: {missing}"
