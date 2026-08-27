"""육각 격자 golden vector — Python 쪽.

`app/geo/cells.py` 는 "Python·Android·적재가 같은 셀 id 를 만든다" 고 **주장**한다. 한쪽
언어의 테스트만으로는 그 주장이 지켜지지 않는다 — 둘 다 통과하면서 서로 다를 수 있다.

그래서 같은 벡터를 양쪽 테스트가 검사한다. 이 파일은 원본(`docs/contracts/hex-grid-golden.json`)을
읽고, Kotlin 쪽(`android/.../HexGridGoldenTest.kt`)은 **자기 모듈에 실린 사본**을 읽는다 —
android 모듈은 이 저장소 밖에서도 빌드돼야 해서 옆 폴더로 손을 뻗을 수 없다. 사본이 갈라지면
`test_android_copy_matches_the_contract` 가 잡는다.

값이 바뀌어야 한다면 격자가 바뀐 것이고, 그건 이미 저장된 셀 id(`anchor.cell` 48만 행)의
뜻이 바뀐다는 뜻이다. golden 을 갱신하기 전에 그 이전(移轉)을 먼저 정해야 한다.
"""

import json
from pathlib import Path

import pytest

from app.geo.cells import hex_cell


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs" / "contracts" / "hex-grid-golden.json").exists():
            return parent
    raise AssertionError("hex-grid-golden.json 을 찾지 못했다")


CONTRACT = "docs/contracts/hex-grid-golden.json"
ANDROID_COPY = "android/app/src/test/resources/hex-grid-golden.json"


def _golden() -> dict:
    return json.loads((_repo_root() / CONTRACT).read_text(encoding="utf-8"))


def test_android_copy_matches_the_contract():
    """사본이 원본과 한 바이트라도 다르면 두 언어가 다른 벡터를 검사하게 된다.

    그 순간 golden 이 지키려던 것(두 구현이 같은 셀 id 를 만든다)이 **양쪽 다 초록인 채로**
    무너진다. 사본을 둔 이유는 android 모듈이 이 저장소 밖에서도 빌드돼야 하기 때문이고,
    그 대가를 여기서 받는다.

    격자가 정말 바뀌어야 한다면 파일을 고치는 게 아니라 **새 버전**(`GRID_VERSION`)이다.
    """
    root = _repo_root()
    copy = root / ANDROID_COPY
    if not copy.exists():
        pytest.skip(f"{ANDROID_COPY} 없음 — android 가 이 저장소에 없는 사본이다")

    assert copy.read_bytes() == (root / CONTRACT).read_bytes(), (
        f"{ANDROID_COPY} 가 {CONTRACT} 와 다르다. 사본을 다시 맞추거나, "
        "격자를 정말 바꾸는 것이라면 GRID_VERSION 을 올려라"
    )


def test_golden_cases_match():
    golden = _golden()
    assert golden["cases"], "golden 이 비어 있다"
    for case in golden["cases"]:
        q, r = hex_cell(case["lat"], case["lng"], case["radius_u"])
        assert (q, r) == (case["q"], case["r"]), (
            f"{case['note']} lat={case['lat']} lng={case['lng']} "
            f"radius_u={case['radius_u']} → ({q}, {r}), golden ({case['q']}, {case['r']})"
        )


def test_golden_covers_latitude_span_and_several_radii():
    """한 좌표 한 반지름만 맞아서는 계약이 안 지켜진다 — 위도 폭과 반지름을 걸쳐야 한다."""
    golden = _golden()
    lats = {case["lat"] for case in golden["cases"]}
    radii = {case["radius_u"] for case in golden["cases"]}
    assert max(lats) - min(lats) > 30, "위도 범위가 좁다"
    assert len(radii) >= 3, "반지름이 하나뿐이면 나눗셈 오류를 못 잡는다"
