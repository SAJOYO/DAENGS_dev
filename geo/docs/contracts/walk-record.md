# 산책 기록 계약 (outbound)

이 레포가 **주는** 것. [dog-profile.md](dog-profile.md) 가 "남이 주고 우리가 소비"라면 이건 그 반대다 —
우리가 수집한 사실을 남(케어 밸런스 · 서술 · 보행체크 · 수의사 리포트)이 소비한다.
소비자가 이 레포 안에 있을 수도 바깥 팀원일 수도 있다. 어느 쪽이든 **이 계약만 본다.**

근거: [결정 — 병원은 산책의 모드, 산책이 척추](../decisions/2026-08-22-walk-as-spine.md) 7절,
[연구 — 산책 데이터 신뢰도 등급](../research/2026-08-19-walk-data-evidence.md).

## 원칙

1. **사실만.** `walk-data-evidence.md` 의 "넣음" 등급만 필드가 된다. 시간 · 거리 · 속도 · 정지. 전부 폰 GPS 로 ±2~5%.
2. **의미 없음.** 목표 대비 · 보상 · 점수 · 레벨 · 트리거 · 권유 · 서술 — 이 계약에 없다. 그건 소비자가 자기 쪽에서 얹는다.
3. **텍스트 없음.** 사용자에게 보여줄 문장 필드가 없다. 문자열은 식별자뿐이다.
4. **버전.** `record_version`은 형태, `calculation_version`은 계산 정책을 식별한다.
   필드나 문턱값이 달라지면 해당 버전이 오른다.
   새 응답은 record/calculation v4이며, purge된 기존 record v2·v3는 읽기 호환한다.

코드: `app/features/walk/models.py`. 필드 집합과 금지 이름은 `tests/test_walk_contract.py` 가 고정한다 —
필드 하나를 더하면 테스트가 깨지고, 깨뜨리는 것이 **보이는 결정**이 된다.

## 형태

```
WalkFix      수신 원본 한 점. 앱이 배치로 올린다
  client_seq    세션 안의 클라이언트 측정 순서. 재전송 안정 키
  chain_index   명시적 pause/resume 뒤 증가. 다른 chain 사이에는 segment가 없다
  at            tz 필수
  lat, lng
  accuracy_m    있으면. 필터 재료
  is_mock       재생·가짜 위치 표식. Android DEVICE/MOCK 구분의 서버 짝 — 개발 재생이
                진짜 산책 사실처럼 쌓이지 않게 한다. 사실 필드가 아니라 표식이다

WalkSession  세션 하나
  id, dog_id
  started_at, ended_at     ended_at 없으면 진행 중
  fix_count
  state                   open → sealed → derived → purged
  evidence_origin         device | mock | unknown (새 세션은 mixed 거부)

WalkFacts    세션이 끝난 뒤 코드가 계산한 사실. 이게 바깥에 나가는 것
  record_version
  calculation_version
  session_id, dog_id
  evidence_origin
  started_at, ended_at
  duration_s
  distance_m               원 GPS 누적. 정지 중 지터 포함 — 참고값
  moving_distance_m        속도 임계 이하 구간을 0 으로 — **"거리"는 이것이다**
  moving_s
  stop_count, stop_s       "정지"까지만. 냄새 맡기로 단정하지 않는다 (evidence C절)
  avg_speed_mps            moving 기준
  fix_count

MotionEventOccurrence   원좌표를 지우기 전에 확정한 공간 상태 변화
  session_id, event_index
  type                   현재는 stop만. 횡단보도·냄새 맡기 같은 이유는 붙이지 않음
  started_at, ended_at, duration_s
  lat, lng               정지 구간 관측점의 중심
  route_offset_m         이 세션의 이동거리 기준 위치. 교차 세션 ID가 아님
  accuracy_p50_m, fix_count
```

FacilityEncounter   동선 주변에 시설 좌표가 있었다는 관측 — 기하값까지만
  session_id, event_index
  occurrence_version, occurrence_index
                                  같은 시설도 연속 진입마다 별도 행. v1 집계행은 복원 불가
  entered_at, exited_at           20m 원 안에서 관측된 시간 구간
  entry_observed, exit_observed   원 경계를 실제 통과했나. 수집 시작·gap이면 false
  entered_offset_m, exited_offset_m   방향 있는 segment 열 위의 진입·이탈 위치
  facility_source, facility_ref   시설 안정 키. facility.id 아님
  kind, lat, lng                  대표점(건물 중심)이지 출입구가 아니다
  place_active                    의료 오버레이 상태 — 필터가 아니라 데이터. 폐업 앞을 지나는 것도 사실
  as_of
  min_lateral_m, offset_m         동선이 가장 가까웠던 거리와 그 지점의 동선상 위치
  dwell_s_10m/15m/20m             판정 원 후보 3개의 체류 시간. 원좌표는 지워지므로
                                  반지름 선택(실측 후)을 위해 밴드를 전부 저장한다.
                                  **occurrence_version <= 2 행은 옛 밴드(10/30/50m)다** —
                                  15m·20m 칸에 30m·50m 원의 값이 들어 있다. 밴드를 좁힐 때
                                  컬럼을 rename 했고 원좌표는 이미 purge 돼 재계산이 안 된다.
                                  버전을 안 보고 읽으면 틀린 반지름의 답을 얻는다
  pass_count                      v2 occurrence는 항상 1. v1 집계행에서만 세션 진입 합계
  stop_overlap_10m/15m/20m, stop_s_10m   같은 시간 구간의 원 안 정지 이벤트 겹침
  accuracy_p50_m                  20m 원 안 관측 정확도 — 판정 가능성의 근거

"지나쳤다/봤다/들렀다"는 여기 없다. 그 판정은 `app/features/scene/judgment.py` 가 규칙표
(JUDGMENT_VERSION, 상수 잠정)로 한다 — 사실은 안 바뀌고 판정 상수만 바뀔 수 있게
층을 가른 것. 틱은 GPS 점이고 원 경계 통과는 선분 위 보간이라 체류가 점 간격보다 정밀하다.
판정은 현재 버전의 occurrence만 받는다. 원좌표 삭제로 분할할 수 없는 v1 집계행과, 밴드가
달라 값의 뜻이 바뀐 v2 행은 읽기 호환만 하고 `unjudgeable`로 격리한다.

동선 계산의 최소 방향 계약은 시간순 `Segment(a → b)`다. 명시적 pause/resume와
gap·jump·거부 지점은 별도
연속 chain으로 끊고, occurrence는 **한 chain에서 20m 원과 겹치는 최대 연속 시간구간**이다.
같은 시설을 왕복하면 서로 다른 occurrence 두 행이 된다. `Leg`·corridor는 이 관측 계약의
선행 조건이 아니며, 가는 길/오는 길 또는 교차 세션 학습이 필요할 때 별도로 정의한다.

## 수집 API (2026-08-24)

```
POST /walk/sessions                시작. id 는 클라이언트 생성(UUID) — 오프라인 시작 + 멱등 재전송
POST /walk/sessions/{id}/fixes     배치 업로드 → 저장/중복 계수. client_seq 재전송은 멱등
POST /walk/sessions/{id}/finish    종료 → WalkFacts 확정. 멱등 — 확정된 사실은 재계산하지 않는다
GET  /walk/sessions/{id}           세션 + 사실
```

**fix 는 세션이 살아 있는 동안만 서버에 있다.** finish는 세션 행을 잠가 업로드와
직렬화하고 `OPEN → SEALED → DERIVED → PURGED`를 한 트랜잭션에서 수행한다. 집계와
`MotionEventOccurrence`가 확정된 뒤에만 원좌표를 지운다. 다음 파생 소비자(facility
encounter)는 PURGED 앞 DERIVED 단계에 들어와야 한다. 실패하면 트랜잭션 전체가
롤백되므로 파생 전 삭제 상태는 생기지 않는다.

서버 계산 정책 v4 (`app/features/walk/facts.py`): 명시적 chain 단절 · 이동 임계 0.5m/s ·
정지 최소 10초 · accuracy 50m 컷 · 200m 튐 단절 · 60초 수집 공백 단절. 문턱값은 실기기 반복 측정
전의 잠정값이다. 결과에 `calculation_version`을 남겨 정책 변경 전후를 섞지 않는다.
Android 미리보기 동기화는 앱 수집기가 이 계약을 채택할 때 같은 버전으로 구현한다.

`is_mock`은 단순 계수로 숨기지 않는다. 세션의 첫 배치가 `evidence_origin`을 정하고
device/mock 혼합 업로드는 거부한다. 재생 세션도 계산은 가능하지만 결과가 `mock`으로
명시되므로 실제 baseline과 EventAnchor 소비자는 제외할 수 있다.

## 예정 — 저장 정책이 선 뒤

저장 **층**은 결정 #57 에서 정해졌다 ([문서](../decisions/2026-08-25-walk-data-retention.md)) —
연속 궤적은 finish 이후 남기지 않고, 무좌표 집계는 장기 보관 후보, 좌표 동반 층은 기본이 짧다.
아직 없는 것은 각 층의 **일수**와 사용자에게 보이는 삭제 표면이다. 아래 항목들은 그 뒤다.

- 궤적 · 재방문 · 구역 — evidence 는 "넣음"이지만 전체 궤적을 재처리하려면 저장 정책이
  필요하다. 시작·종료 좌표는 집 주소다. 절삭 · 지연 저장 · 보관 기간
  ([backlog](../backlog.md))이 먼저다. 그전에는 finish 트랜잭션 안의 승인된 파생 사실만 남긴다
- 평소 대비 % — 가장 믿을 만한 지표지만 baseline 이 있어야 한다. 2~4주 뒤. 그때 `WalkBaseline` 로 별도
- 이용 과정의 데이터(어디서 뭘 찾았나) — 같은 이유로 저장 정책 뒤

## 안 주는 것 (다시 안 물으려고)

| | 왜 |
|---|---|
| 칼로리 | 걷기 MAPE 35%. 숫자로 내면 거짓말 |
| 걸음 수 | 폰은 사람 걸음. 소형견 보폭 3~4배 |
| 냄새 맡기 | 검증된 행동 분류 8개에 없음. 목걸이가 있어도 |
| 계단 · 육교 | TMAP 이 안 준다 — 288경로 0 ([조사](../research/2026-08-22-tmap-option-survey.md)) |
| 목표 대비 · 보상 · 권유 | 사실이 아니라 판정. 소비자의 일 |
| 서술 · 에피소드 | 옵션. 소비자의 일 |
