"""장 캐시 왕복 계약. `scripts/spikes/territory_paint/persona_experiment.py` 의 `load`.

실제로 한 번 깨졌던 버그의 회귀 테스트다. 캐시에 `Person` dataclass 를 그대로 pickle 했는데,
`-m scripts.spikes.territory_paint.persona_experiment` 로 만든 캐시를 `-m scripts.spikes.territory_paint.layer_scenes` 에서
읽으니 터졌다:

    AttributeError: Can't get attribute 'Person' on <module 'scripts.spike_layer_scenes'>

(당시 실제 출력이라 그대로 둔다. 그 모듈은 지금
`scripts/spikes/territory_paint/layer_scenes.py` 다.)

pickle 은 클래스를 **정의 모듈 이름**으로 찾는데 `-m` 진입점이 다르면 그 이름이 달라진다.
그래서 캐시는 순수 자료로만 담는다 — **진입점에 묶이면 안 된다.**
"""

import pickle
from dataclasses import asdict
from datetime import UTC, datetime

from app.features.territory.paint import NARROW_STEP, Cellophane


def _sheet(walk_id: str) -> Cellophane:
    return Cellophane(
        walk_id=walk_id,
        at=datetime(2026, 7, 1, 9, tzinfo=UTC),
        radius_u=15.0,
        profile=NARROW_STEP.name,
        occupancy={(1, 2): 3.5, (1, 3): 1.25},
        peak={(1, 2): 1.0, (1, 3): 0.15},
        profile_fp=NARROW_STEP.fingerprint,
    )


def test_plain_payload_survives_pickle_without_the_defining_module():
    """캐시에 담기는 형태가 클래스 이름에 안 묶이는지 — dict 라 어느 진입점에서도 읽힌다."""
    plain = {"sheets": [asdict(_sheet("w0")), asdict(_sheet("w1"))]}
    blob = pickle.loads(pickle.dumps(plain))
    assert [row["walk_id"] for row in blob["sheets"]] == ["w0", "w1"]
    # pickle 이 되살린 것에 프로젝트 클래스가 하나도 안 섞여 있어야 한다
    assert all(type(row) is dict for row in blob["sheets"])


def test_round_trip_rebuilds_an_identical_cellophane():
    """dict → Cellophane 복원이 값을 하나도 안 잃는지."""
    original = _sheet("w0")
    restored = Cellophane(**pickle.loads(pickle.dumps(asdict(original))))
    assert restored == original
    assert restored.occupancy == original.occupancy
    assert restored.peak == original.peak
    assert restored.profile_fp == original.profile_fp
    assert restored.grid_version == original.grid_version


def test_cell_keys_stay_tuples_through_the_cache():
    """셀 키가 튜플로 살아 돌아와야 한다 — 문자열이 되면 겹치기가 조용히 어긋난다."""
    restored = Cellophane(**pickle.loads(pickle.dumps(asdict(_sheet("w0")))))
    assert all(isinstance(cell, tuple) and len(cell) == 2 for cell in restored.occupancy)
    assert restored.occupancy[(1, 2)] == 3.5
