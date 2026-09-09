# 행동 기록으로 산책 비교

같은 반려견의 평소 산책과, 선택한 행동을 기록한 산책의 공간 분포를 함께 조회한다.
개별 행동의 위치는 핀으로 확인한다. 산책 전체의 도색은 그 행동이 일어난 장소나
시설 방문의 증거가 아니며, 행동 확률·선호·성격을 계산하지 않는다.

## 선택과 계산

- A: 소유한 반려견·기간·맥락 조건에 맞는 산책당 대표 Cellophane 중 occupancy 합이 양수인 산책.
- B: A 중 같은 반려견에게 귀속된, 삭제되지 않은 선택 행동 기록이 하나 이상 있는 산책.
- 위치 없는 행동도 B를 선택하는 근거다. 한 산책에서 여러 번 기록해도 원판은 한 번만 포함한다.
- A와 B 모두 `walk_utilization`을 사용한다. 각 원판의 시간량을 먼저 정규화한 뒤 산책마다 같은 무게로 평균한다.
- B 밖의 산책은 해당 행동의 **기록이 없는 산책**이다. 행동을 하지 않았다고 판정하지 않는다.

기존 공간 일기와 대표 원판 선택·검증·조회 상한을 공유한다. 요청의 `since`와 `until`은
서울 시간대의 날짜이며 양끝을 포함한다. 반려견 소유권 확인, A·B·행동·핀 읽기를
하나의 읽기 전용 repeatable-read 트랜잭션에서 수행한다. 별도 프로필을 저장하지 않는다.

## HTTP 계약

`POST /app/walks/spatial-diary/behavior-comparisons/query`

기존 앱 Bearer 인증을 사용한다. 요청 예:

```json
{
  "comparison_version": "walk-behavior-comparison-v1",
  "walk_selector": {
    "pet_id": "00000000-0000-0000-0000-000000000001",
    "since": "2026-08-11",
    "until": "2026-09-09",
    "context_facets": []
  },
  "behavior_code": "sniffing"
}
```

`behavior_code`는 `sniffing`, `excretion`, `barking` 중 하나다.

| 응답 | 의미 |
| --- | --- |
| `spec`, `projection` | 적용한 요청과 공통 격자·도색 세대 |
| `baseline.walk_ids`, `baseline.field` | A와 그 공간 분포 |
| `matching.walk_ids`, `matching.field` | A의 부분집합 B와 그 공간 분포 |
| `summary.selected_walk_count` | 맥락 필터를 적용한 뒤 선택한 대표 원판 수 |
| `summary.excluded_empty_walk_count` | 시간량이 없어 A에서 제외한 원판 수 |
| `summary.entry_count`, `recorded_day_count` | 근거 기록 수와 서로 다른 서울 기준 기록 날짜 수 |
| `summary.unlocated_entry_count` | 현재 `pin.point`가 없는 근거 수 |
| `evidence` | 기존 `EvidenceV2` 필드에 원본 산책 연결용 `client_session_id`를 추가한 목록 |
| `receipt` | `source_revision`, 조회 시각, 도색 지문, 맥락·집계 정책 버전 |

각 `field`의 `denominator`는 해당 `walk_ids` 수와 같다. A가 비면 두 지도와 근거 모두
빈 결과다. B가 비면 A만 표시할 수 있다. A=B도 유효한 결과이며 차이가 있다고 해석하지 않는다.

핀은 기존 v2 응답 변환을 재사용한다. sidecar가 없는 기존 기록의 위치도 이 변환에서 처리한다.
클라이언트는 응답의 `pin.point`만 지도 위치로 사용한다. 이 값이 없으면 원본 `location`으로
대체하지 않고 위치 없는 기록으로 보여준다.

## 변경 반영과 상한

`source_revision`에는 요청·선택 정책, 선택한 분석 ID·원판 내용·도색 지문, 산책 집합,
현재 기록·핀 revision과 근거 내용을 반영한다. 조회 시각은 지문에 넣지 않는다.
삭제·귀속 변경·핀 정정·원판 재분석은 다음 조회에 반영된다. 이 지문은 현재 근거를
구분하는 값이며 삭제된 데이터를 복원하거나 과거 보고서를 재현하는 저장소가 아니다.

공간 일기의 후보·선택 산책·셀 상한에 더해 행동 근거는 최대 2,000건이다.
초과하면 일부만 잘라 반환하지 않고 `413`으로 기간 축소를 요청한다.
다른 도색 세대의 혼합은 `409`, 소유하지 않은 반려견은 `404`다.

## 실행 조건과 확인 경계

기존 `DAENGS_WALK_ENTRY_V2_ENABLED`가 켜진 서버에서 제공한다. 꺼져 있으면 명시적으로
`404`를 반환한다. 이 작업은 설정을 변경하지 않는다. 새 테이블·migration·수집기·상시
재계산 작업을 추가하지 않으며 기존 공간 분석과 행동·핀 저장 자료를 읽는다.

APP의 행동 비교 화면을 사용하려면 이 API가 포함된 서버가 배포되어 있어야 한다.
코드 병합과 서버 배포·설정 적용은 별개의 단계다. 서버가 준비되지 않은 경우 앱은
준비 안내를 표시한다. 실제 DB 적재·서버 배포·실기기 지도 표시는 로컬 계약 테스트의
검증 범위에 포함되지 않는다.

관련 계약: [공간 일기 API](spatial-diary-api.md),
[행동 기록과 프로필](entries-and-record-profile.md), [행동 핀](action-pin-context.md).

## 로컬 확인 — 2026-09-09

`backend/`에서 비교 집계·HTTP·SQL 구성의 새 테스트 14개와 공통 공간 조회 회귀 21개를
확인했다. skip은 없다. 최초 결합 실행에서 SQL 컴파일 테스트의 bind 이름 기대가 실패해
그 기대를 수정하고 새 비교 파일 14개를 다시 실행했다. 기존 공간 조회 21개는 결합 실행에서
통과했다. 변경한 Python 7개 파일의 Ruff 검사와 형식 검사도 통과했다.

```powershell
uv run pytest -q tests/walk/diary/test_behavior_comparison.py tests/walk/diary/test_spatial_diary_query.py
uv run pytest -q tests/walk/diary/test_behavior_comparison.py
```

테스트용 저장소 대역으로 실제 서비스·라우터를 호출해 직렬화한 응답을 APP의
`app/src/test/resources/walk_behavior_comparison_response.json`에도 사용했다.
예제 원판의 셀 좌표는 계산 검증용 합성 자료다. 실제 핀과 지도 위치의 시각 검증 자료가 아니다.
