"""앵커 선별 — 격자·우선순위·결정론 계약."""

import math

from app.ingest.anchors import HEX_RADIUS_U, hex_cell, hex_center, select


def _lamp(lat, lng, kind="한전주"):
    return {"lat": lat, "lng": lng, "kind": kind, "instt": None, "as_of": None}


def test_one_anchor_per_cell():
    """같은 셀에 여럿 있어도 하나만 남는다 — 원본 12m 간격이 판정 원을 겹치게 하므로."""
    base = [_lamp(37.5000 + i * 0.00005, 127.0000) for i in range(8)]
    picked = select(base)
    assert len({p["cell"] for p in picked}) == len(picked)


def test_kepco_pole_wins_over_dedicated():
    """한전주가 진짜 전봇대다. 같은 셀이면 형태 우선순위가 거리보다 앞선다."""
    q, r = hex_cell(37.5, 127.0)
    cx, cy = hex_center(q, r)
    # 전용주를 중심에, 한전주를 살짝 떨어뜨려 놓아도 한전주가 뽑혀야 한다
    picked = select([_lamp(37.5, 127.0, "전용주"), _lamp(37.50008, 127.00008, "한전주")])
    kinds = [p["kind"] for p in picked if p["cell"] == f"anchor-hex:{round(HEX_RADIUS_U)}:{q}:{r}"]
    assert kinds == ["한전주"]
    assert math.isfinite(cx) and math.isfinite(cy)


def test_selection_is_deterministic():
    """같은 입력이면 같은 앵커. 순서가 바뀌어도 결과가 흔들리면 안 된다."""
    lamps = [_lamp(37.5 + i * 0.0004, 127.0 + i * 0.0003, "전용주") for i in range(30)]
    first = {(p["cell"], p["lat"], p["lng"]) for p in select(lamps)}
    second = {(p["cell"], p["lat"], p["lng"]) for p in select(list(reversed(lamps)))}
    assert first == second


def test_empty_cells_get_no_anchor():
    """후보가 없는 셀은 비운다 — 없는 자리에 앵커를 만들지 않는다."""
    picked = select([_lamp(37.5, 127.0)])
    assert len(picked) == 1


def test_cell_id_carries_radius():
    """반지름이 id 에 박혀야 격자를 바꿔도 옛 앵커와 섞이지 않는다."""
    picked = select([_lamp(37.5, 127.0)], radius_u=200.0)
    assert picked[0]["cell"].startswith("anchor-hex:200:")
