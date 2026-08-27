"""산책 관통 검증용 픽스처 — 경로 하나와 그 옆 시설 배치를 한 곳에서 저작한다.

**왜 한 파일인가**: 이 경로는 두 번 쓰인다 — 서버 단독 관통(직접 POST)과 에뮬레이터
주입(`adb emu geo fix`). 좌표가 두 벌이면 두 실행의 차이가 업로더 탓인지 입력 탓인지
구분이 안 된다. 여기가 유일한 원천이고 양쪽이 같은 것을 읽는다.

**시설 배치가 설계의 핵심**: 밴드가 (10, 15, 20)m 라 횡거리 하나로 진입 밴드 집합이
정확히 갈린다. 그래서 "encounter 가 나왔다"가 아니라 **어느 밴드까지 나왔어야 하는지**를
미리 계산해두고 맞춘다. 30m 짜리 하나는 아무 밴드에도 안 걸려야 하는 음성 대조군이다.

기대값은 `expectations()` 가 기하로 계산한다 — 손으로 적은 숫자를 쓰면 계산기가 틀렸을 때
같이 틀린다. 원 안 현(chord) 길이 2·sqrt(r²-d²) 를 보행 속도로 나눈 것이 체류의 이론값이고,
실제값은 GPS 점 간격 때문에 그 언저리에 떨어진다.

    uv run python -m scripts.verify.walk_fixture route   > route.json
    uv run python -m scripts.verify.walk_fixture seed    > seed.sql
    uv run python -m scripts.verify.walk_fixture expect
"""

import json
import math
import sys

# 강남역. dev_seed.sql 이 이 주변에 병원 9곳을 심어둬서 지도로 눈 확인이 된다.
ORIGIN_LAT = 37.4979
ORIGIN_LNG = 127.0276

SPEED_MPS = 1.4            # 보행. facts.py 의 MOVING_SPEED_MPS(0.5) 보다 확실히 위
FIX_INTERVAL_S = 5
STOP_AT_M = 140.0          # 이 지점에서 멈춘다
STOP_S = 35                # VISIT_MIN_STOP_S(30) 를 넘겨 'visited_guess' 가 되게
PAUSE_AT_M = 210.0         # 여기서 명시적 pause/resume → chain_index 1 증가
TOTAL_M = 350.0

M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LNG = 111_320.0 * math.cos(math.radians(ORIGIN_LAT))

# (ref, 동선상 위치 m, 횡거리 m) — 횡거리가 진입 밴드를 결정한다
FACILITIES = (
    ("dev-near",  70.0,  5.0),    # 10 / 15 / 20 전부
    ("dev-stop", 140.0,  6.0),    # 전부 + 정지 겹침. 정지 지점 바로 옆이다
    ("dev-mid",  245.0, 14.0),    # 15 / 20   (chain 1)
    ("dev-edge", 300.0, 17.0),    # 20 만
    ("dev-out",  330.0, 30.0),    # 아무것도 — 20m 버퍼 밖. 음성 대조군
)


def to_latlng(east_m: float, north_m: float) -> tuple[float, float]:
    return (ORIGIN_LAT + north_m / M_PER_DEG_LAT, ORIGIN_LNG + east_m / M_PER_DEG_LNG)


def route() -> list[dict]:
    """동쪽 직선. 정지 한 번, pause/resume 한 번. client_seq 는 폰이 매기는 것과 같은 규칙."""
    fixes: list[dict] = []
    step_m = SPEED_MPS * FIX_INTERVAL_S       # 7m
    t = 0.0
    east = 0.0
    chain = 0

    def emit(east_m: float, at_s: float, chain_index: int) -> None:
        lat, lng = to_latlng(east_m, 0.0)
        fixes.append({
            "client_seq": len(fixes),
            "chain_index": chain_index,
            "offset_s": round(at_s, 3),
            "lat": round(lat, 7),
            "lng": round(lng, 7),
            "accuracy_m": 8.0,                # 최대 밴드(20m)보다 작아야 판정이 산다
        })

    while east < TOTAL_M + 1e-9:
        emit(east, t, chain)
        if abs(east - STOP_AT_M) < 1e-9:                    # 같은 자리에서 시간만 흐른다
            for _ in range(STOP_S // FIX_INTERVAL_S):
                t += FIX_INTERVAL_S
                emit(east, t, chain)
        if abs(east - PAUSE_AT_M) < 1e-9:                   # 명시적 단절. 이후는 다른 chain
            chain += 1
        t += FIX_INTERVAL_S
        east += step_m
    return fixes


def seed_sql() -> str:
    """facility dev 행. source='dev' 라 실제 kcisa/kto 스냅샷 교체에 안 쓸려나간다."""
    rows = []
    for ref, offset_m, lateral_m in FACILITIES:
        lat, lng = to_latlng(offset_m, lateral_m)
        rows.append(
            f"('{ref}', 'cafe', '개발용', "
            f"ST_SetSRID(ST_MakePoint({lng:.7f},{lat:.7f}),4326)::geography, "
            f"'dev', '{ref}', 'dev-fixture')"
        )
    values = ",\n  ".join(rows)
    return (
        "-- 산책 관통 검증용. scripts/verify/walk_fixture.py 가 생성한다 — 손으로 고치지 않는다.\n"
        "INSERT INTO facility (name, kind, category3, location, source, source_ref, snapshot)\n"
        f"VALUES\n  {values}\n"
        "ON CONFLICT (source, source_ref) WHERE source_ref IS NOT NULL\n"
        "DO UPDATE SET location = EXCLUDED.location, kind = EXCLUDED.kind;\n"
    )


def expectations() -> list[dict]:
    """밴드별 체류의 이론값. 원 안 현 길이 / 속도. 정지 지점은 멈춘 시간을 더한다."""
    out = []
    for ref, offset_m, lateral_m in FACILITIES:
        dwell = {}
        for r in (10.0, 15.0, 20.0):
            if lateral_m >= r:
                dwell[int(r)] = 0.0
                continue
            half_chord = math.sqrt(r * r - lateral_m * lateral_m)
            seconds = 2 * half_chord / SPEED_MPS
            # 정지는 그 자리에서 시간만 흐르므로 원 안에 있으면 통째로 더해진다.
            # 원이 동선을 자르는 구간의 반폭은 r - lateral 이 아니라 반현이다 —
            # 0 < lateral < r 인 동안 반현이 늘 더 크므로 그 식은 겹침을 놓친다.
            if abs(offset_m - STOP_AT_M) < half_chord:
                seconds += STOP_S
            dwell[int(r)] = round(seconds, 1)
        out.append({
            "facility_ref": ref,
            "lateral_m": lateral_m,
            "is_encounter": lateral_m < 20.0,
            "dwell_s": dwell,
            "stop_overlap_10m": ref == "dev-stop",
        })
    return out


if __name__ == "__main__":
    # 출력은 psql 로 파이프된다. Windows 기본 콘솔 인코딩(cp949)이면 주석의 '—' 하나에
    # UnicodeEncodeError 로 죽는다 — 리다이렉트 대상이 무엇이든 UTF-8 로 낸다.
    sys.stdout.reconfigure(encoding="utf-8")
    what = sys.argv[1] if len(sys.argv) > 1 else "route"
    if what == "route":
        print(json.dumps({
            "origin": [ORIGIN_LAT, ORIGIN_LNG],
            "fix_interval_s": FIX_INTERVAL_S,
            "fixes": route(),
        }, indent=2))
    elif what == "seed":
        print(seed_sql(), end="")
    elif what == "expect":
        print(json.dumps(expectations(), indent=2, ensure_ascii=False))
    else:
        raise SystemExit(f"unknown: {what} (route | seed | expect)")
