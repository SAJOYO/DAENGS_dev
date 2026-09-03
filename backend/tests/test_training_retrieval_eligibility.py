"""Evidence eligibility for the Training retriever — what may occupy a top-4 slot.

Background (2026-09-03, PR #165): casual biting queries such as "자꾸 깨물어요" and
"손 물어요" had one to three of their four evidence slots taken by a board's
pagination strip.  After the markdown-link strip only the bold current page
number ("**2**") remained, and a digit satisfied the old "any Hangul, letter or
digit" check.  No corpus text is copied here; the fixtures mimic the shape only.
No model and no database are involved.
"""

from __future__ import annotations

import pytest

from daengs_training.retrieval.pgvector import RuntimeRetriever

eligible = RuntimeRetriever.is_retrieval_eligible

PAGE_LINK = "[{label}](/board/list.do?searchStr=&listUnit=10&cmCode=M1&currPage={page})"


def pagination_strip(current: int, pages: int = 5) -> str:
    parts = [
        PAGE_LINK.format(label="처음 페이지로", page=1),
        PAGE_LINK.format(label="이전 페이지", page=1),
    ]
    for page in range(1, pages + 1):
        parts.append(
            f"**{page}**" if page == current else PAGE_LINK.format(label=str(page), page=page)
        )
    parts.append(PAGE_LINK.format(label="다음 페이지", page=pages))
    return "\n".join(parts)


@pytest.mark.parametrize("current", [1, 2, 3])
def test_pagination_strip_is_not_evidence(current: int) -> None:
    """The three real shapes: links plus one bold page number, nothing else."""
    assert not eligible(pagination_strip(current))


def test_bare_numbers_or_markup_are_not_evidence() -> None:
    assert not eligible("**2**")
    assert not eligible("1 2 3 4 5")
    assert not eligible("| --- | --- |\n| 12 | 34 |")
    assert not eligible("")
    assert not eligible("[다음](/x) [이전](/y)")


@pytest.mark.parametrize(
    "prose",
    [
        # Biting / chewing evidence keeps flowing.
        "반복교육을 하면 서서히 무는 것과 물지 말아야 하는 것을 구분하게 됩니다.",
        "옷이나 손을 물면 놀이를 멈추고 물어도 되는 장난감으로 바꿔 줍니다.",
        # Negative semantic controls: unrelated behaviors are untouched by this
        # filter — nothing here rewrites or reclassifies a query or a chunk.
        "장난감을 가지고 있을 때 빼앗으려 하면 지키려는 행동을 보입니다.",
        "가족 중 한 사람만 계속 따라다니는 반려견의 교정 방법입니다.",
        "초인종 소리에 짖는 행동은 '안돼' 명령과 함께 교정합니다.",
        # Prose that happens to sit next to a link or a number is still prose.
        "[1](/board?page=1) 배변 패드는 잠자리에서 떨어진 곳에 둡니다.",
        "3주 정도 반복하면 크레이트 교육이 자리 잡습니다.",
        # A table row with real content.
        "| 제목 | 짖는 원인 | 조회 | 2623 |\n■ 개요 짖는 행동은 반려견의 의사 표현입니다.",
    ],
)
def test_korean_prose_stays_eligible(prose: str) -> None:
    assert eligible(prose)


def test_existing_artifact_rules_are_unchanged() -> None:
    assert not eligible("[1](#) [2](#)")
    assert not eligible("수집된 HTML에서 본문 텍스트를 추출하지 못했습니다.")
    assert not eligible("schema_version: 1\ndoc_id: x")
    assert not eligible("A" * 200)
    assert not eligible("데이터: " + "QUJD" * 40)  # base64 blob next to a label
