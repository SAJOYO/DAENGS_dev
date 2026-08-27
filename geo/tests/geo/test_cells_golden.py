"""육각 격자 golden vector — Python 쪽.

`app/geo/cells.py` 는 "Python·Android·적재가 같은 셀 id 를 만든다" 고 **주장**한다. 한쪽
언어의 테스트만으로는 그 주장이 지켜지지 않는다 — 둘 다 통과하면서 서로 다를 수 있다.

그래서 같은 파일(`docs/contracts/hex-grid-golden.json`)을 양쪽 테스트가 읽는다. Kotlin 쪽은
`android/app/src/test/java/com/daengs/geo/territory/HexGridGoldenTest.kt`.

값이 바뀌어야 한다면 격자가 바뀐 것이고, 그건 이미 저장된 셀 id(`anchor.cell` 48만 행)의
뜻이 바뀐다는 뜻이다. golden 을 갱신하기 전에 그 이전(移轉)을 먼저 정해야 한다.
"""

import json
from pathlib import Path

from app.geo.cells import hex_cell


def _golden() -> dict:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "docs" / "contracts" / "hex-grid-golden.json"
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise AssertionError("hex-grid-golden.json 을 찾지 못했다")


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
