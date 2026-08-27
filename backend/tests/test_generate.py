"""9단계 생성 (RAG-028).

**Gemini 도 DB 도 부르지 않는다.** `answer()` 를 순수하게 만든 이유가 이것이다 — 프롬프트 조립과
검문소④ 산식은 손으로 만든 `Hit` 몇 개로 검증할 수 있어야 하고, 그러려면 네트워크가 필요한
부분이 한 군데(`client`)로 몰려 있어야 한다.

`tests/test_evaluate.py` 가 그랬듯 **여기가 붙잡는 것은 숫자가 아니라 규칙**이다. 특히 `ask()` 가
넘겨받은 자원을 닫지 않는다는 것(RAG-028 ①)은 서버가 두 번째 요청에서 죽느냐 마느냐의 문제다.
"""
from __future__ import annotations

import pytest

from daengs_life.rag.stages import generate, search
from daengs_life.rag.stages.search import Hit


def hit(rank: int, *, citation: str, content: str, chunk_id: str | None = None) -> Hit:
    return Hit(rank=rank, score=1.0 - rank / 100, chunk_id=chunk_id or f"doc__20260820#c{rank}",
               citation=citation, citation_url=None, section=None,
               document_title="동물보호법", content=content, part=None)


HITS = [
    hit(1, citation="동물보호법 제16조", content="소유자등은 … 안전조치를 하여야 한다."),
    hit(2, citation="반려견 목줄 착용",
        content="안전조치를 하지 않은 경우에는 50만원 이하의 과태료가 부과됩니다"
                "(「동물보호법」 제101조제4항제4호)."),
]


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeClient:
    """`client.models.generate_content(model=, contents=)` 만 흉내낸다."""

    def __init__(self, text: str) -> None:
        self.text, self.calls = text, []
        self.models = self

    def generate_content(self, *, model: str, contents: str) -> FakeResponse:
        self.calls.append((model, contents))
        return FakeResponse(self.text)


# ---------------------------------------------------------------- 프롬프트 (RAG-028 ②)
def test_context_carries_the_whole_content() -> None:
    """**본문을 자르지 않는다.**

    검문소③ B 가 만든 상황 때문이다 — easylaw 해설은 `citation` 이 해설 주소인데 본문 안에 조항
    번호가 박혀 있다. 자르면 모델이 그걸 읽고 인용했는지 지어냈는지 구분할 수 없어진다.
    """
    ctx = generate.build_context(HITS)
    for h in HITS:
        assert h.content in ctx


def test_prompt_asks_for_articles_but_never_for_refusal() -> None:
    """조항 인용은 **요구하고**(KPI), 거부는 **부탁하지 않는다**(RAG-029 가 그 자리다).

    1랩의 목적은 근거가 없을 때 모델이 무엇을 하는지 보는 것이라, 방어 문구를 넣으면 관찰하려던
    것을 지운다. 나중에 누가 "모르면 모른다고 하세요" 를 슬쩍 넣으면 여기서 깨진다.
    """
    prompt = generate.build_prompt("목줄 안 하면 과태료 얼마", HITS)
    assert "조항" in prompt and "질문" in prompt
    assert "모르" not in prompt and "없으면" not in prompt


# ---------------------------------------------------------------- 검문소④ 산식
@pytest.mark.parametrize(("text", "expected"), [
    ("동물보호법 제16조 제2항", ["제16조"]),
    ("제101조제4항제4호", ["제101조"]),
    ("제12조의3 을 보라", ["제12조의3"]),
    ("제16조 그리고 다시 제16조", ["제16조"]),        # 중복은 한 번만
    ("조항이 없는 답변", []),
])
def test_cited_articles(text: str, expected: list[str]) -> None:
    """**항·호는 잡지 않는다.** 검색 단위가 조 단위라 항까지 대조하면 실재하는데도 없다고 센다."""
    assert generate.cited_articles(text) == expected


def test_ungrounded_is_what_the_context_does_not_have() -> None:
    """검문소④가 세는 수 그대로 — 답변이 든 조항 중 컨텍스트에 없는 것."""
    text = "제16조 와 제101조 와 제999조 를 근거로 한다"
    assert generate.ungrounded_articles(text, HITS) == ["제999조"]


def test_ungrounded_is_a_subset_of_cited() -> None:
    text = "제16조 와 제999조"
    a = generate.answer("q", HITS, client=FakeClient(text), model="fake")
    assert set(a.ungrounded) <= set(a.cited)
    assert a.grounded == ["제16조"]


def test_citation_inside_the_body_counts_as_grounded() -> None:
    """**검문소③ B 를 산식으로 고정한다.** `제101조` 는 어느 `citation` 에도 없고 easylaw 해설
    *본문* 에만 있다. 그것을 인용한 답변은 지어낸 것이 아니다 — 1랩이 실제로 그렇게 답했다."""
    assert all("제101조" not in h.citation for h in HITS)
    assert generate.ungrounded_articles("제101조에 따라", HITS) == []


# ---------------------------------------------------------------- 수명 규약 (RAG-028 ①)
def test_ask_does_not_close_what_it_was_handed(monkeypatch: pytest.MonkeyPatch) -> None:
    """**넘겨받은 것을 닫지 않는다.** 서버는 lifespan 이 올린 모델과 요청당 커넥션을 넘기는데,
    여기서 닫아 버리면 두 번째 요청이 죽는다. `search(conn=None)` 의 `own` 패턴과 같은 규약이다."""
    closed: list[str] = []

    class Conn:
        def close(self) -> None:
            closed.append("conn")

    monkeypatch.setattr(generate.embed, "encode_query", lambda *a, **k: [0.0])
    monkeypatch.setattr(generate.search, "search", lambda *a, **k: HITS)
    monkeypatch.setattr(generate.embed, "release", lambda: closed.append("release"))

    conn = Conn()
    generate.ask("q", st=object(), conn=conn, client=FakeClient("제16조"), model="fake")
    assert closed == []


def test_ask_passes_the_borrowed_model_instead_of_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    """`st` 를 주면 **모델을 올리지 않는다.** 안 그러면 요청마다 5~7초가 붙는다."""
    loaded: list[str] = []
    monkeypatch.setattr(generate.search, "encode",
                        lambda *a, **k: loaded.append("loaded") or [0.0])
    monkeypatch.setattr(generate.embed, "encode_query", lambda *a, **k: [0.0])
    monkeypatch.setattr(generate.search, "search", lambda *a, **k: HITS)

    generate.ask("q", st=object(), conn=object(), client=FakeClient("x"), model="fake")
    assert loaded == []


def test_ask_is_the_only_place_the_order_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """**조립 순서가 `rag` 것이라는 RAG-028 ③을 고정한다** — 검색이 생성보다 먼저 불린다.

    이 순서가 `app/services/ask.py` 로 옮겨가면 `rag generate`(검문소④)가 서빙과 다른 코드를
    검사하게 된다. 그때 이 테스트가 아니라 검문소가 조용히 무의미해지므로 여기서 못 박는다.
    """
    seq: list[str] = []
    monkeypatch.setattr(generate.embed, "encode_query",
                        lambda *a, **k: seq.append("encode") or [0.0])
    monkeypatch.setattr(generate.search, "search",
                        lambda *a, **k: seq.append("search") or HITS)
    client = FakeClient("제16조")
    generate.ask("q", st=object(), conn=object(), client=client, model="fake")
    assert seq == ["encode", "search"]
    assert len(client.calls) == 1                      # 생성은 그 뒤 한 번


def test_serving_default_k_matches_the_judged_k() -> None:
    """9단계가 컨텍스트에 넣는 수가 검문소③·RAG-024 ②의 판정 k 와 같아야 한다."""
    from daengs_life.app.services import ask as service

    assert service.SERVING_K == search.DEFAULT_K


# ---------------------------------------------------------------- 덤프 (RAG-028 ⑥)
def test_dump_row_keeps_the_logical_address() -> None:
    """**2랩에서 재수집하면 `chunk_id` 가 바뀐다** (수집 날짜가 들어 있다). 논리 주소를 함께
    남기지 않으면 1랩 덤프와 대조가 통째로 깨진다 — RAG-022 ⑥B 와 같은 처리다."""
    a = generate.answer("q", HITS, client=FakeClient("제16조"), model="fake")
    row = generate.dump_rows([("Q1", a, {"doc#c1"}, set())])[0]
    assert [h.logical for h in row.hits] == ["doc#c1", "doc#c2"]
    assert all("20260820" not in h.logical for h in row.hits)


def test_dump_row_carries_the_three_comparison_axes() -> None:
    """비교 축 셋이 다 있어야 2랩이 성립한다 (RAG-028 ⑥ⓐ). `text` 는 축이 아니라 읽을거리다."""
    a = generate.answer("q", HITS, client=FakeClient("제16조 와 제999조"), model="fake")
    row = generate.dump_rows([("Q1", a, set(), set())])[0]
    assert [h.chunk_id for h in row.hits] and row.cited and row.ungrounded == ["제999조"]
