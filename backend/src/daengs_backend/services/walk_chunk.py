"""좌표 묶음 인코딩. 점마다 한 줄이던 것을 묶음당 한 줄로 담습니다.

## 왜 이렇게 담나

`walk_points` 는 좌표 한 점에 한 줄이었습니다. 실기기 실측으로 초당 1.02점이 쌓여
30분 산책이면 1,842줄입니다. 그런데 **이 좌표를 조건으로 거는 질의가 하나도 없습니다**
— 늘 "한 산책의 전부"를 통째로 읽어 JSON 으로 내보낼 뿐입니다. 점당 실제 데이터는
12바이트쯤인데 행 하나에 124바이트(힙 88 + PK 인덱스 32)를 냈습니다. 90%가 행 헤더입니다.

이진(bytea)으로 담으면 더 작지만 **우리가 평생 관리할 인코더·디코더와 형식 버전**이
생깁니다. jsonb 는 psql 에서 눈으로 보이고, 숫자가 `numeric` 이라 부동소수점 반올림
걱정도 없습니다.

다만 **키를 점마다 반복하면 안 됩니다.** 객체로 담으면 일곱 개 키가 1,842번 들어가
압축 전 151 B/점입니다. 위치 배열이면 62 B/점이고 TOAST 압축까지 거치면 22 B/점입니다.

    실측(131점 트랙) : 점당 한 줄 124 B → jsonb 배열 22 B  (5.6배)

## 모양

    {"v": 1,
     "cols": ["seq", "chain", "at", "lat", "lng", "acc", "mock"],
     "pts": [[0, 0, 1788174280387, 37.566500, 126.977900, 5.7, 0], ...]}

**`cols` 를 같이 담습니다.** 위치 배열은 스스로를 설명하지 못합니다 — 나중에 칸이
늘거나 순서가 바뀌어도 읽는 쪽이 `v` 와 `cols` 를 보고 맞출 수 있어야 합니다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from daengs_backend.schemas.walk import WalkPointUpload

__all__ = ["CHUNK_COLUMNS", "CHUNK_VERSION", "decode_chunk", "encode_chunk"]

#: 형식 번호. 칸이 바뀌면 올리고, 읽는 쪽은 모르는 번호를 거부합니다 —
#: 옛 서버가 새 묶음을 아무 말 없이 잘못 읽으면 안 됩니다.
CHUNK_VERSION = 1

#: 위치 배열의 칸 순서. `at` 은 epoch 밀리초, `mock` 은 0/1 입니다.
CHUNK_COLUMNS = ("seq", "chain", "at", "lat", "lng", "acc", "mock")

#: 좌표 자리수. `NUMERIC(9, 6)` 이 저장하던 것과 같아야 합니다 — 여기가 어긋나면
#: 담는 방식을 바꾸면서 기록을 바꾼 것이 됩니다.
_COORD_PLACES = Decimal("0.000001")


def encode_chunk(points: list[WalkPointUpload]) -> dict[str, Any]:
    """묶음 하나를 jsonb 에 담을 모양으로.

    **순번 순서로 담습니다.** 뒤섞여 도착해도 지도는 순서대로 선을 긋습니다.
    """
    if not points:
        # 빈 묶음은 `point_count > 0` 제약에 걸립니다. 담기 전에 막습니다.
        raise ValueError("빈 좌표 묶음은 담지 않습니다.")
    return {
        "v": CHUNK_VERSION,
        "cols": list(CHUNK_COLUMNS),
        "pts": [_row(p) for p in sorted(points, key=lambda p: p.client_seq)],
    }


def decode_chunk(payload: dict[str, Any]) -> list[WalkPointUpload]:
    """담아 둔 묶음을 다시 좌표로.

    `cols` 를 보고 칸을 찾으므로 **순서가 바뀌어도 읽힙니다.**
    """
    version = payload.get("v")
    if version != CHUNK_VERSION:
        raise ValueError(f"모르는 좌표 묶음 형식입니다: v={version!r}")
    columns = payload.get("cols")
    if not isinstance(columns, list):
        raise ValueError("좌표 묶음에 cols 가 없습니다.")
    index = {name: i for i, name in enumerate(columns)}
    missing = set(CHUNK_COLUMNS) - index.keys()
    if missing:
        raise ValueError(f"좌표 묶음에 없는 칸이 있습니다: {sorted(missing)}")
    return [_point(row, index) for row in payload.get("pts", [])]


def _row(point: WalkPointUpload) -> list[Any]:
    return [
        point.client_seq,
        point.chain_index,
        # epoch 밀리초. 앱이 밀리초로 보내므로 그 정밀도면 충분합니다.
        int(point.at.timestamp() * 1000),
        # **Decimal 을 그대로 넘기지 않습니다.** SQLAlchemy 의 JSONB 는 기본
        # `json.dumps` 로 직렬화하는데 Decimal 을 못 담아 저장에서 터집니다.
        #
        # float 로 바꿔도 안전합니다 — 소수점 여섯 자리는 유효숫자 여덟 자리라
        # float64(약 15~17자리) 안에 정확히 들어가고, 파이썬이 왕복하는 가장 짧은
        # 표기로 찍으므로 `37.566500` 이 `37.5665` 로 나가 postgres 가 numeric 으로
        # 그대로 담습니다. 읽을 때 다시 Decimal 로 세웁니다.
        float(point.lat.quantize(_COORD_PLACES)),
        float(point.lng.quantize(_COORD_PLACES)),
        point.accuracy_m,
        1 if point.is_mock else 0,
    ]


def _point(row: list[Any], index: dict[str, int]) -> WalkPointUpload:
    accuracy = row[index["acc"]]
    return WalkPointUpload(
        client_seq=int(row[index["seq"]]),
        chain_index=int(row[index["chain"]]),
        at=datetime.fromtimestamp(int(row[index["at"]]) / 1000, tz=UTC),
        lat=Decimal(str(row[index["lat"]])),
        lng=Decimal(str(row[index["lng"]])),
        accuracy_m=None if accuracy is None else float(accuracy),
        is_mock=bool(row[index["mock"]]),
    )
