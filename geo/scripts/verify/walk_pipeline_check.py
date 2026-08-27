"""픽스처 경로를 서버에 직접 넣고 사실이 기대대로 나오는지 본다. Android 없이.

**왜 Android 전에 이걸 하나**: 업로더까지 다 만든 뒤 `encounters: []` 를 보면 원인이
셋(시드 없음 / 업로더 버그 / 계산 버그)으로 갈린다. 서버를 먼저 확정해두면 Android
단계에서 나오는 실패는 업로더 문제로 좁혀진다.

여기서 확인하는 것은 넷이다:
  - WalkFacts 의 시간·거리·정지가 저작한 경로와 맞나
  - 밴드별 체류가 시설 배치(횡거리 5/6/14/17/30m)와 맞나 — 20m 밖 하나는 안 나와야 한다
  - finish 뒤 walk_fix 가 실제로 지워지나 (프라이버시 주장의 유일한 관문)
  - 같은 배치를 다시 보내면 duplicates 로만 세나 (업로더가 기대는 멱등 계약)

    uv run python -m scripts.verify.walk_pipeline_check
"""

import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from scripts.verify.walk_fixture import FACILITIES, expectations, route

BASE = "http://127.0.0.1:8000"
SESSION_ID = "fixture-walk-0001"
DOG_ID = "halmae"                 # 서버 PERSONAS 의 정식 테스트 객체. 청소 키이기도 하다
BATCH = 2000                      # FixBatchIn.fixes 상한
T0 = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


def post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{path} → {exc.code}\n{exc.read().decode()[:800]}") from exc


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as response:
        return json.loads(response.read())


def as_wire(fix: dict) -> dict:
    return {
        "client_seq": fix["client_seq"],
        "chain_index": fix["chain_index"],
        "at": (T0 + timedelta(seconds=fix["offset_s"])).isoformat(),
        "lat": fix["lat"],
        "lng": fix["lng"],
        "accuracy_m": fix["accuracy_m"],
        "is_mock": False,
    }


def main() -> int:
    fixes = [as_wire(f) for f in route()]
    ended_at = T0 + timedelta(seconds=route()[-1]["offset_s"])

    post("/walk/sessions", {"id": SESSION_ID, "dog_id": DOG_ID,
                            "started_at": T0.isoformat()})

    uploaded = 0
    for start in range(0, len(fixes), BATCH):
        result = post(f"/walk/sessions/{SESSION_ID}/fixes",
                      {"fixes": fixes[start:start + BATCH]})
        uploaded += result["stored"]
    print(f"업로드      {uploaded} fix (배치 {-(-len(fixes) // BATCH)}개)")

    replay = post(f"/walk/sessions/{SESSION_ID}/fixes", {"fixes": fixes[:5]})
    idempotent = replay["stored"] == 0 and replay["duplicates"] == 5
    print(f"재전송 멱등  stored={replay['stored']} duplicates={replay['duplicates']}"
          f"  {'OK' if idempotent else '← 실패'}")

    done = post(f"/walk/sessions/{SESSION_ID}/finish", {"ended_at": ended_at.isoformat()})
    facts, quality = done["facts"], done["quality"]
    print("\n--- WalkFacts ---")
    for key in ("duration_s", "distance_m", "moving_distance_m", "moving_s",
                "stop_count", "stop_s", "evidence_origin"):
        if key in facts:
            print(f"  {key:20} {facts[key]}")
    print(f"  {'accepted/received':20} {quality.get('accepted')}/{quality.get('received')}")

    print("\n--- FacilityEncounter (기대 vs 실측) ---")
    got = {e["facility_ref"]: e for e in done["encounters"]}
    expected = {e["facility_ref"]: e for e in expectations()}
    ok = True
    print(f"  {'ref':10} {'횡거리':>6} {'10m':>13} {'15m':>13} {'20m':>13}")
    for ref, _offset, lateral in FACILITIES:
        want = expected[ref]
        if not want["is_encounter"]:
            hit = ref in got
            ok &= not hit
            print(f"  {ref:10} {lateral:5.0f}m   "
                  f"{'없어야 함 — ' + ('나옴 ← 실패' if hit else '없음 OK')}")
            continue
        if ref not in got:
            ok = False
            print(f"  {ref:10} {lateral:5.0f}m   encounter 없음 ← 실패")
            continue
        e = got[ref]
        cells = []
        for band in (10, 15, 20):
            actual = e[f"dwell_s_{band}m"]
            theory = want["dwell_s"][band]
            near = abs(actual - theory) <= max(6.0, theory * 0.25)
            ok &= near
            cells.append(f"{actual:>4}/{theory:<5.0f}{'' if near else '✗'}")
        print(f"  {ref:10} {lateral:5.0f}m   " + " ".join(f"{c:>13}" for c in cells))
        if want["stop_overlap_10m"] != e["stop_overlap_10m"]:
            ok = False
            print(f"             stop_overlap_10m 기대 {want['stop_overlap_10m']} "
                  f"실측 {e['stop_overlap_10m']} ← 실패")

    after = get(f"/walk/sessions/{SESSION_ID}")
    purged = after["session"]["state"] == "purged"
    print(f"\n원좌표 purge  state={after['session']['state']}"
          f"  {'OK' if purged else '← 실패'}")

    print(f"\n{'통과' if ok and purged and idempotent else '실패'}"
          f"   정리: DELETE FROM walk_session WHERE dog_id = '{DOG_ID}';")
    return 0 if (ok and purged and idempotent) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
