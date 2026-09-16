"""services/walk_chunk.py — 좌표 묶음 인코딩.

여기서 보는 것은 **넣은 것이 그대로 나오는가** 하나입니다. 좌표는 기록이라, 담는
방식을 바꾸면서 값이 미세하게 달라지면 지도의 선이 조용히 틀어집니다. 그 틀어짐은
눈으로는 안 보이고 나중에 거리에서 드러납니다.

DB 도 FastAPI 도 쓰지 않습니다 — 순수 함수라 바로 부릅니다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.services.walk_session.chunk import (
    CHUNK_COLUMNS,
    CHUNK_VERSION,
    decode_chunk,
    encode_chunk,
)

BASE = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)


def point(
    seq: int,
    *,
    lat: str = "37.566500",
    lng: str = "126.977900",
    chain: int = 0,
    accuracy: float | None = 5.7,
    mock: bool = False,
    after_ms: int = 0,
) -> WalkPointUpload:
    return WalkPointUpload(
        client_seq=seq,
        chain_index=chain,
        at=BASE + timedelta(milliseconds=after_ms),
        lat=Decimal(lat),
        lng=Decimal(lng),
        accuracy_m=accuracy,
        is_mock=mock,
    )


def walk(count: int) -> list[WalkPointUpload]:
    """실제 산책을 닮은 좌표들. 1초에 하나, 한 걸음이 1.5m 쯤입니다."""
    return [
        point(
            i,
            lat=f"37.{566500 + i * 13:06d}",
            lng=f"126.{977900 - i * 7:06d}",
            accuracy=3.5 + (i % 7) * 0.4,
            after_ms=i * 1_000 + (i % 3),
        )
        for i in range(count)
    ]


def test_한_점을_그대로_되돌린다() -> None:
    assert decode_chunk(encode_chunk([point(0)])) == [point(0)]


def test_긴_산책을_그대로_되돌린다() -> None:
    """앱이 한 번에 보내는 최대치입니다 — `WalkSync.POINTS_PER_REQUEST` = 2000."""
    points = walk(2_000)
    assert decode_chunk(encode_chunk(points)) == points


def test_좌표는_소수점_여섯째_자리까지_지킨다() -> None:
    """`NUMERIC(9, 6)` 이 저장하던 자리수 그대로여야 합니다.

    여기가 어긋나면 이 변경이 기록을 바꾼 것이 됩니다.
    """
    points = [point(0, lat="-33.868820", lng="151.209290")]
    (back,) = decode_chunk(encode_chunk(points))
    assert back.lat == Decimal("-33.868820")
    assert back.lng == Decimal("151.209290")


def test_밀리초가_살아_있다() -> None:
    """기기가 주는 시각은 초 단위가 아닙니다 (실측: 1788174280863)."""
    points = [point(0, after_ms=387), point(1, after_ms=1_863)]
    assert [p.at for p in decode_chunk(encode_chunk(points))] == [p.at for p in points]


def test_정확도가_없는_점도_된다() -> None:
    """`accuracy_m` 은 nullable 입니다. 0 으로 채우면 '아주 정확함' 이 됩니다."""
    points = [point(0, accuracy=None), point(1, accuracy=5.0), point(2, accuracy=None)]
    assert decode_chunk(encode_chunk(points)) == points


def test_일시정지가_남는다() -> None:
    """`chain_index` 가 다른 두 점은 직선으로 이으면 안 되는 사이입니다."""
    points = [point(0, chain=0), point(1, chain=0), point(2, chain=1)]
    assert [p.chain_index for p in decode_chunk(encode_chunk(points))] == [0, 0, 1]


def test_가상_위치_표시가_남는다() -> None:
    """점수나 랭킹이 생기면 걸러야 할 값입니다. 지우지 않고 표시만 해 둡니다."""
    points = [point(0, mock=False), point(1, mock=True)]
    assert [p.is_mock for p in decode_chunk(encode_chunk(points))] == [False, True]


def test_순번_순서대로_담는다() -> None:
    """뒤섞여 들어와도 담길 때는 순서대로입니다 — 지도가 그 순서로 선을 긋습니다."""
    points = [point(2), point(0), point(1)]
    assert [p.client_seq for p in decode_chunk(encode_chunk(points))] == [0, 1, 2]


def test_모양이_약속대로다() -> None:
    """`cols` 를 같이 담는 이유는 위치 배열이 스스로를 설명하지 못하기 때문입니다."""
    payload = encode_chunk(walk(3))
    assert payload["v"] == CHUNK_VERSION
    assert payload["cols"] == list(CHUNK_COLUMNS)
    assert len(payload["pts"]) == 3
    assert all(len(row) == len(CHUNK_COLUMNS) for row in payload["pts"])


def test_빈_묶음은_거부한다() -> None:
    """빈 묶음을 담으면 `point_count > 0` 제약에 걸린다. 담기 전에 막는다."""
    with pytest.raises(ValueError):
        encode_chunk([])


def test_모르는_형식_번호는_거부한다() -> None:
    """나중에 형식을 바꿀 때, 옛 서버가 새 묶음을 아무 말 없이 잘못 읽으면 안 됩니다."""
    payload = encode_chunk(walk(2))
    payload["v"] = 99
    with pytest.raises(ValueError):
        decode_chunk(payload)


def test_모르는_칸_순서도_읽는다() -> None:
    """`cols` 를 담아 둔 값어치가 여기서 나옵니다 — 순서가 바뀌어도 읽힙니다."""
    payload = encode_chunk([point(0, chain=2, accuracy=1.5, mock=True, after_ms=42)])
    order = payload["cols"]
    flipped = list(reversed(order))
    payload["cols"] = flipped
    payload["pts"] = [list(reversed(row)) for row in payload["pts"]]
    (back,) = decode_chunk(payload)
    assert back.client_seq == 0
    assert back.chain_index == 2
    assert back.accuracy_m == 1.5
    assert back.is_mock is True


def test_점당_바이트가_행보다_훨씬_작다() -> None:
    """이 변경의 존재 이유입니다.

    행 하나가 124 B 였습니다(힙 88 + 라인포인터 4 + PK 인덱스 32). 압축 전 jsonb
    배열이 실측 62 B/점이었으므로, 넉넉히 잡아도 100 B 는 넘지 않아야 합니다.
    (TOAST 압축은 여기서 재지 않습니다 — DB 가 하는 일입니다.)
    """
    import json

    points = walk(2_000)
    per_point = len(json.dumps(encode_chunk(points), separators=(",", ":"))) / len(points)
    assert per_point < 100, f"{per_point:.1f} B/점"
