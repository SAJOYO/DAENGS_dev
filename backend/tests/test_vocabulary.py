"""질의 어휘 다리 (RAG-066 / D11 · #270).

**오탐이 조용한 것**이 이 모듈의 위험이다 — 엉뚱한 낱말이 붙은 질의는 예외가 안 나고 그냥
답이 나빠질 뿐이다. 그래서 여기서 잡는 것은 셋이다:

  ① 별칭이 실제로 붙는가 (그리고 신호가 없으면 **한 글자도** 안 바뀌는가)
  ② 부분 문자열 함정 — `region` 이 `고양이`/`고양시` 로 배운 것
  ③ **확장이 한 입구로만 들어가는가** — 입구가 둘이 되는 순간 한쪽만 걸린다

네트워크도 DB 도 `data/` 도 쓰지 않는다.
"""
from __future__ import annotations

import inspect

import pytest

from daengs_life.rag.core import vocabulary
from daengs_life.rag.stages import search

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ------------------------------------------------------------------ ① 붙는가 / 안 붙는가

def test_the_housing_question_gets_the_statute_words() -> None:
    """`HS1` — 이 카드를 연 문항. 정찰에서 없음 → 1위."""
    added = vocabulary.aliases("아파트에서 반려견을 키우려면 관리사무소 동의를 받아야 하나요?")
    assert set(added) >= {"공동주택", "관리주체", "가축"}


def test_the_funeral_question_gets_the_statute_words() -> None:
    """`FW3` — RAG-059 가 "조례가 top-5 를 차지한다"로 적었던 실패. **조례가 이긴 이유**가
    사람은 `화장장`, 법은 `장묘시설` 이라 쓰는 것이었다.
    """
    assert "동물장묘시설" in vocabulary.aliases("우리 동네에 동물 화장장이 들어온다는데 그게 가능한가요?")


def test_a_query_without_a_signal_is_untouched() -> None:
    """**신호가 없으면 한 글자도 안 바꾼다.** `region` 규칙 4 와 같다 —
    지역을 안 밝힌 질의가 전국을 봐야 하듯, 일상어가 없는 질의는 원문 그대로 인코딩된다.
    """
    q = "강아지 등록 안 하면 어떻게 되나요?"
    assert vocabulary.aliases(q) == ()
    assert vocabulary.expand(q) == q


def test_a_word_already_in_the_query_is_not_added_twice() -> None:
    """같은 낱말을 두 번 넣으면 렉시컬 축의 가중치만 흔들리고 얻는 것이 없다."""
    added = vocabulary.aliases("공동주택에서 아파트 주민이 가축을 사육할 수 있나요?")
    assert "공동주택" not in added
    assert "가축" not in added


def test_expand_keeps_the_original_text_in_front() -> None:
    out = vocabulary.expand("아파트에서 키워도 되나요?")
    assert out.startswith("아파트에서 키워도 되나요?")
    assert "공동주택" in out


# ------------------------------------------------------------------ ② 부분 문자열 함정

def test_the_cat_does_not_trigger_the_cremation_alias() -> None:
    """`화장` 이 트리거인데 **`화장품`·`화장실` 에 붙으면 안 된다.**

    `region` 이 `고양이`/`고양시` 로 배운 그 자리다 (RAG-063). 형태소 토큰과의 **정확 일치**라
    부분 문자열로는 안 걸린다.
    """
    for q in ("강아지가 화장실에서 실수를 해요", "사람 화장품을 강아지가 핥았어요"):
        assert "동물장묘시설" not in vocabulary.aliases(q), q


def test_pet_becomes_livestock_only_in_a_housing_question() -> None:
    """**이 카드에서 가장 위험한 한 줄이다.**

    `반려동물` → `가축` 은 공동주택관리법 시행령 제19조제2항제4호가 반려동물을 `가축` 이라
    부르기 때문에 필요한데, 조건 없이 걸면 **골든셋 46문항 중 18개가 확장됐다**(2026-09-06
    실측) — 조례 지원금·기차·해설까지 `가축` 이 붙는다. 코퍼스에 가축전염병예방법이 통째로
    들어 있어 검색이 그쪽으로 끌려간다. 맥락을 걸어 18 → 5 로 줄였다.
    """
    assert "가축" in vocabulary.aliases("아파트에서 반려동물을 키워도 되나요?")
    for q in ("반려동물 진료비를 지원받을 수 있나요?",
              "기차에 반려동물은 몇 kg까지 태울 수 있나요?",
              "반려견이 죽었는데 동물등록 관련해서 해야 할 일이 있나요?"):
        assert "가축" not in vocabulary.aliases(q), q


def test_a_context_gated_trigger_needs_its_context() -> None:
    """조건 목록이 비어 있으면 무조건 걸리고, 있으면 그중 하나가 같이 있어야 한다."""
    gated = [t for t, a in vocabulary.ALIASES.items() if a.requires]
    assert gated, "맥락 조건이 하나도 없다 — `가축` 이 다시 무조건 걸리고 있다"
    for trigger in gated:
        assert not vocabulary.aliases(f"{trigger} 관련해서 궁금한 게 있어요"), trigger


def test_every_trigger_is_a_single_morpheme_token() -> None:
    """트리거가 형태소로 쪼개지면 **영영 안 걸린다** — 사전에 적어 두고도 죽은 줄이 된다.

    `tokenize.tokens` 가 복합어를 되붙이므로 대부분 통과하지만, 새 트리거를 넣을 때
    이 테스트가 조용한 실패를 막는다.
    """
    from daengs_life.rag.core import tokenize
    dead = [t for t in vocabulary.ALIASES if t not in set(tokenize.tokens(f"{t} 관련 질문입니다"))]
    assert not dead, f"형태소로 안 잡히는 트리거: {dead}"


def test_no_alias_points_back_at_its_own_trigger() -> None:
    """자기 자신을 더하면 무한히 같은 낱말이 붙는다."""
    bad = [(t, w) for t, a in vocabulary.ALIASES.items() for w in a.add if w == t]
    assert not bad


# ------------------------------------------------------------------ ③ 입구가 하나인가

def test_encode_takes_a_loaded_model() -> None:
    """`st` 를 못 받으면 모델 재사용 경로가 이 함수를 **건너뛰고** 확장이 한쪽만 걸린다.
    그것이 `transport`·`region` 이 *"인자로 빼면 CLI·9단계·FastAPI 가 각자 켜고 끈다"* 며
    막아 온 모양이다 (RAG-052 · RAG-063).
    """
    assert "st" in inspect.signature(search.encode).parameters


def test_nobody_builds_a_query_vector_outside_encode() -> None:
    """**어휘 확장을 건너뛰는 길이 있으면 안 된다.**

    질의 벡터를 만드는 곳은 `search.encode` 하나여야 한다. `embed.encode_query` 를 직접 부르는
    곳이 생기면 그 질의만 확장 없이 검색되고, **증상은 "그 문항만 이상하다"로만 나타난다.**
    (`evaluate.py` 는 예외다 — 6단계는 모델 3종 비교라 확장이 들어가면 비교 축이 흔들린다.)
    """
    import ast
    import pathlib

    root = pathlib.Path(search.__file__).resolve().parents[2]      # daengs_life/
    allowed = {"rag/stages/search.py", "rag/stages/embed.py", "rag/stages/evaluate.py"}
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in allowed or "__pycache__" in rel:
            continue
        # **주석이 아니라 실제 호출을 센다** — 도크스트링에 이름이 나오는 것은 설명이지 경로가 아니다
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "encode_query"):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, f"encode_query 를 직접 부른다 — search.encode(st=…) 를 쓸 것: {offenders}"


def test_the_query_text_stays_original() -> None:
    """⚠ **`Query.text` 는 원문이다.** `transport.exclusions()` 와 `region.orgs()` 가 그것을
    읽으므로, 넓힌 문장을 넣으면 교통·지역 신호가 오작동한다 — `비행기` → `운송` 이 더해지면
    교통 배제가 엉뚱하게 켜진다.
    """
    q = "우리 애 비행기에 태울 수 있나요?"
    built = search.make_query(q, vector=[0.0])
    assert built.text == q
    # 렉시컬 축은 넓힌 문장으로 만든다 — dense 만 넓히면 두 축이 다른 질의를 본다
    assert "항공" in built.tsquery
