# 기능별 테스트 실행 지도

2026-09-09 `origin/dev` (`70969dca695ee55c4cb7fa22766ff231f12ac62e`) 기준으로 작성했다.
rkbuhtig 작성 PR에서 다룬 9개 기능의 검증 범위를 정리한다. Geo 이관과 공동 작업이 포함되므로
여기에 연결한 코드·테스트 전체의 단독 소유권을 뜻하지 않는다. APP의 Kotlin 테스트는 별도 범위다.

이 문서는 **실행 범위 목록이며 모든 기능의 통과 보고서가 아니다.** 1단계 조사 시점의 backend
전체에는 `test_*.py` 272개, 루트 직속 184개가 있었다. 2단계에서 Walk 파일 37개를 기능별로
옮기고 공용 도구를 분리했다. 테스트 파일 수는 272개 그대로이며 루트 직속은 172개다.
이 수치는 최초 Walk 정리 시점의 기록이다. 이후 Walk·Place·Territory·Activity 정리와 임시 DB 검증 결과는
아래 각 정리 항목에 기록한다. 원격 CI를 실행한 결과는 아니다.

## 실행 원칙

- 모든 명령의 작업 디렉터리는 `backend/`다. 해당 변경에 연결된 명령만 선택한다.
- Python 3.12와 프로젝트 의존성이 필요하다. 최초 준비 또는 lock 변경 시
  `uv sync --frozen --extra place --extra agent`로 공통 API/Place/agent 의존성을 준비한다.
  문서 정리만 할 때는 설치나 테스트가 필요하지 않다.
- 아래 기본 묶음은 실제 DB 통합 묶음과 구분했다. HTTP 계약 테스트의 fake/override 성공은
  배포 서버의 API 등록, 실제 인증, 외부 공급자 가용성까지 확인한 결과가 아니다.
- 현재 pytest에 실행용으로 선언된 marker는 `slow`이고 기본값은 `-m 'not slow'`다.
  `-m unit`/`-m integration` 구분은 아직 없다. 파일·디렉터리 경로로 선택한다.
- DB 미설정으로 `skipped`이면 해당 DB 동작은 **미검증**이다. 통과 수와 skip 사유를 함께 기록한다.
- CI 예산이 소진된 현재 작업에서는 로컬 변경·타겟 검증까지만 한다. push·PR 생성·workflow dispatch는
  하지 않는다. 3단계에서는 Walk DB workflow의 변경 감지·실행 대상·skip 확인을 로컬에서 검증한다.

## 9개 기능 지도

각 기본 묶음은 기능 전체를 건드릴 때의 출발점이다. 한 파일 내부 변경이면 관련 테스트 파일이나
확인한 test selector로 더 좁히고, 공용 계약이 바뀌면 아래 소비자 검증을 추가한다.

### 1. 반려견 프로필

- 근거: PR #64. 경계: `daengs_backend`의 pet 라우터·스키마·저장소.
- 기본: CRUD/검증은 fake 저장소를 사용하는 `pets/test_pets.py`.
- 확대: 인증 변경은 `test_app_auth.py`, 사진 계약 변경은 `pets/test_pet_photo.py`.
  반려견 조건 필드를 바꾸면 장소 검색의 `place/api/test_multi_dog_search.py`,
  `place/geo/test_pet_axes.py` 및 PostGIS `place/integration/test_pet_filter.py`도 연결된다.
- 한계: 프로필 fake 테스트만으로 실제 DB 스키마·제약조건 변경을 검증할 수 없다.

```powershell
uv run pytest -q tests/pets/test_pets.py
uv run pytest -q tests/pets/test_pet_photo.py
```

### 2. 장소 검색

- 근거: PR #63, #71, #206, #253, #356. 경계: `daengs_place`의 검색·반려견 조건·필터·API.
- 기본: 이름/다견/필터/축 변환 계약. DB 연결이 섞인 파일은 아래 통합 묶음으로 구분한다.
- DB 추가: PostGIS 설정 후 `test_search_v2.py`, `test_contract.py`, `integration/`.
  정렬·검색 SQL·AND/OR 변경이면 이 검증이 필요하다.
- 확대: 원천 데이터·제한조건 변경은 `place/source_facts/`,
  `place/search/test_restriction_*.py`에 해당하는 개별 파일과 `place/ingest/` 중 변경 원천 테스트.
  자연어 검색 결과 구조에 영향이 있으면 3번 소비자도 확인한다.

```powershell
uv run pytest -q tests/place/search/test_name_search.py tests/place/api/test_multi_dog_search.py tests/place/api/test_contract_validation.py tests/place/filters tests/place/geo
# 아래 명령은 격리된 PostGIS와 마이그레이션 준비 후 선택한다.
uv run pytest -q tests/place/search/test_search_v2.py tests/place/search/test_contract.py tests/place/integration
```

### 3. 자연어 시설 검색

- 근거: PR #183–#196, #274. 경계: `daengs_place`의 intent/planning/presentation/discovery/provider와
  `daengs_backend` 시설 discovery API, `daengs_life` orchestration 소비자.
- 기본: 의도 해석→계획→표현과 discovery 서비스, 내부/외부 API 계약.
- 확대: capability/응답 계약 변경은 아래 두 번째 명령. 프롬프트·gold 기준을 바꿀 때는
  `test_router_benchmark_place_gold.py`를 추가한다. 실제 검색 SQL 변경은 2번 DB 묶음도 필요하다.
- 테스트 provider 대역 검증은 실제 Gemini 호출 품질/가용성 검증과 구분한다.
  PR #275에서 DEV 밖으로 돌린 web review 화면은 현재 서버 검증 범위에 넣지 않는다.

```powershell
uv run pytest -q tests/place/intent tests/place/planning tests/place/presentation tests/place/discovery tests/place/providers tests/place/api/test_discovery_internal.py tests/place/api/test_facility_discovery_api.py
uv run pytest -q tests/test_place_capability_fixtures.py tests/test_orchestration_place_adapter.py tests/test_orchestration_place_projection.py tests/test_orchestration_place_routing.py
```

### 4. Journey

- 근거: PR #74. 경계: `daengs_journey`, 장소 선택 후 일회성 이동 경로 스냅샷.
- 기본: 현재 7개 파일이 경로 제공자/config, state, handoff, usage gate, API, 패키지 경계를 검증한다.
- 확대: Place에서 Journey로 넘기는 계약을 바꾸면 3번의 해당 routing/adapter 소비자도 확인한다.
  이 묶음만으로 실제 TMAP 서비스나 배포 프록시 설정을 검증하지 않는다.

```powershell
uv run pytest -q tests/journey
```

### 5. 산책 측정·분석

- 근거: PR #124, #125, #138–#140, #159, #302.
  경계: `daengs_walk` measurement/evidence/cellophane/capsule와 backend 업로드·finalize·분석 저장.
- 기본: 측정·격자·분석 직렬화 및 finalize 계약. 저장소 테스트에도 가짜 세션/SQL 구성 검사가
  있으므로 파일 이름에 storage/repository가 있다고 실제 DB 검증으로 세지 않는다.
- 확대: 업로드/API/권한/스타일 정책을 바꾸면 두 번째 명령. finalize 결과 구조를 바꾸면
  8번 storyboard·diary 소비자, 활동 반영을 바꾸면 9번 activity DB 검증을 추가한다.

```powershell
uv run pytest -q tests/walk/measurement/test_walk_measurement.py tests/walk/measurement/test_cellophane.py tests/walk/measurement/test_hex_grid.py tests/walk/measurement/test_walk_capsule.py tests/walk/measurement/test_walk_facts.py tests/walk/measurement/test_finalize_contract.py tests/walk/measurement/test_walk_analysis_storage.py tests/walk/api/test_walk_repository.py tests/walk/api/test_walks.py tests/walk/test_package_boundary.py
uv run pytest -q tests/walk/api/test_walk_api.py tests/walk/api/test_walk_auth.py tests/walk/api/test_walk_chunk.py tests/walk/measurement/test_walk_style.py
```

### 6. 산책 환경 정보

- 근거: PR #171, #173. 경계: `daengs_life.realtime.weather_at`과 산책 환경 adapter/observation.
- 기본: 좌표·과거 시각 선택, 공급자 저하 처리, 캐시 및 산책 입력 변환.
- 확대: 공용 observation 구조 변경은 `test_observation.py`도 확인한다.
  핀 context 또는 일기 observation에 전달하는 값 변경은 7·8번 해당 소비자를 추가한다.

```powershell
uv run pytest -q tests/test_weather_at.py tests/test_weather_at_api.py tests/walk/environment/test_walk_weather_adapter.py tests/walk/environment/test_walk_observation.py
```

### 7. 산책 기록·행동 핀

- 근거: PR #251, #339, #354, #357, #371.
  경계: backend walk_entry/walk_entry_v2 라우터, 스키마·저장소, context 수집·작업 큐.
- 기본: 기존 기록 HTTP, v2 핀 계약, context 및 pin-context 서비스.
- DB 추가: context와 pin의 DB가 서로 다르다. 아래 DB 표에 따라 필요한 명령을 선택한다.
- 확대: 핀 위치/타입/버전 계약 변경은 `walk/storyboard/test_storyboard_pins.py`와 8번 관련 일기 입력을 확인한다.
  사진과 연결되는 키/삭제/재시도 계약은 `test_walk_photo_db.py`까지 포함한다.
  앱의 위치 추정·오프라인 큐·v1 임시 전환 동작은 APP에서 별도 검증해야 한다.

```powershell
uv run pytest -q tests/walk/entries/test_walk_entries.py tests/walk/entries/test_walk_entry_http.py tests/walk/context/test_walk_entry_context.py tests/walk/context/test_walk_entry_pin_context.py tests/walk/entries/test_walk_entry_v2.py
uv run pytest -q tests/walk/context/test_walk_entry_context_db.py
uv run pytest -q tests/walk/entries/test_walk_entry_v2_db.py tests/walk/photos/test_walk_photo_db.py
```

### 8. 산책 일기·스토리보드

- 근거: PR #162, #166, #255, #308, #313, #370, #372, #374, #377, #378.
  경계: `daengs_walk` 공간 일기·storyboard·diary와 backend 조회/생성, 사진 metadata 입력.
- 기본: 공간 조회·anchor/제목/핀/observation, 일기 계약·stamp·writing·generation을 두 묶음으로 선택한다.
- DB 추가: live storyboard와 photo/diary generation은 다른 DB 설정을 사용한다.
- 확대: 사진/핀 공용 계약 변경은 7번 v2/사진 DB 검증도 포함한다. 생성 결과 검증은
  모델 공급자 운영 품질 평가나 앱 화면 검증까지 대신하지 않는다.

```powershell
uv run pytest -q tests/walk/diary/test_spatial_diary.py tests/walk/diary/test_spatial_diary_query.py tests/walk/storyboard/test_walk_storyboard.py tests/walk/storyboard/test_walk_storyboard_titles.py tests/walk/storyboard/test_storyboard_pins.py tests/walk/storyboard/test_storyboard_observations.py
uv run pytest -q tests/walk/diary/test_diary_contract.py tests/walk/diary/test_diary_stamps.py tests/walk/diary/test_diary_observations.py tests/walk/diary/test_diary_writing.py tests/walk/diary/test_diary_generation.py tests/walk/photos/test_walk_photo_input.py
uv run pytest -q tests/walk/storyboard/test_walk_storyboard_db.py
uv run pytest -q tests/walk/photos/test_walk_photo_db.py tests/walk/diary/test_diary_generation_db.py
```

### 9. 전봇대 점령

- 근거: PR #178, #193, #197, #249, #260, #281, #335, #360.
  경계: Place 중립 site 읽기/적재와 backend 방문·사진 판정·소유권·시즌·owner 요약.
- 기본: attempts/vision/claim/ownership API/owner summary. activity는 이 기능의 공동 소비자다.
- DB 추가: 점령 동시성·rollback·사진 확정·활동 반영은 claims DB 묶음.
  site 공간 조회는 별도 Place PostGIS 묶음이다.
- 확대: site 식별자·필터 계약은 두 서비스 경계를 함께 검증한다. 실제 사진 판정 서비스의
  응답 품질은 vision 대역 테스트 통과만으로 확인되지 않는다.

```powershell
uv run pytest -q tests/territory/visits/test_territory_attempts.py tests/territory/visits/test_territory_vision.py tests/territory/claims/test_territory_claim.py tests/territory/ownership/test_territory_ownership_api.py tests/territory/ownership/test_territory_owner_summary.py tests/activity/test_activity.py
uv run pytest -q tests/territory/ownership/test_territory_ownership_db.py tests/territory/certification/test_territory_certified_db.py tests/activity/test_activity_db.py
uv run pytest -q tests/place/api/test_territory_sites.py tests/place/ingest/test_territory_sites.py
```

## 실제 DB 검증 조건

아래는 실제 DB 테스트의 실행 조건이다. 로컬 검증에는 실행 후 제거하는 임시 DB를 사용한다.
공유 개발 DB 주소를 테스트 환경 변수에 복사하지 않는다. 각 fixture가 SQL/schema 생성·삭제를 수행한다.

| 환경 변수 | 대상 | 현재 코드의 허용 조건 / 준비 |
| --- | --- | --- |
| `WALK_CONTEXT_TEST_DATABASE_URL` | `test_walk_entry_context_db.py` | `postgresql+asyncpg://` 형식, localhost/127.0.0.1의 `walk_context_test`. fixture가 UUID schema와 실제 SQL 준비/제거 |
| `WALK_PIN_TEST_DATABASE_URL` | v2 pin, photo, diary generation DB | 같은 형식, localhost/127.0.0.1의 `walk_pin_test`. 공유 fixture가 실제 초기화·migration·검증 SQL 실행 |
| `TERRITORY_TEST_DATABASE_URL` | ownership/certified/activity DB | 같은 형식, localhost/127.0.0.1의 `claims_test`. UUID schema로 실제 SQL 검증 |
| `LIVE_STORYBOARD_TEST_DSN` | `walk/storyboard/test_walk_storyboard_db.py` | `postgresql://` 형식. localhost/127.0.0.1/::1 제한만 있고 DB 이름 제한은 없음. 별도 폐기 가능한 DB 지정 |
| `DAENGS_PLACE_DATABASE_URL` | Place DB 포함 파일 | 격리된 로컬 PostGIS와 Place Alembic 전체 schema 필요. `place/support/database.py`는 localhost/DB 이름을 강제하지 않고 설정 URL을 사용하므로 실행자가 명시적으로 지정 |

첫 네 환경 변수가 없으면 해당 fixture는 skip한다. Place는 연결 실패를 skip으로 처리하지만
연결 가능한 DB를 찾으면 seed를 쓰고 commit할 수 있다. 단순히 `.env` 기본값에 맡기지 않는다.
Place 전용 URL을 명시하고 대상이 폐기 가능한 로컬 DB인지 확인한 뒤에만 다음 schema 준비를 한다.
이 명령은 테스트 실행 명령이 아니라 실제 DB schema 변경 명령이다.

```powershell
uv run alembic -c infra/place/alembic.ini upgrade head
```

## Walk 정리 — 2단계

Walk 테스트 38개 중 패키지 경계 검사는 `walk/test_package_boundary.py`에 유지하고,
나머지 37개를 아래 기능 폴더로 옮겼다. 테스트끼리 직접 import하던 관계를 없애고
공용 생성 도구는 `support/`, pytest fixture 등록은 필요한 기능의 `conftest.py`에 둔다.

| `walk/` 아래 위치 | 담당 범위 |
| --- | --- |
| `measurement/` | GPS 측정, 격자·분석 저장, finalize, 스타일 정책 |
| `api/` | 산책 업로드·조회·인증·chunk·저장소 응답 계약 |
| `entries/` | 기존 기록 HTTP와 v2 핀 계약·실제 DB 검증 |
| `context/` | 기록/핀의 주변 정보 수집·작업 큐·실제 DB 검증 |
| `photos/` | 사진 metadata, 저장 형식 입력 변환·실제 DB 검증 |
| `diary/` | 공간 일기 조회·계약·관측·stamp·writing·generation |
| `storyboard/` | scene·anchor·제목·핀·환경 관측·live DB 검증 |
| `environment/` | 산책 환경 adapter·observation |
| `support/` | 공용 도구 8개 모듈과 fixture/SQL 경로 기준인 `paths.py`. 테스트 케이스는 두지 않음 |
| `fixtures/` | 기존 JSON 기준값. 내용·배치를 유지 |

공용 fixture는 import 시 DB를 열지 않으며 테스트가 요청할 때만 실행된다.
`entries`, `photos`, `diary`에서 사용하는 pin DB 장치는 `support/pin_database.py`가 제공하고,
사진 schema 추가 장치는 `support/photo_database.py`가 제공한다. `context`와 `storyboard`의
별도 DB fixture는 해당 DB 테스트 파일에 남겨 서로의 DB 설정을 덮어쓰지 않는다.
루트 `tests/conftest.py`와 `walk/conftest.py`의 기존 장치는 바꾸지 않았다.

함수별로 필요한 입력을 만들고 각 테스트가 값을 수정하는 방식은 유지한다.
공용 도구에는 assertion을 이동하지 않고, 검사 케이스를 추가·삭제하거나 marker로 제외하지 않는다.
기존 fixture 내부의 장치 무결성 assertion은 그대로 유지한다.

```powershell
# 해당 기능만 선택. 아래 두 명령을 매번 함께 실행할 필요는 없다.
uv run --no-sync pytest -q tests/walk/entries
# Walk 공용 도구 전체/폴더 배치 변경의 검증 범위
uv run --no-sync pytest -q -rs tests/walk
```

2단계 로컬 검증: 변경 전 Walk 수집 490개, 변경 후 실행 490개.
첫 실행은 464 passed / 6 failed / 20 skipped였고, 6개 실패는 공간 일기 fixture 경로 한 곳의
이동 누락이었다. 경로 수정 후 `uv run --no-sync pytest -q tests/walk/diary/test_spatial_diary.py`로
해당 파일 29개 모두 통과했다. 합산하면 기존 470개 실행 케이스를 확인했고, 실제 DB 20개는
환경 변수 미설정으로 당시 미검증이었다. 이후 `e3ba4f9`에서 임시 PostgreSQL 17로 DB 파일 5개를
실행해 20 passed / 0 skipped를 확인했고 임시 DB와 컨테이너를 제거했다.
전체 backend suite나 원격 CI를 실행한 결과는 아니다.
Walk Python 파일 53개의 ruff 검사·format 검사와 문서/CI 명령의 경로 130개 정적 확인도 완료했다.
로컬 venv에 `check` 진입점이 없어 동일 코드인 `uv run --no-sync python -m daengs_backend.cli.check`를
실행했다. migration 이름·검증 짝 검사는 통과했지만 Windows 바이트 검사는 이 PC의 PowerShell
스크립트 실행 정책에 막혀 미검증이다. 실행 정책은 변경하지 않았다.

## Place 정리

`origin/dev`의 `2fe6afc`에서 시작해 테스트 파일 35개를 이동했다. `place/place/` 중복 경로를
없애고 검색·자연어 검색의 각 단계와 API 테스트를 다음처럼 배치했다.

| `place/` 아래 위치 | 담당 범위 |
| --- | --- |
| `search/` | 검색 결과·이름·장소 유형·반려견 평가·제한조건. 기존 JSON도 함께 이동 |
| `filters/`, `intent/`, `planning/`, `presentation/` | 필터 계약, 자연어 해석, 실행 계획, 결과 표현 |
| `discovery/`, `providers/`, `source_facts/` | 검색 조립, 모델 transport 대역, 원천 사실 |
| `api/` | Place API와 backend의 시설 discovery API 계약 |
| `integration/`, `ingest/`, `geo/`, `core/` | 기존 DB·적재·공간·설정 검증 위치 유지 |
| `support/` | discovery 입력/서비스 생성, 메모리 세션 대역, DB seed·세션 도구 |

테스트 모듈을 다른 테스트에서 import하던 관계를 `support/discovery.py`로 옮겼다.
기존 `place/conftest.py`는 pytest fixture 없이 일반 도구만 제공했으므로
`support/database.py`로 이동했다. 루트의 시설 세션 대역은 `support/session_store.py`에 둔다.
기존 테스트 함수 421개와 공용 도구의 본문·데코레이터가 유지됐음을 AST로 대조했다.
capability·오케스트레이션 소비자 4개와 앱 로딩 검사는 루트에 유지하며 공용 경계 검증에 포함한다.

```powershell
# Place 배치/공용 도구 변경 범위. 임시 PostGIS와 Alembic 적용 후 실행한다.
uv run --no-sync pytest -q -rs tests/place tests/test_place_capability_fixtures.py tests/test_orchestration_place_adapter.py tests/test_orchestration_place_projection.py tests/test_orchestration_place_routing.py tests/test_main_stays_light.py
```

로컬 검증은 PostGIS 18-3.6에 Place Alembic 전체 이력을 적용한 뒤 위 범위에서
**666 passed / 0 skipped**였다. 실행 후 임시 DB와 컨테이너를 제거했다.
Place 소스·테스트 ruff 검사도 통과했다. workflow의 변경 감지 경로와 실행 대상을 대조했고,
JUnit 확인 단계가 정상 결과를 허용하고 skip·빈 결과를 거부하는 것을 로컬에서 확인했다.
기존 Place CI에 시설 API 및 공용 소비자 검증을 연결했다. 제품 코드·DB 스키마·의존성은
변경하지 않았으며 팀원이 설치할 별도 DB 도구나 실행기를 추가하지 않았다.

## Territory·Activity 정리

`origin/dev`의 `2fe6afc`에서 시작해 점령 테스트 7개와 활동 테스트 2개를 이동했다.
Place의 중립 점령지 조회·적재 테스트는 기존 Place 위치에 유지한다.

| 위치 | 담당 범위 |
| --- | --- |
| `territory/visits/` | 방문·사진 업로드·비동기 판정 |
| `territory/claims/` | 점령 상태 전이·권한과 APP 공유 시나리오 |
| `territory/ownership/` | 소유권 API·주인 요약 및 실제 SQL·동시성·rollback |
| `territory/certification/` | 사진 인증·보호 시간·재시도·점수의 DB 계약 |
| `territory/support/` | 임시 claims schema·사용자 fixture, 점령/사진/판정 생성 도구, SQL 경로 |
| `territory/fixtures/` | 기존 APP 공유 TSV 시나리오. 내용 유지 |
| `activity/` | 활동 측정·산책 연결·점수·worker·공개/개인 조회 |
| `activity/support/` | 활동 schema 확장·시계 fixture와 시즌·시간 지정 점령 생성 도구 |

기존 점령 DB 테스트 → 활동 DB 테스트 → 인증 점령 DB 테스트의 import 관계를 제거했다.
각 `conftest.py`는 필요한 fixture만 등록한다. 소유권 검증은 claims schema를,
활동·인증 검증은 여기에 활동 schema를 추가한 구성을 사용한다. fixture는 요청받을 때만
DB를 열며, 기존 localhost/`claims_test` 제한과 UUID schema 생성·제거 동작을 유지한다.
변경 전후 테스트 함수 84개와 helper 본문을 대조했고, 경로 변경과 매 수집마다 생성되는
UUID를 정규화한 뒤 파라미터 케이스 123개가 일치함을 확인했다.

```powershell
# 점령·활동 공용 도구/배치 변경 범위. 폐기 가능한 로컬 claims_test DB를 지정한 뒤 실행한다.
uv run --no-sync pytest -q -rs tests/territory tests/activity
```

기존 territory CI는 두 디렉터리를 실행해 주인 요약 API도 포함한다. 테스트·support·TSV 변경을
함께 감지하며, pytest 성공 뒤 JUnit 결과가 비었거나 skip이 있으면 실패한다.
제품 코드·스키마·의존성은 변경하지 않고 별도 DB 설치 도구나 실행기를 추가하지 않는다.

로컬 PostgreSQL 17에서 위 명령으로 **123 passed / 0 skipped**를 확인했다.
각 DB fixture가 운영 초기화·마이그레이션·검증 SQL을 실행했고, 실행 후 테스트 schema가
0개임을 확인한 뒤 임시 컨테이너와 DB를 제거했다. 대상 Python 25개 파일의 ruff·format 검사,
이전 import/실행 경로 잔재 확인, CI 변경 감지 및 JUnit 정상·skip·빈 결과 검사도 통과했다.
전체 backend suite나 원격 CI를 실행한 결과는 아니다.

## 반려견 프로필 정리와 Journey 점검

`origin/dev`의 `96e4529` 기준으로 프로필·사진 테스트 2개를 `pets/`로 이동했다.
두 파일이 반복하던 메모리 저장소와 인증된 pet API 클라이언트 생성은
`pets/support/api.py`로 모았다. 소유자 UUID와 테스트별 fixture 등록은 각 파일에 유지하고,
사진 클라이언트는 기존처럼 `storage` fixture를 먼저 준비한다. 공용 생성 도구는 호출마다
새 Store와 FastAPI 앱을 만들며 전역 상태를 보관하지 않는다.

루트 `fakes.py`는 인증·대화·산책 등 25개 테스트 파일이 공유하므로 이번에 분리하지 않았다.
`FakePet`과 pet 저장소 대역도 계정 탈퇴·산책·대화 검증에서 사용한다. 기존 `from fakes`
import 방식과 루트 pytest 설정은 유지한다. 제품 코드·DB 스키마·공용 인증 fixture 변경은 없다.

```powershell
uv run --no-sync pytest -q -rs tests/pets
uv run --no-sync pytest -q -rs tests/test_app_auth.py -k '강아지와 or 데이터_삭제'
```

로컬 결과는 프로필·사진 **48 passed / 0 skipped**, 탈퇴 시 데이터 삭제 **3 passed / 0 skipped**다.
두 번째 명령은 인증 파일의 다른 52개 케이스를 선택하지 않는다. 기존 테스트 함수 48개와
수집된 케이스가 모두 보존됐고, fixture의 인자·데코레이터도 유지됐음을 확인했다.
사진 검증은 임시 폴더의 실제 LocalBridgeStorage를 사용하지만 DB는 메모리 대역이므로
이번 단계에서 DB를 생성하지 않았다. 사진 테스트에는 기존 HTTP 413 이름 사용에 따른
Starlette deprecation 경고 14개가 있으며 테스트 실패는 아니다.

Journey는 기존 `tests/journey/`의 7개 파일과 전용 workflow 연결을 정적으로 확인했다.
테스트 간 직접 import와 추가 이동이 필요한 경로는 발견하지 못했다. Journey 코드를 바꾸거나
테스트를 재실행한 결과는 아니다. 프로필은 기존 `backend-tests`의 전체 자동 수집 대상이므로
새 CI job이나 실행 도구를 추가하지 않는다. 원격 CI는 이번 단계에서 실행하지 않았다.

## 남은 공유 관계

fixture 변경 시에는 제공 파일뿐 아니라 소비 파일도 검증 범위에 포함한다.
Walk·Place·Territory·Activity 지원 도구의 현재 소비 관계를 기록한다.

| 공용 장치 또는 테스트 모듈 | 연결된 소비자 / 다음 단계에서 보존할 계약 |
| --- | --- |
| 루트 `conftest.py` | 암호화 키·DB 기본값·warmup, crawl/metrics/Celery autouse 대역. 모든 하위 테스트에 적용 |
| `fakes.py`, `place_capability_cases.py` | 프로필 API/능력 fixture. 테스트 이름만 검색하면 빠지는 지원 파일 |
| `pets/support/api.py` | 프로필·사진 테스트가 공유하는 Store와 인증된 API 클라이언트 생성 도구 |
| `walk/support/entry_context.py` | context와 pin-context의 응답 생성 도구·state fixture |
| `walk/support/entry_v2.py`, `pin_database.py`, `photo_database.py` | 핀·사진·일기 DB에서 공유. 제공 도구 변경 시 세 기능의 소비자를 확인 |
| `walk/support/diary.py`, `photo_input.py`, `observations.py` | 일기 계약·stamp·writing·generation 및 사진 입력의 생성 도구 |
| `walk/support/storyboard.py` | storyboard/pin의 live fixture 및 제목 응답 생성 도구 |
| `place/support/discovery.py`, `session_store.py` | discovery 서비스·행동 및 시설 API의 공용 생성 도구·세션 대역 |
| `place/support/database.py` | 검색·통합·territory site API의 실제 DB 세션·seed. 일반 모듈로 import |
| `territory/support/database.py`, `ownership.py` | 소유권·활동·인증 점령의 DB/사용자 fixture 및 점령·사진·판정 생성 도구 |
| `activity/support/database.py`, `actions.py` | 활동·인증 점령의 활동 schema·시계·시즌 및 시간 지정 점령 생성 도구 |
| `fixtures/`, `walk/fixtures/`, Place 하위 fixtures/JSON | capability, 점령 시나리오, 산책 스타일/finalize, 공간 일기/observation, 원천 데이터 기준값. 파일 이동 시 상대 경로도 검증 |

`pythonpath = ["."]`는 `tests` namespace와 공용 도구 import에 계속 필요하다.
남은 도메인 정리는 후속 단계다. 중복 검증은 같은 동작·입력·실패 경계를
실제로 비교한 뒤 합친다. 파일 이름이 비슷하다는 이유로 삭제하지 않는다.

## CI 연결 현황 — 참고용, 실행하지 않음

| 현재 workflow | 담당 검증 / 확인한 한계 |
| --- | --- |
| [backend-tests](../../.github/workflows/backend-tests.yml) | 기본 pytest 전체. 기본 marker/의존성/DB 미설정으로 제외·skip되는 검증이 있을 수 있음 |
| [place-search-tests](../../.github/workflows/place-search-tests.yml) | PostGIS + Alembic + Place·시설 API·capability·오케스트레이션·앱 로딩. 빈 결과·skip 거부 |
| [journey-tests](../../.github/workflows/journey-tests.yml) | Journey 테스트와 서비스 설정 검증 |
| [walk-entry-context-tests](../../.github/workflows/walk-entry-context-tests.yml) | context DB·서비스·pin-context·기존 기록 HTTP. backend/Walk/Life 소스와 Walk 테스트 전체 변경을 감지 |
| [walk-entry-v2-tests](../../.github/workflows/walk-entry-v2-tests.yml) | pin/photo/diary generation 및 live storyboard DB. 위와 같은 소스·테스트 변경을 감지 |
| [territory-ownership-tests](../../.github/workflows/territory-ownership-tests.yml) | claims DB·activity·점령 API/서비스·주인 요약. 두 테스트 디렉터리와 Walk 소스 변경 감지, 빈 결과·skip 거부 |
| [migration-verification-tests](../../.github/workflows/migration-verification-tests.yml) | 별도 migration 검증. Python 대역 테스트로 대체할 수 없는 SQL 검증 경계 |

3단계에서 live storyboard DB 검증을 기존 v2 job에 연결했다. `LIVE_STORYBOARD_TEST_DSN`은
동일한 임시 PostgreSQL의 `walk_pin_test`를 사용하되 fixture마다 별도 UUID schema를 만든다.
새 DB job을 추가하지 않는다. 두 Walk job은 pytest 성공 뒤 JUnit 결과를 확인하여
빈 결과나 skip이 있으면 실패한다. DB 접속 실패는 기존 fixture에서 오류로 처리된다.
Walk 테스트 경로는 파일별 나열 대신 `backend/tests/walk/**`로 묶어 새 테스트·fixture·JSON이
추가돼도 검증 실행이 빠지지 않게 한다. 기존 전체 backend workflow와 점령 job의 테스트 명령은 유지한다.
위 내용은 로컬 설정·검증 기준이며 원격 CI 성공 상태를 뜻하지 않는다.

3단계 로컬 확인: 두 workflow에 적힌 pytest 대상을 임시 PostgreSQL 17에서 실행했다.
context 50 passed, v2/photo/diary/storyboard 46 passed, 양쪽 모두 0 skipped였다.
JUnit 확인 단계는 정상 결과를 허용하고 skip·빈 결과를 거부하는 것도 확인했다.
임시 컨테이너와 DB는 삭제했다. 점령 job은 변경 감지 경로만 정적으로 확인했으며
해당 DB 테스트나 전체 backend suite를 다시 실행하지 않았다. push·원격 CI 실행도 하지 않았다.

## 이 지도를 갱신하는 기준

- 테스트 추가 시 기능, 보장하는 실패 경계, DB/외부 의존성, 실행 명령을 함께 연결한다.
- 계약/fixture 변경 시 이를 import하거나 결과를 소비하는 테스트를 해당 기능의 확대 범위에 반영한다.
- 실행 결과에는 기준 커밋·명령·passed/failed/skipped·미검증 경계를 남긴다.
- 대응하는 테스트가 없으면 검증 공백으로 기록하고, 전체 suite 실행으로 대신하지 않는다.
