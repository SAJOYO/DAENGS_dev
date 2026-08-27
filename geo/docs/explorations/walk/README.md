# 산책 — 탐색 갈래

**담당: 사용자.**

코어는 탐색이 아니라 계약이다 — [`contracts/walk-record.md`](../../contracts/walk-record.md), `app/features/walk/models.py`,
`tests/test_walk_contract.py`. 수집 → `WalkFacts`. 의미 없음.

`session-engine-draft`는 초기 세션 흐름과 소비자 아이디어를 함께 적었던 초안이다. 이후 수집 코어의
상당 부분(start/fixes/finish, Android foreground tracking, Room 원본 저장)은 구현됐고, 트리거·서술
절과 `loop-and-balance`는 산책 사실을 **소비하는 옵션**으로 parked다.

| 갈래 | status | implementation | 한 줄 |
|---|---|---|---|
| [session-engine-draft](session-engine-draft.md) | exploring | draft | 초기 start/locations/finish 흐름과 소비자 아이디어. 구현 현황은 Android README/계약이 더 최신 |
| [session-continuity-and-dwell](session-continuity-and-dwell.md) | exploring | partial | 이미 있는 pause/chain/unfinished-session 위에서 세션 연속성·복구·체류·territory 의미를 정리 |
| [territory-paint](territory-paint.md) | exploring | working-skeleton | 산책 점을 붓으로 지도를 칠한다. 산책 한 번 = 셀로판 한 장, 조건으로 골라 겹친다. §A(영구 형태)는 [결정 #69](../../decisions/2026-08-26-walk-permanent-spatial-form.md) 로 닫혔고 §B·§C 는 열려 있다 |
| [evidence-layer](evidence-layer.md) | exploring | draft | **원시 행동 → 판단 가능한 상태.** 사람과 AI 가 같은 근거 계약을 다른 표현으로 읽는다. 지도는 그 인간용 투영이고 맨 마지막이다 |
| [experience-scenario](experience-scenario.md) | exploring | draft | 저녁 산책 직전 화면 한 장. 셀로판이 기록·해석·행동 중 **어떤 가치를 만드는지** 가장 싼 형태로 검증한다. 금지 목록과 판정 기준이 여기 있다 |
| [drawn-region](drawn-region.md) | parked | working | 사용자가 면을 그리고 그 안의 체류를 잰다. 붓 모델로 대체 — 5배 규칙은 남는다 |
| [loop-and-balance](loop-and-balance.md) | parked | none | 3단 루프 · 케어 밸런스. 데이터 수집 전에는 결정하지 않음 |

`territory-paint`는 서버가 확정한 `Segment[]`를 소비해 공간을 칠하는 갈래다. 셀 격자는
`app/geo/cells.py`로 `anchors.py`·Android `LocalHexCellIndexer`와 공유한다. 무엇을 영구히
남길지(단순화 궤적 vs 산책별 셀 맵)는 아직 열려 있고, 실사용 업로드는 #57·#58이 막고 있어
지금 고를 필요가 없다.

`session-continuity-and-dwell`은 이 수집/paint 사이의 **제품 의미**를 정리한다. 한 사용자 산책 안에
여러 client/derived continuity chain이 있을 수 있고, chain 사이 관측 공백은 붓으로 연결하지 않는다.
또 현재 `occupancy`가 이미 시간 가중 spatial exposure라는 점을 명시하고, 반복 방문(`walks`)·공간
노출(`occupancy`)·실제 정지 사건(dwell)을 하나의 "진함"으로 섞지 않는 방향을 검토한다.

병원 찾기 쪽에서 나온 것 중 산책에도 쓰이는 것: 반경 검색(`app/geo/search.py`), 단발 경로 스냅샷(`app/journey`).
대화로 조건 편집하는 루프(`../hospital-search/refine-loop.md`)는 parked — 편집할 조건이 없었다.
