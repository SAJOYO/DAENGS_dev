"""산책 — **수집한다.** 판정 · 보상 · 서술 · 알림은 하지 않는다.

이 패키지가 만드는 건 `WalkFacts`와 좌표가 붙은 `MotionEventOccurrence`까지다
(models.py, docs/contracts/walk-record.md). 그 위에 무엇을
얹을지 — 케어 밸런스, 개의 목소리, 출발 전 목표, 응급 모드 — 는 전부 이 사실을 **소비하는** 별도 결정이고
전부 옵션이다. 이 레포 안에 생길 수도, 바깥 팀원이 만들 수도 있다.

왜 이렇게 좁게 잡나: 병원 검색에서 남의 데이터 위에 판정을 세웠다가 데이터가 못 받쳐 무너졌다
(docs/decisions/2026-08-22-walk-as-spine.md). 산책은 데이터가 넘치니 이번엔 반대 위험 — 메커니즘을 데이터
없이 설계하는 것 — 을 막는다. 수집이 먼저 돌아야 "목표 있는 산책 vs 없는 산책"을 비교할 수라도 있다.

아래 이름은 이 패키지의 어떤 모델에도 들어오지 않는다. tests/test_walk_contract.py 가 지킨다.
"""

# 사실이 아니라 의미인 것. 필드 이름에 이 토큰이 보이면 경계를 넘은 것이다.
OUT_OF_SCOPE_TOKENS: tuple[str, ...] = (
    "goal", "target", "reward", "score", "level", "xp", "streak", "badge",
    "trigger", "advice", "warn", "message", "reply", "narration", "episode", "story",
    "calorie", "kcal", "step",          # 근거 없음 (walk-data-evidence.md)
    "sniff",                            # 검증된 행동 분류에 없음
    "stairs", "overpass",               # TMAP 이 안 준다 (tmap-option-survey)
)
