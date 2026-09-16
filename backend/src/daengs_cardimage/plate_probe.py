"""틀 이미지에서 검은 제목판을 재어 `title.Plate` 를 만든다.

달마다 판의 높이·위치·오른쪽 경계가 다르다(09-14 실측: 4월 y 53~145 · 9월 y 40~135).
손으로 재면 9월처럼 두 번 틀리므로 도구로 재고 눈으로 확인한다.
"""

from __future__ import annotations

from PIL import Image

from daengs_cardimage.title import PLATE_DARK, Plate

# `title.PLATE_PROBE_XS`(234~246, 판 왼쪽 끝과 글자 시작 사이의 좁은 4열)는 모델이 그린
# 카드에서 "몇 px 어긋났나"만 재는 용도라 좁아도 된다. 그런데 빈 틀 자체를 재려 하면
# 홀로그램 하이라이트가 하필 그 좁은 열을 대각선으로 가로질러(09-16 실측: 4월 x=234~246
# 열은 y 90~145 구간이 하이라이트로 밝게 씻겨 나가 판을 못 찾는다) 안 된다. 틀에는 아직
# 글자가 없으므로 더 넓은 열 집합(230~298)에서 "절반 이상이 어둡다"로 판정한다 —
# 하이라이트가 그중 한두 열만 훑고 지나가도 나머지 열이 버틴다.
PROBE_XS = tuple(range(230, 300, 4))
MIN_PLATE_RUN = 40      # 제목판은 최소 이만큼 세로로 이어진다 (테두리 선과 가른다)
SEARCH_BOTTOM = 220     # 판은 카드 위쪽에만 있다


def _row_is_dark(px, y: int) -> bool:
    dark = sum(1 for x in PROBE_XS if max(px[x, y][:3]) < PLATE_DARK)
    return dark >= len(PROBE_XS) // 2


def _longest_dark_run(px, height: int) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    start: int | None = None
    limit = min(height, SEARCH_BOTTOM)
    for y in range(limit):
        if _row_is_dark(px, y):
            start = y if start is None else start
        elif start is not None:
            if y - start >= MIN_PLATE_RUN and (best is None or y - start > best[1] - best[0]):
                best = (start, y - 1)
            start = None
    if start is not None and limit - start >= MIN_PLATE_RUN and (best is None or limit - 1 - start > best[1] - best[0]):
        best = (start, limit - 1)
    return best


def _right_edge(px, y: int, width: int, from_x: int) -> int:
    x = from_x
    while x + 1 < width and max(px[x + 1, y][:3]) < PLATE_DARK:
        x += 1
    return x


def measure(img: Image.Image) -> Plate:
    rgb = img.convert("RGB")
    px = rgb.load()
    run = _longest_dark_run(px, rgb.height)
    if run is None:
        raise ValueError("제목판을 못 찾았습니다 — 틀 이미지를 확인하세요")
    top, bottom = run
    y_hi, y_lo = top + 3, bottom - 3
    edge = ((y_hi, _right_edge(px, y_hi, rgb.width, PROBE_XS[-1])),
            (y_lo, _right_edge(px, y_lo, rgb.width, PROBE_XS[-1])))
    return Plate(center_y=(top + bottom) // 2, edge=edge, top_y=top)
