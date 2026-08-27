"""괘선표 파서 단위 테스트 (RAG-020).

원본 파일이 아니라 **손으로 만든 최소 표**를 쓴다. 실제 별표에 의존하면 법령이 개정될 때
테스트가 같이 깨져서 "코드가 틀린 것"과 "원문이 바뀐 것"을 구분할 수 없다.
실제 원본에 대한 확인은 `test_parse_law.py` 가 맡는다.
"""
from __future__ import annotations

from daengs_life.rag.stages.parse.extract import boxtable


CLOSED = """\
┌────────────┬──────┬───────────┐
│위반행위    │근거  │과태료     │
│            │      ├─────┬─────┤
│            │      │1차  │2차  │
├────────────┼──────┼─────┼─────┤
│가. 등록하지│제101 │20   │40   │
│않은 경우   │조    │     │     │
│            │      │     │     │
│나. 목줄 미 │제101 │20   │30   │
│착용        │조    │     │     │
└────────────┴──────┴─────┴─────┘
"""

# 바깥 테두리 없이 가운데 `│` 하나로만 나눈 표 (시행규칙 별표 12 형태)
OPEN = """\
  ──────────────┬────────────────
  1) 15일 이내  │ 교환 또는 환급
  폐사한 경우   │(단, 과실 제외)
  ──────────────┼────────────────
  2) 계약서 미  │ 계약해제
  교부          │
  ──────────────┴────────────────
"""

# light 와 heavy 가 섞인 표 (수수료 표 형태)
HEAVY = """\
┏━━━━━━━━┳━━━━━━━━┓
┃검사질병명      ┃수수료          ┃
┣━━━━━━━━╋━━━━━━━━┫
┃구제역          ┃6,200원         ┃
┗━━━━━━━━┻━━━━━━━━┛
"""


def _only_table(text: str) -> boxtable.TableBlock:
    tables = [b for b in boxtable.parse(text) if isinstance(b, boxtable.TableBlock)]
    assert len(tables) == 1, [type(b).__name__ for b in boxtable.parse(text)]
    return tables[0]


def test_closed_table_merges_header_and_joins_wrapped_rows() -> None:
    t = _only_table(CLOSED)
    # 병합 헤더는 걸친 컬럼 전부에 배분되고, 물리적 줄은 **공백으로** 이어진다
    assert t.header == ["위반행위", "근거", "과태료 1차", "과태료 2차"]
    # 논리 행은 빈 셀 행으로 끊기고, 셀 안의 hard-wrap 은 **공백 없이** 이어진다
    assert t.rows == [
        ["가. 등록하지않은 경우", "제101조", "20", "40"],
        ["나. 목줄 미착용", "제101조", "20", "30"],
    ]


def test_no_phantom_column_when_header_line_is_wider() -> None:
    """헤더 줄이 본문보다 넓게 그려져도 빈 컬럼이 생기면 안 된다 (별표 4 에서 실제로 겪음)."""
    wider = CLOSED.replace("│위반행위    │근거  │과태료     │",
                           "│위반행위    │근거  │과태료       │")
    assert all(row[-1] != "" for row in _only_table(wider).rows)


def test_open_table_without_outer_border() -> None:
    """`────┼────` 만으로 행을 나누는 표. 못 읽으면 내용이 통째로 사라진다."""
    t = _only_table(OPEN)
    assert t.n_cols == 2
    assert t.rows == [
        ["1) 15일 이내폐사한 경우", "교환 또는 환급(단, 과실 제외)"],
        ["2) 계약서 미교부", "계약해제"],
    ]


def test_heavy_box_characters() -> None:
    """같은 원본 안에서 light 와 heavy 가 섞여 나온다 — 글자 열거가 아니라 블록으로 판정한다."""
    t = _only_table(HEAVY)
    assert t.header == ["검사질병명", "수수료"]
    assert t.rows == [["구제역", "6,200원"]]


def test_text_block_joins_hard_wrap_without_space() -> None:
    """원본이 셀 폭에 맞춰 임의 위치에서 끊으므로 공백을 넣으면 `위반하 여` 가 된다."""
    blocks = boxtable.parse("  1. 일반기준\n  가. 위반행위의 횟수에 따른 가중된 부과기\n  준은 최근 2년간 적용한다.\n")
    assert isinstance(blocks[0], boxtable.TextBlock)
    assert blocks[0].paragraphs == ["1. 일반기준", "가. 위반행위의 횟수에 따른 가중된 부과기준은 최근 2년간 적용한다."]


def test_form_drawing_is_marked_degraded() -> None:
    """괘선으로 그린 빈 양식은 표가 아니다. 판단은 호출자에게 넘기되 표로 착각하지는 않는다."""
    drawing = "┌──────────────┐\n│              │\n└──────────────┘\n"
    blocks = boxtable.parse(drawing)
    assert all(isinstance(b, boxtable.TextBlock) and b.degraded for b in blocks)


def test_east_asian_width_is_used_for_column_offsets() -> None:
    """한글은 2칸이다. 문자 인덱스로 재면 헤더와 본문이 어긋난다."""
    assert boxtable.width("가나다") == 6
    assert boxtable.width("abc") == 3
