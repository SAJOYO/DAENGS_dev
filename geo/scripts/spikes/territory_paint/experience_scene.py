"""장면 하나를 deterministic JSON 으로 — 저녁 산책 직전 화면이 읽을 것 전부.

    uv run python -m scripts.spikes.territory_paint.persona_year --cache osm.json --json personas.json
    uv run python -m scripts.spikes.territory_paint.experience_scene \\
        --personas personas.json --cache-sheets sheets.pkl --out scene.json

갈래는 [experience-scenario](../../../docs/explorations/walk/experience-scenario.md).

## 이 스크립트의 완료 조건

"화면을 만들 수 있다" 가 아니라 **장면의 모든 숫자와 카드가 JSON 하나로 나온다** 이다.
화면 없이 이 JSON 만 읽어도 장면 1~5 번이 재연돼야 한다.

## 왜 페르소나 둘인가

한 사람으로는 카드 셋이 다 안 나온다. 자료가 그렇게 생겨서지 코드가 모자라서가 아니다.

    C  time-of-day   아침 180 회 전부 골목 · 저녁 180 회 전부 공원 · 하천 0 회
                     → 조건 칩이 갈리는 것을 가장 세게 보여준다.
                       그런데 1 년 내내 안 변해서 **덜 간 곳이 없다**

    D  drift         Q1 골목만 → Q2 공원 추가 → Q3 하천 추가
                     → 골목 비중이 계속 떨어져서 **덜 간 곳 카드가 나온다.**
                       대신 아침·저녁이 같아서 시간대 칩으로는 안 갈린다

그래서 둘 다 내보내고 화면이 고르게 한다. **없는 카드를 만들어 내지 않는 것**이 이 스켈레톤의
규율이기도 하다 — C 에게 "덜 간 곳" 을 억지로 붙이면 그 카드는 어떤 자료에서도 참이 된다.

C 에서 중요한 것 하나: **전체 지도만 보면 골목과 공원이 둘 다 180/360 으로 똑같다.**
조건 칩이 있어야 갈린다는 것이 이 장면의 논점이고, 숫자가 정확히 그렇게 나온다.

## 영역은 손으로 그렸다 — 그리고 **사람마다 다르다**

자동 분할은 [금지 목록]에 있다. 각 route family 의 **목적지 점**(집에서 가장 먼 점)을
보고 그 둘레에 130m 사각형을 얹었다. 사용자가 지도를 보고 "여기가 공원" 이라고 그리는
행위의 대역이다.

처음엔 영역 하나를 두 페르소나가 같이 쓰게 했는데 틀렸다. **C 와 D 는 집도 목적지도
다르다** — D 의 공원은 C 의 공원에서 130m 떨어져 있고 하천은 아예 반대편이다. 같은 상자를
쓰니 D 의 하천이 0 회로 나왔다. 사람마다 자기 동네가 있는 게 당연하고, 영역이 사용자가
그리는 것이라면 사람마다 달라야 맞는다.

`empty` 는 **아무도 안 가는 자리**다. 집에서 780m 라 활동 범위 안인데 어느 페르소나의
경로에서도 570m 떨어져 있다 — 미개척 카드가 실제로 나오는지 보려고 둔다. 이건 공유해도 된다.

처음엔 집 남쪽(37.4865, 127.0470)에 뒀다가 **131/360 이 나왔다.** 골목 경로가 그 위를
지나고 있었다. 지도를 안 보고 "여기쯤 아무도 안 가겠지" 로 찍으면 이렇게 된다.

**좌표는 이 파일에 박혀 있다.** 실행 때 계산하지 않는다 — 계산하기 시작하면 그게 자동
분할이다.

## "지금" 도 사람마다 고른다

기본은 `마지막 산책 다음 날` 인데 D 는 그 날짜에서 추세가 평평하다. D 의 이동은 분기
단위라(1분기 골목만 → 2분기 공원 → 3분기 하천) 연말의 30 일 창 둘은 둘 다 4분기 안이다.

그래서 D 는 **변화가 실제로 일어난 날**(2026-05-05)을 쓴다. 3 월은 골목 30/30, 4 월은
골목 14/28 이라 30 일 창 비교에서 하락이 잡힌다.

이건 **연출이고 그렇다고 적어 둔다.** 사용자가 앱을 여는 날이 하필 그 날일 이유는 없다.
동시에 이것 자체가 E4 로 넘길 발견이다 — **"지난달보다" 라는 창이 분기 단위 이동을 못 본다.**
"""

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta

from app.features.territory.evidence import brief, sentence
from app.features.territory.experience import CHIPS, NamedRegion, build
from app.features.territory.layers import Projection
from app.features.territory.region import Region
from scripts.spikes.territory_paint.persona_experiment import PROFILE, RADIUS_U, load

# 사람이 그린 후보 영역. 페르소나마다 자기 동네가 있다.
# (영역 id, 중심 lat, 중심 lng, 반변 m, 이름)
Box = tuple[str, float, float, float, str]

# 아무도 안 가는 자리 — 미개척 카드가 실제로 나오는지 보려고 둔다. 이건 공유한다.
EMPTY: Box = ("empty", 37.48555, 127.05580, 130.0, "남동쪽 블록")

HAND_DRAWN: dict[str, tuple[Box, ...]] = {
    # C: 아침엔 골목, 저녁엔 공원. 하천은 한 번도 안 간다
    "C": (("park",  37.49218, 127.04747, 130.0, "도곡공원"),
          ("alley", 37.49171, 127.05316, 130.0, "골목 안쪽"),
          ("river", 37.49123, 127.05573, 130.0, "양재천 산책로"),
          EMPTY),
    # D: 골목만 → 공원 추가 → 하천 추가. 목적지가 C 와 다른 자리다
    "D": (("park",  37.49163, 127.04601, 130.0, "동네 공원"),
          ("alley", 37.49171, 127.05316, 130.0, "골목 안쪽"),
          ("river", 37.48590, 127.04145, 130.0, "천변 산책로"),
          EMPTY),
}

# 장면을 만들 날. None 이면 마지막 산책 다음 날.
# D 만 따로 잡는 이유는 위 docstring 참고 — 연말에는 추세가 평평하다.
SCENE_DAY: dict[str, date | None] = {"C": None, "D": date(2026, 5, 5)}


def _box(region_id: str, lat: float, lng: float, half_m: float, name: str) -> NamedRegion:
    dlat = math.degrees(half_m / 6_371_000.0)
    dlng = math.degrees(half_m / (6_371_000.0 * math.cos(math.radians(lat))))
    ring = ((lat - dlat, lng - dlng), (lat - dlat, lng + dlng),
            (lat + dlat, lng + dlng), (lat + dlat, lng - dlng))
    return NamedRegion(Region(id=region_id, version=1, ring=ring), name)


def regions_for(persona: str) -> list[NamedRegion]:
    return [_box(*box) for box in HAND_DRAWN[persona]]


FMT_HEAD = (chr(10) + "=== {pid} ({kind}) · 산책 {walks}회 · 지금 {now} ({season}·{band}) ===")
FMT_DONE = chr(10) + "장면 {count}개 → {out}"


def _rate(value) -> dict:
    """비율만 내보내지 않는다 — 분자·분모가 같이 가야 화면이 표본을 말할 수 있다."""
    return {"visited": value.visited, "selected": value.selected, "total": value.total,
            "rate": None if value.rate is None else round(value.rate, 4)}


def _evidence(row, *, chosen: bool = False) -> dict:
    """근거 하나 → JSON. **고른 것도 떨어진 것도 같은 모양으로 나간다.**

    화면이 "왜 이 말을 했지" 만이 아니라 "왜 저 말은 안 했지" 까지 펼칠 수 있어야 한다.
    """
    item = row.evidence
    return {
        "kind": item.kind, "region_id": item.region_id,
        "region_version": item.region_version, "name": item.name,
        "cohort": _rate(item.cohort), "cohort_label": item.cohort_label,
        "baseline": None if item.baseline is None else _rate(item.baseline),
        "baseline_label": item.baseline_label,
        "delta": None if item.delta is None else round(item.delta, 4),
        "trustworthy": item.trustworthy,
        "score": round(row.score, 4), "reasons": row.reasons,
        "dropped": row.dropped, "sayable": row.sayable,
        "chosen": chosen,
        # 말하지 않기로 한 근거에는 문장을 **안 만든다.** 붙여 두면 소비자가 `sayable`
        # 검사를 한 번 빠뜨리는 순간 그대로 거짓 푸시가 된다.
        "sentence": sentence(row) if row.sayable else None,
    }


def to_payload(scene, briefing, rings: dict) -> dict:
    """`Experience` → JSON. 여기서 값을 새로 만들지 않는다. 모양만 바꾼다."""
    return {
        "version": scene.version,
        "now": scene.now.isoformat(),
        "context": scene.context,
        "spec_label": scene.spec_label,
        "walks_total": scene.walks_total,
        "thresholds": scene.thresholds,
        "chips": [{"key": key, "label": label} for key, label in CHIPS],
        "regions": [
            {
                "id": stat.region_id,
                "version": stat.region_version,
                "name": stat.name,
                "ring": [[round(lat, 6), round(lng, 6)] for lat, lng in rings[stat.region_id]],
                "by_chip": {chip: _rate(value) for chip, value in stat.by_chip.items()},
                "trend": {
                    "delta": None if stat.trend.delta is None else round(stat.trend.delta, 4),
                    "recent": _rate(stat.trend.recent),
                    "previous": _rate(stat.trend.previous),
                    "trustworthy": stat.trend.trustworthy,
                },
            }
            for stat in scene.regions
        ],
        "briefing": {
            "chosen": (None if briefing.chosen is None
                       else _evidence(briefing.chosen, chosen=True)),
            # 고른 것도 **후보 목록 안에 그대로 있다** — 화면이 순위 전체를 펼칠 수 있어야
            # "왜 저건 안 골랐지" 가 검산된다. `chosen` 플래그로 어느 것인지만 표시한다.
            "candidates": [_evidence(row, chosen=row is briefing.chosen)
                           for row in briefing.candidates],
            "thresholds": briefing.thresholds,
        },
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--personas", required=True)
    parser.add_argument("--cache-sheets")
    parser.add_argument("--persona", default="C,D",
                        help="장면을 만들 페르소나. C=시간대 패턴, D=이동 추세")
    parser.add_argument("--chip", default="evening", help="지금 조건 (장면은 저녁이다)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    people = load(args.personas, args.cache_sheets)
    wanted = [name.strip() for name in args.persona.split(",") if name.strip()]
    chosen = [p for p in people if p.persona in wanted]
    missing = set(wanted) - {p.persona for p in chosen}
    if missing:
        print(f"없는 페르소나 {sorted(missing)}: 있는 것은 {[p.persona for p in people]}")
        return 1

    projection = Projection(radius_u=RADIUS_U, brush=PROFILE.name,
                            profile_fp=PROFILE.fingerprint)
    scenes = []
    for person in chosen:
        # "지금" 은 마지막 산책 다음 날 저녁이다. **시계를 읽지 않는다** — 합성 1 년치라
        # 오늘 날짜를 쓰면 최근 30 일이 통째로 비고, 같은 입력이 매일 다른 답을 낸다.
        day = SCENE_DAY.get(person.persona)
        if day is None:
            last = max(sheet.at for sheet in person.sheets)
            now = last + timedelta(days=1)
        else:
            now = datetime(day.year, day.month, day.day,
                           tzinfo=max(s.at for s in person.sheets).tzinfo)
        now = now.replace(hour=18, minute=30, second=0, microsecond=0)
        regions = regions_for(person.persona)
        scene = build(person.sheets, regions, now, projection, context_chip=args.chip)
        briefing = brief(scene)
        payload = to_payload(scene, briefing,
                             {r.region.id: r.region.ring for r in regions})
        payload["persona"] = {"id": person.persona, "kind": person.kind}
        scenes.append(payload)
        _report(person, scene, payload)

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"scenes": scenes}, handle, ensure_ascii=False, indent=2)
    print(FMT_DONE.format(count=len(scenes), out=args.out))
    return 0


def _report(person, scene, payload) -> None:
    print(FMT_HEAD.format(
        pid=person.persona, kind=person.kind, walks=scene.walks_total,
        now=f"{scene.now:%Y-%m-%d %H:%M}",
        season=scene.context["season"], band=scene.context["time_band"]))
    print(f"  {'영역':<12}{'전체':>12}{'아침':>12}{'저녁':>12}{'추세':>9}")
    for row in payload["regions"]:
        chips = row["by_chip"]
        # 삼항을 f-string 이어붙이기 **뒤에** 놓으면 안 된다. 조건이 사슬 전체를 감싸서
        # delta 가 있을 때 뒷단 하나만 찍힌다 — 실제로 그렇게 이름·숫자가 다 사라졌었다.
        delta = row["trend"]["delta"]
        trend = "—".rjust(9) if delta is None else f"{delta:>+9.2f}"
        print(f"  {row['name']:<11}"
              f"{chips['all']['visited']:>5}/{chips['all']['selected']:<6}"
              f"{chips['morning']['visited']:>5}/{chips['morning']['selected']:<6}"
              f"{chips['evening']['visited']:>5}/{chips['evening']['selected']:<6}"
              f"{trend}")
    said = payload["briefing"]["chosen"]
    if said is None:
        print("  말할 것 없음 — 자료가 그렇게 생긴 것이지 규칙이 실패한 것이 아니다")
    else:
        print(f"  → \"{said['sentence']}\"")
        base = said["baseline"]
        detail = f"{said['cohort']['visited']}/{said['cohort']['selected']}"
        if base is not None:
            detail += f" 대 {base['visited']}/{base['selected']}"
        print(f"     {said['kind']} · {detail} · 점수 {said['score']}")
    for row in payload["briefing"]["candidates"]:
        if row["chosen"]:
            continue
        why = row["dropped"] or f"점수 {row['score']}"
        print(f"     · {row['name']:<12}{row['kind']:<15}{why}")


if __name__ == "__main__":
    sys.exit(main())
