# 배경 공급 패키징 — 6단계 (#530)

기준은 DEV `70a07371`(#529 머지)이다. 0~5단계의 의존성 분리를 유지하면서 배경 공급 13개 서비스의 실제 구현을 `services/walk_background`에 배치한다. 기록·사진·원본·측정·공간 조회까지 패키징이 끝났다는 뜻은 아니다.

## 소유권과 공개 진입

| 기존 services 모듈 | 새 walk_background 소유 모듈 |
| --- | --- |
| walk_area_catalog | catalogs.area |
| walk_area_context | providers.area |
| walk_catalog_refresh | catalogs.refresh |
| walk_catalog_regions | catalogs.regions |
| walk_commerce_catalog | catalogs.commerce |
| walk_park_catalog | catalogs.park |
| walk_public_context | providers.public |
| walk_public_http | http |
| walk_river_catalog | catalogs.river |
| walk_sgis | providers.sgis |
| walk_space_catalog_input | catalogs.retention |
| walk_weather_context | providers.weather |
| walk_entry_context_source | contracts / collection / providers.facility로 분리 |

`contracts`는 Collected와 기존 정규 JSON 해시만 소유하며 설정·HTTP·DB를 불러오지 않는다. `collection.collect`는 원본 위치와 pin 상태를 확인하고 공급자를 선택한다. `providers`는 공급자별 요청·응답 검증을 맡고 수집 조정기나 기록 outbox를 역참조하지 않는다. `catalogs`는 원본 스냅샷·지역 조회·보존을, `catalogs.refresh`는 별도 작업에서 갱신·Redis 잠금/예산을 맡는다.

제품·CLI·worker·backend/tools는 실제 소유 모듈을 직접 import한다. 13개 옛 파일은 [명시한 공개 이름](background-package.json)을 지연 제공하는 호환 경로다. 구현이나 singleton을 복제하지 않으며 같은 자료형/함수 객체를 내보낸다. 기존 모듈의 비공개 함수, 가져온 의존성 변수, 옛 경로에 대한 monkeypatch 전파는 공개 호환 계약에 포함하지 않는다. 테스트 대역은 실제 소유 모듈에 주입한다.

## 바깥에 남기는 책임

- `walk_entry_context`: 기록 변경과 outbox 예약의 원자성, claim, 원본 revision 재확인, 늦은 결과 저장/폐기, 재시도. 공급 계약과 `collection.collect`만 소비한다.
- `walk_context_backfill`: 기록별 누락 관측의 선택·재수집 예약과 CAS. 카탈로그 및 공급 해시를 소비한다. outbox와 함께 다음 기록 패키징 단계에서 배치한다.
- `walk_diary/collection`: 원자료를 일기 입력으로 변환하고 장면에 적용한다. 공급 패키지는 일기의 내부 자료형이나 채택 정책을 참조하지 않는다.
- 모델·저장소·작업자·CLI: 기존 MVC/운영 계층 유지. Celery task/queue 이름, CLI 진입점, 설정 키를 바꾸지 않는다.

소유권 목록의 background 분류에는 기록별 배경 작업도 포함한다. 그 분류가 outbox 트랜잭션 코드를 공급자 패키지에 넣어야 한다는 뜻은 아니다.

## 동작 보존과 함께 고친 오류

위치/태그별 분기, 공급 결과 상태·이유·시각·retryable, 시간 제한, HTTP 요청, 저장 payload 및 해시를 보존한다. 함수/클래스 본문을 기준 커밋과 비교하면 import를 제외한 차이는 분리된 `collect`와 아래 공원 갱신 호출뿐이다. outbox·재수집·worker 본문은 import 경로만 바뀐다.

기존 `walk_catalog_refresh.cycle`은 존재하지 않는 `walk_park_catalog.refresh`를 호출했다. 공원 카탈로그가 이미 신선하면 드러나지 않지만, 파일이 없거나 오래되면 실패하는 경로다. 실제 함수인 `refresh_catalog`를 호출하도록 수정했고, 파일이 없는 상태에서 다운로드 대역을 실행해 유효 카탈로그가 생기는 회귀 테스트를 추가했다. 따라서 이번 PR은 순수 파일 이동만 포함하지 않는다.

## 검증 범위

- 직접 회귀 15개 파일의 207개 사례: 공급 계약/호환, area/public/weather, 카탈로그 갱신, 기록/pin context, 운영 점검·명령, backfill 명령, 일기 공간 수집/슬롯/card writing.
- 첫 실행에서 201개 통과, 새 테스트의 str/Path 혼동 1건과 Windows PowerShell 실행 정책 5건 실패. 테스트 경로를 수정하고 테스트 프로세스에만 `PSExecutionPolicyPreference=Bypass`, `PYTHONUTF8=1`을 적용해 관련 두 파일 23개 재실행 통과.
- 임시 localhost Postgres/Redis에서 32개 통과, skip 없음: entry context 저장/롤백/늦은 결과, catalog demand, context backfill, Redis 원자 예산/잠금 소유권. 공유 DB를 사용하지 않는다.
- 새 인터프리터에서 조정기를 차단한 공급자 import, 설정/HTTP/DB를 차단한 계약 import, 13개 호환 경로의 객체 동일성을 검사한다. 기존 정규 해시는 기준 커밋에서 계산한 고정값으로 확인한다.
- 마지막 경계 재검증 23개, `uv run --no-sync check`, 변경 Python 56개 Ruff 검사/형식 검사, 소유권 검사와 self-test, `git diff --check` 통과. 실행 명령은 PR #530 본문에 기록했다.
- 소유권 목록은 708개 파일·1,691개 직접 로컬 import·362개 영역 간 관계다. 지연 호환은 AST 목록에 잡히지 않아 별도 명시 목록과 객체 동일성 테스트로 검사한다.

전체 저장소 테스트, 실제 공급자·모델 호출, 실기기 검증은 이번 범위에 포함하지 않는다. 기록/사진, 원본/봉인, 측정/공간 조회의 패키지 재배치와 최종 통합 검증은 후속 단계다.
