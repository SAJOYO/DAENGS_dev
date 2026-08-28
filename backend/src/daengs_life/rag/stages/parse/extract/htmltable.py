"""포맷 층 ③ — HTML `<table>` 을 IR `Table`(header + rows)로 편다.

`boxtable` 이 괘선 아트를 다뤘다면 여기는 진짜 표 태그다. **사이트 지식이 아니라 포맷 지식**이라
`extract/` 에 둔다 — 지역 도시철도공사(부산·대구·인천)가 같은 형태이고, 펫보험 약관 HTML 도
같은 것을 필요로 한다.

**`colspan`·`rowspan` 을 전개하지 않으면 헤더와 값이 어긋난다.** 서울교통공사 별표 3 이 그 예다:

    ┌──────── 정기권 (colspan=3) ────────┬ 1회권 운임 (rowspan=2) ┬ 교통카드 운임 (rowspan=2) ┐
    │ 종별(단계) │ 운임(원) │ 이용거리   │                        │                           │
    │ 1          │ 68,200   │ 20km까지   │ 1,850                  │ 1,750                     │

헤더를 `<th>` 나열 그대로 읽으면 6칸인데 본문 행은 5칸이라, 청커의 `헤더: 값` 짝짓기가 한 칸씩
밀려 **`종별 교통카드 운임(원): 20km까지`** 같은 문장을 만든다. 예외는 나지 않는다 —
`zip` 이 짧은 쪽에서 조용히 끊기기 때문이다 (RAG-036 ③).

그래서 **격자로 먼저 편다.** 병합 칸은 자기가 덮는 모든 좌표에 같은 값을 채우고, 헤더가 여러
줄이면 열별로 위에서 아래로 이어 붙인다 (`정기권 종별(단계)`). 중복은 한 번만 남긴다.
"""
from __future__ import annotations

from typing import Any

from daengs_life.crawler.core import textutil


def _cell(node: Any) -> str:
    return textutil.squeeze(textutil.block_text(node)).replace("\n", " ").strip()


def grid(rows: list[Any]) -> list[list[str]]:
    """`<tr>` 목록 → 직사각 격자. 병합 칸은 덮는 좌표를 전부 채운다."""
    filled: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(rows):
        c = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while (r, c) in filled:
                c += 1
            text = _cell(cell)
            span_c = max(1, _int(cell.get("colspan")))
            span_r = max(1, _int(cell.get("rowspan")))
            for dr in range(span_r):
                for dc in range(span_c):
                    filled[(r + dr, c + dc)] = text
            c += span_c
    if not filled:
        return []
    height = max(r for r, _ in filled) + 1
    width = max(c for _, c in filled) + 1
    return [[filled.get((r, c), "") for c in range(width)] for r in range(height)]


def _int(raw: Any) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return 1


def _merge_header(head_grid: list[list[str]]) -> list[str]:
    """헤더가 여러 줄이면 열별로 이어 붙인다. 병합으로 반복된 같은 라벨은 한 번만."""
    if not head_grid:
        return []
    out = []
    for col in zip(*head_grid):
        seen: list[str] = []
        for label in col:
            if label and label not in seen:
                seen.append(label)
        out.append(" ".join(seen))
    return out


def header_and_rows(table: Any) -> tuple[list[str], list[list[str]]]:
    """`<table>` → (헤더 1줄, 본문 행들). `<thead>` 가 없으면 첫 행을 헤더로 본다."""
    thead = table.find("thead")
    tbody = table.find("tbody")

    if thead is not None:
        header = _merge_header(grid(thead.find_all("tr")))
        body_rows = (tbody or table).find_all("tr")
        if tbody is None:                      # thead 는 있는데 tbody 가 없는 표
            body_rows = [tr for tr in body_rows if tr.find_parent("thead") is None]
        return header, grid(body_rows)

    all_rows = grid((tbody or table).find_all("tr"))
    if not all_rows:
        return [], []
    return all_rows[0], all_rows[1:]
