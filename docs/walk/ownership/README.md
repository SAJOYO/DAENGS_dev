# 산책 기능의 소유권과 의존 경계 — 0단계

0단계 조사 기준은 DEV `8e3d1589`, 현재 목록은 `159067b7` 위의 #531(7단계) 변경을 반영한다. 조사·갱신일은 2026-09-14다.
이 문서는 **남은 구조 변경의 범위를 고정하는 조사 결과**이며 리팩토링 전체 완료 선언이 아니다.
0단계는 조사만 수행했다. 1단계는 B1·B2·B3의 공급 계약/일기 어댑터를 분리하며 DB·공개 계약·정책은 유지한다.

## 먼저 읽을 결과

- [파일별 소유권 표](files.md): 범위 내 720개 파일, 소유 영역·현재 계층·처리·근거·부채.
- [직접 import 판단표](crossings.md): 제품 소스의 교차 영역 관계 354개. 측정 내부 계산/조회 결합 C1은 제거됐다.
- [기계 판독 목록](inventory.json): 위 파일 목록, 1,714개 직접 로컬 import 관계, 판단과 관련 영역.
- [범위 재검사 도구](../../../tools/check_walk_ownership.py): 새 파일·삭제·import 변경·미분류 판단 검출.

범위에 속한 **핵심 Python 265개**, 직접 import 접점 162개, 검증 자산 227개,
SQL 접점 52개, 문자열로 확인한 UI/운영 접점 8개, 명명된 도구 4개, 명시한 통합 접점 2개다.
각 파일은 발견 사유 하나로만 집계한다. 720개를 모두 제품 구현 파일이라고 세지 않는다.
서비스 루트의 `walk.py`·`walk_*.py` **46개는 모두 포함**했다.

354개 관계는 기능 소비·계층 조립·공통 계약을 보존하는 대상으로 분류했다.
0단계에서 선언한 부채 A1/A2·B1/B2/B3·C1·D1/D2/D3·E1/E2는 각 단계에서 구현·직접 검증했다.
이 범위에서 미해결로 기록된 항목은 0개지만, 모든 산책 기능의 품질/실환경 검증 완료를 뜻하지 않는다. 이는 코드의 결함 개수나 필요한 PR 개수가 아니다.
동일 원인의 여러 import가 별도 행으로 기록된다. `retain-interface`는 **현재의 기능 소비
관계를 유지**한다는 뜻이며 비공개 함수까지 영구적인 API로 승인하지 않는다.

6단계는 [배경 공급 패키징](../background-package.md)이다. 루트 13개 파일은 동일 공개 객체를 제공하는 호환 경로로 남고, 실제 구현은 `walk_background`가 소유한다. 기록 outbox/재수집 조정은 7단계에서 기록 트랜잭션과 함께 배치했다. 정적 import가 보지 못하는 지연 호환 대상은 [명시 목록](../background-package.json)과 동일 객체 테스트로 별도 검증한다.

7단계는 [기록·사진 패키징](../records-package.md)이다. 기록 8개 구현은 `walk_records`, 사진 동기화는 `walk_photos`로 배치하고, 옛 9개 파일은 [공개 이름 호환](../records-package.json)만 남겼다. 기록별 context/backfill의 소유권 분류도 background에서 records로 구체화했으며, ORM·DAO·schema·CLI 파일은 이동하지 않았다. 두 작업을 연결하는 Celery 모듈은 통합 접점으로 분류한다.

## 범위가 어떻게 정해졌는가

파일 이름만으로 완료를 판단하지 않도록 다음 합집합을 사용한다.

1. 추적 중인 `backend/src/daengs_walk/**/*.py` 전부.
2. backend의 모든 계층에서 `walk.py`·`walk_*.py`, `services/walk_diary/**`·`walk_generation/**`·`walk_legacy/**`·`walk_artifacts/**`·`walk_background/**`·`walk_records/**`·`walk_photos/**`,
   일기 그래프 `orchestration/diary.py`. 활동·어시스턴트의 `walk` 접점도 포함한다.
3. 위 핵심 코드가 직접 import하는 로컬 파일과, 핵심 코드를 직접 import하는 모든 로컬
   Python 호출자. 함수 내부·상대 import·별칭·TYPE_CHECKING import도 포함한다.
4. `backend/tests/walk/**`, `backend/evals/walk*`·`diary*`의 테스트·표본·산출물,
   이름에 walk/diary/trajectory/motion/measurement가 들어가는 도구·평가 파일.
5. walk 테이블을 언급하는 `db/**/*.sql`, 산책 API/점검기를 연결하는 frontend와
   workflow/옛 CI 파일, 명시한 config·main·compose·nginx 접점.

각 분류는 **책임의 소유자**이며 최종 디렉터리 이름을 확정하는 것이 아니다. 파일 안에
다른 책임이 섞인 경우 `split`과 부채 번호를 기록한다. 모델·라우터·저장소를 모두
서비스 폴더로 이동하거나 파일별로 새 서비스/런타임을 만드는 계획이 아니다.

### 포함하지 않는 것과 검사 한계

- APP·GEO는 별도 저장소다. 이 목록은 **DEV 전체에서 선언한 산책 범위**의 목록이다.
  Kotlin 소비자·GEO 정책 전체를 검토하거나 수정했다는 뜻은 아니다. 측정 표본과 경로
  정책의 저장소 간 호환은 후속 단계의 검증 접점으로 남긴다.
- 공통 인증·설정·활동·Life 어댑터 같은 파일은 산책과 만나는 **직접 접점만** 포함한다.
  그들의 의존성을 끝없이 따라가 다른 제품 전체를 산책 소유로 분류하지 않는다.
- Python AST는 정적으로 적힌 import를 찾는다. 문자열 동적 import, subprocess 안의
  Python, callback 주입, HTTP·Celery task 이름·SQL 실행·잠금은 실행 그래프가 아니다.
  아래 별도 접점 표를 함께 유지한다. 추적되지 않은 개인 스크립트/환경 파일은 읽지 않는다.
- 파일 분류/직접 import 재검사의 통과는 이 범위의 목록 일치만 뜻한다. 모든 함수의
  품질·실환경 동작·의존성 제거 완료를 증명하지 않는다. 부채를 허용 목록에 숨기지 않는다.

## 소유 영역과 바깥에 남기는 이유

| 영역 | 소유하는 것 | 다른 영역과의 관계 |
| --- | --- | --- |
| 원본 기록·봉인 | GPS 업로드, recording eligibility, motion/precision 보조 입력, 봉인 트랜잭션 | 측정/산출물 생성기를 호출하되 원본·잠금·커밋 권한은 봉인 서비스가 유지 |
| 측정·동선 | canonical 계산, motion 재생, 동선 투영, 측정 요약·페이지 | 검증된 불변 입력을 소비; HTTP 출력과 저장 조립은 같은 계산 결과에서 각각 수행 |
| 한 산책의 봉인 산출물 | capsule·cellophane 생성, 분석 산출물의 직렬화/복원 | 봉인에서 생성되고 공간 조회가 소비함. 여러 산책 집계와 같은 책임이 아님 |
| 공간 조회·집계 | 봉인된 원판 조회, 조건별 여러 산책 집계 | 원본 기록·측정·일기를 다시 생성하거나 고치지 않음 |
| 사용자 기록 | 메모·행동 핀 수정/삭제, CAS, 위치 출처 검증 | 일기 입력 공급자. 배경 outbox 예약·완료·재수집은 기록 소유이며 변경과 원자성을 유지 |
| 사진 | manifest CAS·재시도 해시·삭제 기록 | 일기 입력 공급자. B3에서 공용 값/해시 계약을 직접 소비하도록 분리 |
| 배경 공급·카탈로그 | 원본 관측·페이지 보존·갱신·예산·공급자 수집 | 일기와 기록이 함께 소비. 일기 `AreaInput` 변환/장면 채택은 공급자에 넣지 않음 |
| 현재 일기 | 재료 해석, 장면 선정, 카드/행동/활동/제목 작성, 발행 | 원본·사진·배경·측정을 소비. 기존 그래프와 공통 실행기를 사용 |
| 과거 형식 지원 | storyboard 후보 v1~v5, 지원 중인 bundle/slot 실행·읽기 | 현재 정책을 암묵적으로 소유하지 않음. 지원 폐지는 이번 계획이 아님 |
| 공통 생성 상태 | 생성 번호·입력 버전·lease·재사용·완료와 공유 저장 행 | 현재와 과거가 같은 상태 전이를 사용; 복제하지 않음 |
| 공용 계약 | 실제로 같은 의미인 값·좌표·정규 직렬화 | 소비자 전용 정책 없음. 모든 digest/좌표 모델을 무조건 하나로 합치지 않음 |
| 통합 접점 | 인증·활동 게임·어시스턴트·공통 실행기·배포·UI | 각 제품/계층 소유 유지. 산책과 만나는 호출 계약·잠금 순서를 관리 |
| 검증·도구 | 테스트·고정 표본·실험·운영 점검 | 제품 패키지 밖에 유지; 소비하는 영역과 명시적 실행 경로를 추적 |

## 제거할 결합과 보존할 계약

B1·B2·B3은 **#521**, A1·A2는 **#522**, D1·D2·D3은 **#524**, C1은 **#526**, E1·E2는 **#529에서 구현·직접 검증**했다. 아래 표는 원래 문제와 보존 계약을 함께 남긴다.

| ID / 단계 | 정리 전 결합 / 근거 | 변경 방향 | 반드시 보존할 것 |
| --- | --- | --- | --- |
| B3 / 1 · #521 구현 | `walk_photo.transition` 및 사진 schema, area/park/river 카탈로그 → `diary.contracts.input` | 사진 요청 digest와 실제 공용 값의 독립 계약 추출. 이름이 digest인 함수를 전부 합치지 않음 | 키 정렬·공백·유니코드·숫자·datetime/model 직렬화·기존 요청 해시, 같은 revision 재시도/충돌 판정 |
| B2 / 1 · #521 구현 | `walk_weather_context` → `diary.slots.temperature.GridTemperature`·`Point` | 시간/좌표/공급자에 묶인 관측 자료형은 공급 계약으로, `temperature_candidate`의 장면 일치·나이 제한은 일기에 유지 | 과거 hourly 관측 검증, query/request/fetch 시각, 날씨 채택/제외 이유와 저장 payload |
| B1 / 1 · #521 구현 | `walk_space_catalog_input.retain_page/retained_fields/normalization_input` | 원자료 보존/검증은 카탈로그, `AreaInput` 구성은 일기 수집 어댑터로 분리 | pagination/metadata·잘못된 행·충돌 행 보존, 원자료 hash, coverage 검증. 요약 결과에서 원자료 복원 금지 |
| A1 / 2 · #522 구현 | `diary.route.binding/observations`, `diary.slots.sources` → `storyboard_input.route_nodes` | 연속 경로/원본 관측 주소를 공통 계산으로 분리; 과거 `scene_inputs`는 호환 소유 | 끊긴 경로 block, 실제 GPS 출처, moving 거리 누적, 장면/관측 식별자 |
| A2 / 2 · #522 구현 | `diary.selection.board`·`route.observations` → `storyboard_selection` | 거리/빈 구간 계산은 공유; 상대 속도 관측 정책은 소유자와 명시 파라미터/버전을 구분 | 기존 기준 속도·비율·지속 시간·후보 순위·동률 처리·미선정 구간. 정책 통합 금지 |
| D1 / 3 · #524 구현 | `lifecycle.generation.generate_diary`의 `CardWritingResult` 타입에 따른 완료기 선택 | 실행 전에 작성기·완료기·기대 결과 계약을 함께 결정하고 결과를 검증 | provider 대역 주입이 전략을 바꾸지 않음, 현재/과거 지원 계약, 수집 적용·마감·실패 fallback |
| D2 / 3 · #524 구현 | `services.walk_storyboard`·`schemas.walk_storyboard`의 현재/과거 분기 | HTTP 협상 진입과 과거 실행/응답 소유권을 분리 | 기존 URL·지원 format·협상 결과·공개 JSON. **같은 board format에도 현재/과거 슬롯 경로가 있으므로 format만으로 전략을 결정하지 않음** |
| D3 / 3 · #524 구현 | `walk_storyboard_state`의 `reusable/reserve/complete` | 공통 생성 상태로 이름/소유권 명시; 모델·DAO 계층과 공유 행 유지 | generation/input revision/lease 비교, 원본 재확인, 중복/늦은 결과 차단. 현재/과거 상태 복제 금지 |
| C1 / 4 · #526 구현 | `walk_measurement.project` → `walk_trajectory._project` → JSON → `TrajectoryCalculation` 재파싱 | 검증된 공통 투영 결과 → 조회 응답 직렬화 / 측정 요약·페이지 조립으로 분리 | measurement ID·응답 바이트·페이지 hash·원본 시각·정밀도·순서·예외 계약, 계산/직렬화의 off-lock 실행 |
| E1 / 5 · #529 구현 | `walk_analysis`가 측정 모델과 셀로판 직렬화를 함께 제공 | 실제 사용자를 기준으로 한 산책의 산출물 생성/저장 어댑터 경계 정리 | WalkAnalysis와 capsule의 1:1 바인딩·cellophane 봉인 형식·복원 검증. DB 분할/스키마 변경 없음 |
| E2 / 5 · #529 구현 | `daengs_walk.__init__`에서 측정·capsule·cellophane·spatial_diary를 함께 import | 소유 영역의 명시 진입과 기존 공개 이름 호환 범위 정의 | 공유 타입 객체와 기존 사용자 import의 호환, 불필요한 제품 기능의 암묵 로딩 점검 |

공용 계약 추출에도 기존 저장 계약의 의미가 다르면 **독립 버전을 유지**한다. 예를 들어
사진 요청 hash와 원본 GPS fingerprint가 둘 다 SHA-256이라고 같은 직렬화로 바꾸지 않는다.
좌표도 공통 `Point`라는 이름만으로 원본 비트·소수점 정밀도 계약을 통일하지 않는다.

### 같은 속도 정책이라고 합치면 안 되는 두 경로

`diary.route.observations`가 소유하는 `OBSERVATION_PACE`는 느림 0.5배 미만,
빠름 1.75배 초과, 지속 20초 이상이다. #508의 `diary.route.movement_policy.MovementPolicy`
기본값은 느림 0.5배, 빠름 1.5배, 최소 10초다. 전자는 장면의 관측 후보 공급, 후자는
새 이동 재료/장면 결합 정책의 경로다. `movement.py`·`slots/movement.py`·`board/activity.py`·
`model_input.py`·`model_materials.py`도 목록에 포함했다. 두 결과의 통합/우선순위 변경은
기획 변경이므로 이번 의존성 정리와 섞지 않는다.

## import 밖의 실행·저장 접점

| 연결 | 실제 접점 | 유지/후속 확인 |
| --- | --- | --- |
| HTTP 형식 협상 | `routers/walk_storyboard` → `services/walk_storyboard` → `walk_diary.api` | D2에서 같은 요청/저장 형식 협상을 보존. 라우터의 provider 대역이 작성 전략을 바꾸지 않음 |
| 모델/수집 주입 | `runtime.write_board/write_cards`, `api`, `lifecycle.generation`, `orchestration/diary` | 함수 인자/반환 계약이어서 import만으로 완성 경로를 증명하지 못함. D1에서 현재·과거의 명시 계약 검사 |
| 기록 outbox | `walk_entry`·`walk_entry_v2`가 `walk_entry_context.reserve*` 호출 | 기록 revision 변경과 작업 예약의 동일 트랜잭션을 보존. 일기 생성이 기록을 쓰지 않음 |
| 배경 작업자 | compose worker/Beat → `tasks.walk_entry_context` → process/refresh task 이름 | 기록 queue와 catalog queue/lease/예산을 보존. task 이름은 정적 import 검사와 별도로 점검 |
| 봉인·게임 잠금 | `walk.finalize_walk`: 읽기 commit → 계산/날씨 → 게임 공통 잠금 → Walk 행 잠금 → 원본 재확인 → 산출물/활동 기록 → commit | 계산 순서뿐 아니라 잠금 순서·재시도·원자성을 보존. 카탈로그/일기 패키징에 끌어들여 재작성하지 않음 |
| GPS 보완·핀 출처 | `walk_recording` → entry-v2 repository, `walk_entry_pin` → chunk/원본 검증 | 기록 복원에 따른 기존 핀 출처 영향 확인. 서로 import한다고 양쪽 트랜잭션을 합치지 않음 |
| 공유 저장 행 | `models/repositories.walk_storyboard`, SQL 20 및 관련 migration | 현재/과거 bundle이 같은 행 사용. 모델/DAO를 과거 전용으로 분류하거나 행을 복제하지 않음 |
| 산출물 생성/집계 | `walk` → `walk_analysis/walk_capsule` → 봉인 행; `walk_spatial_diary` → 저장 산출물 조회 | 한 산책의 생성과 여러 산책 집계를 분리; 조회가 재계산/수정을 유발하지 않음 |
| 활동/어시스턴트 | `activity.record_walk`, `activity_core.walk`, `activity_walk_projection`, `walk_activity_context`, orchestration walk/life adapter | 각 소비자/게임 소유 유지, 측정 계약·잠금·허용된 요약 범위 확인 |
| 실환경 점검 | workflow/PowerShell → `tools/walk_runtime_smoke.py`, 점검 CLI; backend 시나리오 도구 | subprocess/HTTP/합성 원본 생성은 명시 실행. 이번 조사에서 실행하지 않았음 |
| APP/GEO | 측정 fixture·현재 HTTP 계약·동결 정책의 외부 소비 | DEV 내부 회귀만으로 Kotlin 실제 소비/실기기 또는 GEO 동등성을 완료로 보고하지 않음 |

## 후속 작업의 검증 지도

0단계에서는 아래 제품 테스트를 실행하지 않았다. 1단계 실행 결과는 아래 별도 기록을 따른다. 후속 변경의 실제 diff와 저장소
테스트 지침을 보고 해당 파일/selector를 선택한다. 전체 walk/전체 저장소 재실행을 미리
승인하거나 성공 건수로 고정하지 않는다.

| 단계 | 최소 직접 검증 후보 | 추가로 증명할 경계/빈틈 |
| --- | --- | --- |
| 1 사진 | `tests/walk/photos/test_walk_photo_input.py`, `test_walk_photo_db.py` | 공용 digest 전후 고정 hash·삭제 tombstone·동일 revision 재시도. 새 인터프리터에서 diary import 차단 |
| 1 날씨/카탈로그 | `tests/walk/context/test_walk_weather_context.py`, `test_walk_catalog_refresh.py`, `tests/walk/diary/test_diary_temperature.py`, `test_diary_temperature_db.py`, `test_diary_space_materials.py`, `test_diary_space_integration.py` | 관측 검증과 장면 채택 검증 분리, malformed/conflicting 페이지와 기존 hash 보존; background→diary 금지 |
| 2 계산/정책 | `tests/walk/diary/test_diary_observations.py`, `test_diary_base_selection.py`, `test_diary_route_patterns.py`, `tests/walk/storyboard/` | 현재 후보/새 이동/과거 선정의 고정 결과를 각각 검사; diary→storyboard 금지. 두 정책의 값 동일화 금지 |
| 3 생성/호환 | `tests/walk/diary/test_diary_writer_entrypoints.py`, `test_diary_writing_boundaries.py`, `test_diary_board_receipt.py`, `test_diary_board_db.py`, `test_diary_collection_db.py`, `test_diary_retention_db.py`, `tests/walk/storyboard/test_walk_storyboard_db.py` | 현재·과거 작성, 저장 판독, 틀린 결과 계약, 취소·마감 이후 반환·중복 생성·부분 성공을 분리해 검증 |
| 4 측정 | `tests/walk/measurement/test_stored_measurement.py`, `test_trajectory_shadow.py`, `tests/walk/api/test_trajectory_calculation.py`, `tests/walk/measurement/test_walk_measurement.py` | 기존 JSON 바이트/페이지 hash·긴 경로 표본·ID·예외 일치. 측정의 HTTP 응답 타입/직렬화 역참조 금지 검사 추가 |
| 5 봉인/집계/재배치 | `tests/walk/api/test_walk_finalize_db.py`, `tests/walk/measurement/test_walk_capsule.py`, `test_walk_analysis_storage.py`, `tests/walk/diary/test_spatial_diary.py`, `test_spatial_diary_query.py` 및 영향받은 직접 호출자 | 동시 봉인/원본 재확인/게임 잠금 순서와 rollback 범위의 기존 커버리지를 확인하고 부족한 경쟁 조건 검사 추가 |

DB 검사는 `docs/ci/README.md`의 **폐기용 loopback DB**를 사용하며 skip을 통과로 보고하지
않는다. 해시/응답이 달라지면 golden을 갱신해서 통과시키지 말고 의미 변화인지 먼저 판정한다.

## 1단계 구현·검증 기록 (#521)

- `daengs_walk.value_contracts`: 사진·배경·일기의 동일한 값/정규 JSON hash 계약.
  `DiaryContract`·`Point`·`Instant`·`digest`의 기존 일기 import는 같은 객체를 가리킨다.
  `daengs_walk.weather.GridTemperature`는 관측 검증만 소유하고, 기온의 장면 채택은
  기존 `diary.slots.temperature.temperature_candidate`에 남는다.
- `walk_space_catalog_input`은 보존/해시만 제공한다. 일기의 `AreaInput` 변환과
  소비 범위 확인은 `walk_diary.collection.catalog.normalization_input`으로 옮겼다.
- 사진·배경·공용 계약으로 분류된 소스의 함수 내부/상대 import까지 검사하고,
  10개 공급 모듈을 일기 import가 금지된 새 인터프리터에서 각각 로딩한다.
- `provider-contracts-v1.json`은 변경 전 `390ddd0c`의 코드에서 추출한 사진 요청·기온·
  공백/한글 메모의 JSON, JSON Schema, SHA-256과 일반 JSON hash 표본이다.
  변경 후 출력으로 기대값을 갱신하지 않았다.

실행일 2026-09-14. backend에서 `uv run --no-sync pytest -q`로 다음 묶음을 실행했다.
세 묶음의 경계 테스트 일부는 중복 실행이므로 건수를 합쳐 고유 테스트 수로 보고하지 않는다.

| 실행 묶음 | 결과 |
| --- | --- |
| `context/test_provider_contract_boundaries.py`, `photos/test_walk_photo_input.py`, `context/test_walk_weather_context.py`, `context/test_walk_catalog_refresh.py`, `diary/test_diary_temperature.py`, `diary/test_diary_space_materials.py`, `diary/test_diary_space_integration.py`, `diary/test_diary_domain_package.py` (모두 `tests/walk/` 아래) | 120 passed (최초 경계 테스트 10개) |
| `context/test_provider_contract_compatibility.py`, `photos/test_walk_photo_db.py`, `diary/test_diary_temperature_db.py` | 14 passed, skip 0. `127.0.0.1:55432/walk_pin_test`의 폐기용 PostgreSQL 17 사용 |
| 최종 `context/test_provider_contract_boundaries.py`(11개), `diary/test_diary_contract.py`, `diary/test_diary_board_contract.py`, `diary/test_diary_board_receipt.py` | 64 passed |

사진 DB 검사는 재시도·삭제·권한·동시 CAS·계정 삭제를 확인했다. 기온 DB 검사는
수집→작성기 전달→JSONB 저장→원자료 제거 이후 판독을 확인했다. 변경 파일 Ruff,
`uv run --no-sync check`, 목록 재검사도 통과했다. 전체 스위트·실환경 공급자·모델·실기기
검증은 실행하지 않았다. 1단계 종료 시 A1/A2·C1·D1/D2/D3·E1/E2가 후속 작업이었다.

## 2단계 구현·검증 기록 (#522)

- `daengs_walk.route.nodes`: canonical 연속 block, moving 거리 누적, 원본 GPS 관측 주소.
- `route.geometry`: 기존 거리 및 block별 미포함 구간 계산. 장면 개수/선정 정책 없음.
- `route.pace`: 기준 속도와 상대 속도 후보 계산. 기본 임계값을 숨기지 않고 호출자가
  최소 속도·표본 수와 `PacePolicy`를 명시한다. 현재 관측의 `OBSERVATION_PACE`와
  과거 선정의 `STORYBOARD_PACE`는 각각 독립적으로 소유한다.
- 새 이동 재료의 `MovementPolicy`와 `pace_claims`는 일기에 유지한다. 기존 관측과
  연속성/ID 구성 방식도 다르므로 이름이 비슷하다고 같은 알고리즘으로 합치지 않았다.
- 현재 일기 도메인의 `storyboard_*` import를 모두 제거했다. 과거 소비자는 공통 계산을
  사용하되 기존 `route_nodes`, `distance`, `uncovered`, 속도 함수 이름/시그니처를 유지한다.

변경 전 `d9b1b2da`에서 11개 합성 경로의 결과를 `route-boundary-v1.json`에 고정했다.
검증 대상은 노드, 관측 ID/버전, 장면 계획, 새 이동 재료와 과거 스토리보드 결과의 hash다.
표본에는 정지·회전·되짚기·단절 및 변속 경로가 포함된다. #521이 머지된 최신 DEV
`f6ac5819`를 통합한 뒤에도 기대값 수정 없이 동일했다.

2026-09-14, backend에서 `uv run --no-sync pytest -q`로 아래 파일을 실행하여
**157 passed, skip 0**을 확인했다. 최초 136개 실행과 합산하지 않는다.

- `tests/walk/diary/`: `test_route_calculation_boundary.py`, `test_diary_observations.py`,
  `test_diary_base_selection.py`, `test_diary_route_patterns.py`, `test_diary_activity.py`,
  `test_diary_domain_package.py`.
- `tests/walk/storyboard/`: `test_walk_storyboard.py`, `test_storyboard_observations.py`,
  `test_storyboard_pins.py`.
- `tests/walk/context/`: `test_provider_contract_boundaries.py`,
  `test_provider_contract_compatibility.py` (#521 통합 뒤 공급 계약 보존 확인).

함수 내부/상대 import 검사와 새 인터프리터에서 과거 모듈 차단, strict 속도 경계·최소
지속 시간·기준 속도 표본 수·block 단절, 소비자 간 정책 변경 격리를 확인했다.
Ruff check/format, 저장소 규칙 검사, 소유권 목록 검사도 통과했다. 이번 단계는 순수 계산의
의존성 변경이므로 DB/전체 스위트/실환경 공급자·모델·실기기 검증은 실행하지 않았다.
2단계 완료 당시 생성·호환(D1/D2/D3), 측정 투영(C1), 산출물/공개 패키지 경계(E1/E2)는 미완료였다.

## 3단계 구현·검증 기록 (#524)

- `walk_diary.lifecycle.strategy`가 협상된 형식과 명시적 override로 작성기·기대 결과
  자료형·완료 함수를 **예약/외부 실행 전에 함께 확정**한다. 결과 종류는 계약 검사에만
  사용한다. 잘못된 결과에서 다른 완료기로 전환하지 않고 기존 실패/base 발행 처리를 한다.
- 일반 `writer=` 대역과 wrapper는 현재 카드 계약을 유지한다. 같은 board 형식의 과거
  슬롯 작성은 `walk_diary.api.legacy_slot_writer(writer)`로 명시한다. 이는 HTTP 옵션이
  아닌 내부 주입 계약이다. 과거 슬롯 테스트는 이 계약을 명시하도록 전환했으며, 의도 없이
  일반 writer가 슬롯 결과를 반환하면 카드 실패 base가 발행되는 것을 별도로 검사했다.
- `walk_generation.api`가 저장 형식 협상을, `walk_legacy.storyboard`가 과거 후보 생성/
  조회를 소유한다. 요청 schema는 `schemas.walk_generation`, 현재 응답은 `walk_diary`,
  과거 응답은 `walk_legacy`가 소유한다. 기존 `walk_storyboard` 서비스/schema import는
  호환 진입으로 남기고 URL·요청/응답 JSON Schema는 유지한다.
- `walk_generation.state`가 유일한 공통 reserve/complete/reusable 구현이다.
  `walk_storyboard_state`의 함수/예외는 같은 객체를 재노출한다. ORM/DAO·같은 저장 행·
  60초 lease·세대/revision 비교·원본 재확인·잠금/커밋 순서는 변경하지 않았다.
- `walk_generation/**`와 `walk_legacy/**`도 목록 검사의 핵심 범위에 명시했다.
  새로운 디렉터리 아래로 이동했다고 직접 import 접점만 검사하는 누락이 생기지 않게 한다.

2026-09-14, backend `uv run --no-sync pytest -q`로 최종 **134 passed**를 확인했다.
최초 133개 실행과 합산하지 않는다. 실행 파일은 모두 `tests/walk/` 아래다.

- `diary/`: `test_generation_contract_boundary.py`, `test_diary_generation.py`,
  `test_diary_board_api.py`, `test_diary_board_slot_writing.py`, `test_diary_writer_entrypoints.py`,
  `test_diary_writing_boundaries.py`, `test_diary_publication.py`, `test_diary_service_package.py`,
  `test_diary_space_integration.py`.
- `storyboard/`: `test_walk_storyboard.py`, `test_storyboard_pins.py`,
  `test_storyboard_observations.py`.

폐기용 PostgreSQL 17 (`127.0.0.1:55432/walk_pin_test`)에서 아래 6개 파일을
`uv run --no-sync pytest -q -rs`로 실행하여 **15 passed, skip 0**을 확인했다.
`WALK_PIN_TEST_DATABASE_URL`과 `LIVE_STORYBOARD_TEST_DSN` 모두 이 loopback DB를 가리켰다.

- `diary/test_diary_generation_db.py`, `test_diary_board_db.py`, `test_diary_collection_db.py`,
  `test_diary_retention_db.py`, `test_diary_temperature_db.py`.
- `storyboard/test_walk_storyboard_db.py`.

현재/과거 작성과 저장본 판독, HTTP schema hash(`4d7407f6` 고정 표본), 중복/취소/마감과
늦은 결과 보호, 배경 보존·새 사진 입력 이후 덮어쓰기 차단을 확인했다. Ruff, 저장소 규칙,
목록 재검사/자체 검증도 통과했다. 전체 스위트·실환경 공급자/모델·실기기 검증은 실행하지
않았다. **3단계 완료 당시 C1(측정 투영), E1/E2(산출물/공개 패키지 경계)는 미완료**였다.

## 4단계 구현·검증 기록 (#526)

- `services.walk_measurement_projection.project`가 검증된 motion/precision 입력을 받아
  `MeasurementProjection` 자료형을 반환한다. 동결 엔진 재생, 최종 ledger, 원본 시각,
  경로·경계·metrics를 공유하며 응답 JSON 또는 저장 페이지를 만들지 않는다.
- `walk_trajectory`는 이 결과에서 기존 후보 조회 응답을 조립·직렬화한다.
  `walk_measurement`는 같은 결과에서 측정 요약과 경로 페이지를 직접 조립한다.
  측정 저장의 `walk_trajectory._project` 호출과 `TrajectoryCalculation` JSON 재파싱은 제거했다.
- `SourceWallTime`은 `daengs_walk.trajectory_projection`이 소유하고 두 전송 계약이
  직접 소비한다. 기존 `schemas.walk_trajectory.SourceWallTime` import는 같은 객체로 유지된다.
- fingerprint/expected-ID 검증과 예외 코드, ledger superseded 제외, 원본 시각의 정수 변환,
  계산·직렬화의 off-loop 실행, 저장 전 원본 재확인과 잠금·커밋 흐름은 유지했다.

2026-09-14, 변경 전 `018a9ce7` 코드에서 GPS 재생 32개·정밀 입력 32개·긴 경로 1개,
총 **65개** 표본의 조회 응답 바이트 SHA-256·measurement ID를 먼저 고정했다.
정밀 입력 33개는 측정 요약과 페이지 바이트 SHA-256도 기록했다.
`measurement-projection-v1.json`에 기준을 남겼으며 변경 후 재생으로 일치를 확인했다.
기존 Kotlin 공유 fixture 두 개의 측정 요약·페이지 문자열 비교도 그대로 통과했다.
조회/측정/페이지의 JSON Schema hash 3개도 변경 전과 같다.

backend에서 아래 명령으로 **192 passed, skip 0**을 확인했다.

```powershell
uv run --no-sync pytest -q tests/walk/measurement/test_projection_boundary.py tests/walk/measurement/test_stored_measurement.py tests/walk/api/test_trajectory_calculation.py tests/walk/api/test_motion_calculation.py
```

조회 스키마의 직렬화/파싱을 차단한 측정 생성, 새 인터프리터에서 조회 모듈과 조회 스키마
로딩 차단, 저장 전 입력 변경 거부·중복 저장 재사용·계산 실패 시 미발행도 검사했다.
측정 발행 테스트는 session/repository 대역이며 실제 PostgreSQL round trip 검증은 아니다.
이 단계는 계산·자료형 의존성 변경이고 SQL/DAO/저장 트랜잭션은 수정하지 않았다.
Ruff check/format(변경 Python 7개), 저장소 규칙 검사 3개, 소유권 목록 검사/자체 검증도 통과했다.
실제 DB·전체 스위트·실환경 공급자/모델·APP/실기기 검증은 실행하지 않았다.
**4단계 완료 당시 E1/E2(봉인 산출물/공개 패키지 경계)는 미완료**였다.

## 5단계 구현·검증 기록 (#529)

- `walk_analysis`는 측정 분석의 JSONB/ORM 조립·복원을 소유한다. 새
  `build_analysis_model`은 셀로판이나 캡슐을 만들지 않는다.
- `walk_artifacts.cellophane`은 기존 v1 codec·fingerprint·ORM 자식 생성/복원,
  `walk_artifacts.capsule`은 분석 identity에 묶인 1:1 캡슐 저장/복원을 소유한다.
  `walk_artifacts.api.build_analysis_models`가 분석과 원판의 identity/시각을 검증해
  함께 조립한다. 원본 봉인 서비스의 잠금·외부 날씨 호출·원본 재확인·commit/rollback은 유지했다.
- 공간 조회는 셀로판 복원 어댑터를 직접 참조한다. 분석을 읽기 위해 원판 codec을
  함께 로딩하거나, 원판을 읽기 위해 분석 저장기를 로딩하지 않는다.
- 도메인 명시 진입은 `contracts`/`evidence`(측정), `cellophane`/`capsule`(봉인 산출물),
  `spatial_diary`(조회)다. 제품/평가 코드의 기존 루트 export 소비를 실제 소유 모듈로 바꿨다.
  `daengs_walk`의 기존 `__all__` 11개는 지연 export로 보존하며 새 객체를 만들지 않는다.
  `from daengs_walk import *`와 기존 함수/자료형 객체 identity도 검증한다.
- `walk_analysis`의 기존 셀로판 함수와 `build_analysis_models`는 지연 호환 이름,
  `walk_capsule`은 기존 캡슐 함수 재노출이다. 구현은 새 어댑터에 하나만 존재한다.
  `walk_artifacts/**`는 목록 검사의 핵심 범위에 추가했다.

### import 목록 밖의 호환 경로

`import_module`로 지연 제공하는 export는 정적 import 행에 나타나지 않는다.
루트의 `_EXPORTS`(11개 이름 → 위 5개 소유 모듈)와 `walk_analysis._COMPAT`
(셀로판 상수/codec 6개 → `walk_artifacts.cellophane`, 조립 함수 → `walk_artifacts.api`)가
그 명시 목록이다. 이를 의존성 제거로 숨기지 않는다. 제품 호출자는 호환 경로를 사용하지
않으며, 새 인터프리터의 금지 모듈 로딩 검사와 동일 객체 검사가 실제 경계를 확인한다.

### 검증 결과와 한계

변경 전 `284695e1`에서 분석·셀로판·날씨 포함 캡슐·날씨 미상 캡슐의 payload 해시와
원판 fingerprint를 `sealed-artifacts-v1.json`에 고정한 뒤 변경 후 일치를 확인했다.
빈 산책 원판, 잘못된 identity/시각, 변조된 metadata/fingerprint, 저장본 복원은 기존
검사를 유지했다. 전체 스위트·실환경 공급자/모델·APP/실기기 검증은 실행하지 않았다.

backend에서 아래 명령으로 **140 passed, skip 0**을 확인했다.

```powershell
uv run --no-sync pytest -q tests/walk/measurement/test_artifact_boundaries.py tests/walk/measurement/test_walk_analysis_storage.py tests/walk/measurement/test_cellophane.py tests/walk/measurement/test_walk_capsule.py tests/walk/measurement/test_finalize_contract.py tests/walk/diary/test_spatial_diary.py tests/walk/diary/test_spatial_diary_query.py tests/walk/diary/test_diary_service_package.py tests/walk/storyboard/test_storyboard_observations.py
```

폐기용 PostgreSQL 17 (`127.0.0.1:55432/claims_test`)에
`TERRITORY_TEST_DATABASE_URL`을 지정하고 아래 명령으로 **44 passed, skip 0**을 확인했다.
봉인/게임 잠금 해제, 경쟁 요청 재사용, 늦은 날씨 결과 차단, 캡슐 복구와 활동 요약 조회를
실제 SQL로 검증했다. 테스트 컨테이너는 종료했다.

```powershell
uv run --no-sync pytest -q -rs tests/walk/api/test_walk_finalize_db.py tests/activity/test_walk_summary_queries_db.py
```

변경 Python 16개 Ruff check/format, 저장소 규칙 검사 3개와 소유권 목록/자체 검증도 통과했다.

현재 일기 생성과 과거 생성/저장본 읽기의 실행 기록은 3단계, 측정 투영 바이트는 4단계,
봉인 산출물과 공개 import는 이 단계 기록을 각각 따른다. 이 단계에서 이전 테스트 전체를
다시 실행한 것으로 합산하지 않는다. 선언한 의존성 부채 정리의 완료와 모든 walk 파일의
폴더 재배치, 전체 기능/실환경 검증 완료는 서로 다르다. 서비스 루트 46개 파일은 목록의
소유권/남긴 이유를 기준으로 판단한다.

## 재검사와 완료 기준

저장소 루트에서 다음 명령을 명시적으로 실행한다. stdlib만 사용하며 앱을 import하지 않는다.

```powershell
uv run --no-project --python 3.12 python tools/check_walk_ownership.py
uv run --no-project --python 3.12 python tools/check_walk_ownership.py --self-test
```

검사는 추적 파일을 다시 발견하여 누락·삭제·소유권 미지정·중복·새/사라진 import·판단표
누락을 실패로 돌린다. 목록 자동 갱신 옵션은 없다. 다음 PR에서 변화를 검토한 뒤 JSON과
읽기용 두 표를 함께 갱신한다. 추후 미완료 부채가 생기면 `change`로 남겨야 하며 검사 통과 자체가 해결을
뜻하지 않는다. 자체 검증은 메모리에서 누락/새 import/잘못된 소유권 등을 주입한다.

**0단계 완료 조건:** 선언 범위 내 파일 누락/미분류 0, 실제 직접 import 목록 재현,
교차 관계의 유지/변경 판단, 정적 분석 밖 접점·후속 검증·미완료 부채가 문서에 존재한다.

**전체 리팩토링 완료 조건:** 후속 부채별 구현/검증 근거와 남긴 공유 계약을 제시하고,
새 역참조를 막는 검사와 현재/과거 생성·기존 저장본 판독 결과를 함께 제출한다. 폴더 이동,
파일 개수 감소, 이 목록 검사 또는 특정 테스트 통과만으로 완료라고 하지 않는다.
