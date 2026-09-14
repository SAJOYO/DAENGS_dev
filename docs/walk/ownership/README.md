# 산책 기능의 소유권과 의존 경계 — 0단계

0단계 조사 기준은 DEV `8e3d1589`, 현재 목록은 `390ddd0c` 위의 #521(1단계) 변경을 반영한다. 조사·갱신일은 2026-09-14다.
이 문서는 **남은 구조 변경의 범위를 고정하는 조사 결과**이며 리팩토링 전체 완료 선언이 아니다.
0단계는 조사만 수행했다. 1단계는 B1·B2·B3의 공급 계약/일기 어댑터를 분리하며 DB·공개 계약·정책은 유지한다.

## 먼저 읽을 결과

- [파일별 소유권 표](files.md): 범위 내 661개 파일, 소유 영역·현재 계층·처리·근거·부채.
- [직접 import 판단표](crossings.md): 제품 소스의 교차 영역과 측정 내부 계산/조회 결합 357개.
- [기계 판독 목록](inventory.json): 위 파일 목록, 1,599개 직접 로컬 import 관계, 판단과 관련 영역.
- [범위 재검사 도구](../../../tools/check_walk_ownership.py): 새 파일·삭제·import 변경·미분류 판단 검출.

범위에 속한 **핵심 Python 217개**, 직접 import 접점 156개, 검증 자산 222개,
SQL 접점 52개, 문자열로 확인한 UI/운영 접점 8개, 명명된 도구 4개, 명시한 통합 접점 2개다.
각 파일은 발견 사유 하나로만 집계한다. 661개를 모두 제품 구현 파일이라고 세지 않는다.
서비스 루트의 `walk.py`·`walk_*.py` **45개는 모두 포함**했다.

357개 관계 중 21개는 명시한 후속 경계 변경 대상, 336개는 기능 소비·계층 조립·공통 계약
관계를 보존하는 대상으로 분류했다. 이는 코드의 결함 개수나 필요한 PR 개수가 아니다.
동일 원인의 여러 import가 별도 행으로 기록된다. `retain-interface`는 **현재의 기능 소비
관계를 유지**한다는 뜻이며 비공개 함수까지 영구적인 API로 승인하지 않는다.

## 범위가 어떻게 정해졌는가

파일 이름만으로 완료를 판단하지 않도록 다음 합집합을 사용한다.

1. 추적 중인 `backend/src/daengs_walk/**/*.py` 전부.
2. backend의 모든 계층에서 `walk.py`·`walk_*.py`, `services/walk_diary/**`,
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
| 사용자 기록 | 메모·행동 핀 수정/삭제, CAS, 위치 출처 검증 | 일기 입력 공급자. 배경 outbox 예약은 기록 변경과 원자성을 유지 |
| 사진 | manifest CAS·재시도 해시·삭제 기록 | 일기 입력 공급자. B3에서 공용 값/해시 계약을 직접 소비하도록 분리 |
| 배경 공급·카탈로그 | 원본 관측·페이지 보존·갱신·예산·기록별 수집/재수집 | 일기와 기록이 함께 소비. 일기 `AreaInput` 변환/장면 채택은 공급자에 넣지 않음 |
| 현재 일기 | 재료 해석, 장면 선정, 카드/행동/활동/제목 작성, 발행 | 원본·사진·배경·측정을 소비. 기존 그래프와 공통 실행기를 사용 |
| 과거 형식 지원 | storyboard 후보 v1~v5, 지원 중인 bundle/slot 실행·읽기 | 현재 정책을 암묵적으로 소유하지 않음. 지원 폐지는 이번 계획이 아님 |
| 공통 생성 상태 | 생성 번호·입력 버전·lease·재사용·완료와 공유 저장 행 | 현재와 과거가 같은 상태 전이를 사용; 복제하지 않음 |
| 공용 계약 | 실제로 같은 의미인 값·좌표·정규 직렬화 | 소비자 전용 정책 없음. 모든 digest/좌표 모델을 무조건 하나로 합치지 않음 |
| 통합 접점 | 인증·활동 게임·어시스턴트·공통 실행기·배포·UI | 각 제품/계층 소유 유지. 산책과 만나는 호출 계약·잠금 순서를 관리 |
| 검증·도구 | 테스트·고정 표본·실험·운영 점검 | 제품 패키지 밖에 유지; 소비하는 영역과 명시적 실행 경로를 추적 |

## 제거할 결합과 보존할 계약

B1·B2·B3은 **#521에서 구현·직접 검증**했다. 나머지 8개 항목은 미완료다. 아래 표는 원래 문제와 보존 계약을 함께 남긴다.

| ID / 단계 | 근거가 되는 현재 코드 | 변경 방향 | 반드시 보존할 것 |
| --- | --- | --- | --- |
| B3 / 1 · #521 구현 | `walk_photo.transition` 및 사진 schema, area/park/river 카탈로그 → `diary.contracts.input` | 사진 요청 digest와 실제 공용 값의 독립 계약 추출. 이름이 digest인 함수를 전부 합치지 않음 | 키 정렬·공백·유니코드·숫자·datetime/model 직렬화·기존 요청 해시, 같은 revision 재시도/충돌 판정 |
| B2 / 1 · #521 구현 | `walk_weather_context` → `diary.slots.temperature.GridTemperature`·`Point` | 시간/좌표/공급자에 묶인 관측 자료형은 공급 계약으로, `temperature_candidate`의 장면 일치·나이 제한은 일기에 유지 | 과거 hourly 관측 검증, query/request/fetch 시각, 날씨 채택/제외 이유와 저장 payload |
| B1 / 1 · #521 구현 | `walk_space_catalog_input.retain_page/retained_fields/normalization_input` | 원자료 보존/검증은 카탈로그, `AreaInput` 구성은 일기 수집 어댑터로 분리 | pagination/metadata·잘못된 행·충돌 행 보존, 원자료 hash, coverage 검증. 요약 결과에서 원자료 복원 금지 |
| A1 / 2 | `diary.route.binding/observations`, `diary.slots.sources` → `storyboard_input.route_nodes` | 연속 경로/원본 관측 주소를 공통 계산으로 분리; 과거 `scene_inputs`는 호환 소유 | 끊긴 경로 block, 실제 GPS 출처, moving 거리 누적, 장면/관측 식별자 |
| A2 / 2 | `diary.selection.board`·`route.observations` → `storyboard_selection` | 거리/빈 구간 계산은 공유; 상대 속도 관측 정책은 소유자와 명시 파라미터/버전을 구분 | 기존 기준 속도·비율·지속 시간·후보 순위·동률 처리·미선정 구간. 정책 통합 금지 |
| D1 / 3 | `lifecycle.generation.generate_diary`의 `CardWritingResult` 타입에 따른 완료기 선택 | 실행 전에 작성기·완료기·기대 결과 계약을 함께 결정하고 결과를 검증 | provider 대역 주입이 전략을 바꾸지 않음, 현재/과거 지원 계약, 수집 적용·마감·실패 fallback |
| D2 / 3 | `services.walk_storyboard`·`schemas.walk_storyboard`의 현재/과거 분기 | HTTP 협상 진입과 과거 실행/응답 소유권을 분리 | 기존 URL·지원 format·협상 결과·공개 JSON. **같은 board format에도 현재/과거 슬롯 경로가 있으므로 format만으로 전략을 결정하지 않음** |
| D3 / 3 | `walk_storyboard_state`의 `reusable/reserve/complete` | 공통 생성 상태로 이름/소유권 명시; 모델·DAO 계층과 공유 행 유지 | generation/input revision/lease 비교, 원본 재확인, 중복/늦은 결과 차단. 현재/과거 상태 복제 금지 |
| C1 / 4 | `walk_measurement.project` → `walk_trajectory._project` → JSON → `TrajectoryCalculation` 재파싱 | 검증된 공통 투영 결과 → 조회 응답 직렬화 / 측정 요약·페이지 조립으로 분리 | measurement ID·응답 바이트·페이지 hash·원본 시각·정밀도·순서·예외 계약, 계산/직렬화의 off-lock 실행 |
| E1 / 5 | `walk_analysis`가 측정 모델과 셀로판 직렬화를 함께 제공 | 실제 사용자를 기준으로 한 산책의 산출물 생성/저장 어댑터 경계 정리 | WalkAnalysis와 capsule의 1:1 바인딩·cellophane 봉인 형식·복원 검증. DB 분할/스키마 변경 없음 |
| E2 / 5 | `daengs_walk.__init__`에서 측정·capsule·cellophane·spatial_diary를 함께 import | 소유 영역의 명시 진입과 기존 공개 이름 호환 범위 정의 | 공유 타입 객체와 기존 사용자 import의 호환, 불필요한 제품 기능의 암묵 로딩 점검 |

공용 계약 추출에도 기존 저장 계약의 의미가 다르면 **독립 버전을 유지**한다. 예를 들어
사진 요청 hash와 원본 GPS fingerprint가 둘 다 SHA-256이라고 같은 직렬화로 바꾸지 않는다.
좌표도 공통 `Point`라는 이름만으로 원본 비트·소수점 정밀도 계약을 통일하지 않는다.

### 같은 속도 정책이라고 합치면 안 되는 두 경로

`diary.route.observations`가 참조하는 기존 `movement_candidates`는 느림 0.5배 미만,
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
검증은 실행하지 않았다. A1/A2·C1·D1/D2/D3·E1/E2는 여전히 후속 작업이다.

## 재검사와 완료 기준

저장소 루트에서 다음 명령을 명시적으로 실행한다. stdlib만 사용하며 앱을 import하지 않는다.

```powershell
uv run --no-project --python 3.12 python tools/check_walk_ownership.py
uv run --no-project --python 3.12 python tools/check_walk_ownership.py --self-test
```

검사는 추적 파일을 다시 발견하여 누락·삭제·소유권 미지정·중복·새/사라진 import·판단표
누락을 실패로 돌린다. 목록 자동 갱신 옵션은 없다. 다음 PR에서 변화를 검토한 뒤 JSON과
읽기용 두 표를 함께 갱신한다. 현재 미완료 부채는 `change`로 남아 있어 검사 통과가 해결을
뜻하지 않는다. 자체 검증은 메모리에서 누락/새 import/잘못된 소유권 등을 주입한다.

**0단계 완료 조건:** 선언 범위 내 파일 누락/미분류 0, 실제 직접 import 목록 재현,
교차 관계의 유지/변경 판단, 정적 분석 밖 접점·후속 검증·미완료 부채가 문서에 존재한다.

**전체 리팩토링 완료 조건:** 후속 부채별 구현/검증 근거와 남긴 공유 계약을 제시하고,
새 역참조를 막는 검사와 현재/과거 생성·기존 저장본 판독 결과를 함께 제출한다. 폴더 이동,
파일 개수 감소, 이 목록 검사 또는 특정 테스트 통과만으로 완료라고 하지 않는다.
