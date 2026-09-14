# 산책 기록 봉투 저장과 비동기 수집

2026-09-08 최초 연결 기준. [Dev #339](https://github.com/SAJOYO/DAENGS_dev/pull/339).
2026-09-09에 추가한 SGIS 행정동·공원 제공자 및 다섯 번째 태그는
[공공 배경 공급](public-context.md)을 따른다. 아래 초기 상태 설명과 구분한다.
Geo의 [기록/봉투 실험 #260](https://github.com/rkbuhtig/DAENGS_geo/pull/260)을
현재 서버의 행동·자유 글 원본에 연결한다. 원본의 관측 시각·위치·내용을 바꾸지 않는다.

## 저장과 처리

`PUT /app/walks/{walk_id}/entries/{entry_id}`의 기존 요청/응답은 그대로다.
`DAENGS_WALK_ENTRY_CONTEXT_ENABLED=true`이면 동일 트랜잭션 안에서 원본과 수집 작업
네 개를 저장한다. 별도 Redis 전송이 없어 커밋 직후 프로세스가 죽어도 DB에 예약이 남는다.
외부 API 장애는 기록 저장과 무관하지만, DB의 예약 INSERT 실패는 원본 저장도 롤백한다.

작업 키는 `walk_id + entry_id + revision + policy_version + tag`다.
같은 mutation 재전송으로 중복 작업을 만들지 않으며, 수정되면 이전 버전의 진행 작업을
취소한다. 기존 성공 봉투는 이전 버전의 자료로 남고 현재 버전에 재사용하지 않는다.
삭제 표식(SQL NULL 또는 JSON null) 업데이트는 DB 트리거가 모든 버전의 작업과 봉투를 지운다.
기능 flag를 꺼도 이 정리는 동작한다. 산책/회원 삭제도 기존 FK cascade에 연결된다.

워커는 다음의 짧은 트랜잭션 둘을 사용한다.

1. `FOR UPDATE SKIP LOCKED`로 due 작업 하나 획득, 45초 lease와 고유 token 저장 후 커밋.
2. DB 세션을 닫은 상태에서 수집. 이후 산책 행 → 작업 행 순서로 잠그고,
   현재 원본 버전·삭제 여부·lease token·만료 시각을 확인한 뒤 결과를 append하고 커밋.

삭제/수정/lease 재획득과 겹친 옛 응답은 버린다. 새 버전에 자동 귀속하지 않는다.
HTTP 429/5xx/전송 오류는 30초, 60초 간격으로 최대 세 번 시도한다.
세 번째 시도의 워커가 죽어도 만료 뒤 `failed`로 끝내며 무한 재획득하지 않는다.
403/응답 형식 오류는 재시도하지 않는다. 외부 API 에러 본문과 요청 URL은 로그/봉투에 남기지 않는다.

작업 상태와 봉투 상태는 다르다. `completed`는 처리 종료이며 수집 성공을 뜻하지 않는다.
봉투의 `known / partial / empty / unavailable / not_requested`를 함께 읽는다.
`failed`의 `attempts_exhausted`는 처리 시도 소진이고, 마지막 시도가 중단됐다면 이전 시도의
봉투 또는 null이 반환될 수 있다. 소진된 작업의 수동 재수집/정책 변경 backfill은 이번에 없다.

## 지금 연결한 원천

| 태그 | 현재 구현 |
|---|---|
| `space.facility` | 기존 Place `/v2/places/search`, 250m, leisure/cafe/restaurant 각 10개 상한 |
| `space.park` | `not_requested / provider_not_connected` |
| `space.river` | `not_requested / provider_not_connected` |
| `environment.weather` | 기존 Life 과거 조회의 격자 기온 저장 (아래 현재 연결 참고) |

Place의 여가 범주에 공원이 있어도 공원 전용 수집 성공으로 바꾸지 않는다.
전체 주변 시설을 빠짐없이 수집한 결과가 아니라 선택한 세 범주의 제한된 결과다.
응답의 key(source/ref), name, distance_m, kind를 보존하며 전화번호 등 불필요한 필드는 제외한다.
등록 위치와의 거리일 뿐 방문·공원 내부·행동 원인이 아니다.
partial 표시와 응답 해시, 조회 시각, 정책 버전을 남긴다. 조회 시각을 과거의 현장 관측으로
바꾸지 않는다. 원문/반려견 이름은 Place로 전송하지 않고 좌표·반경·범주만 보낸다.
LLM과 data.go.kr/SGIS 키를 새로 연결하지 않는다.

위치 없는 글도 작업은 생성하고 워커가 HTTP 없이 `not_requested / no_location` 봉투를 남긴다.
무엇을 모르는지와 수집하지 않은 이유를 읽을 수 있게 하기 위한 처리다.

## 읽기 API

`GET /app/walks/{walk_id}/entries/{entry_id}/contexts`는 기존 앱 인증을 사용한다.
소유권 확인 뒤, repeatable-read snapshot에서 현재 원본 revision의 태그별 최신 시도 봉투만 읽는다.
다른 소유자/없는 원본/삭제 표식은 404다. 기능을 끄면 원본 소유권 확인 후 `disabled`와 빈 sources를
반환하며 새 테이블을 조회하지 않는다. 활성화 전 기록은 `not_requested`이며 일괄 backfill하지 않는다.

원본의 `recorded_at`이 봉투 `target.event_at`이다. 화면에서는 **연월일과 시각**을 함께 표시해야 한다.
실제 작성 시각 컬럼이 없는 현 서버에서 수집 시각을 작성 시각으로 대신 쓰지 않는다.
Geo wire를 그대로 복제한 것은 아니다: 현재 서버의 `(walk_id, entry_id, revision)`을 명시한
`walk-entry-context-v1` 계약이며 향후 LLM/근접 검색 어댑터가 변환한다.
기존 `/record-profile/query`의 행동 집계는 이번에 봉투 소비자로 바꾸지 않았다.

## 활성화와 롤백

1. 서버 테이블 소유권과 배포 환경을 확인한다. [마이그레이션 안내](../../db/migrations/README.md)를 따른다.
2. `2026-09-08_walk_entry_contexts.sql`과 `verify_2026-09-08_walk_entry_contexts.sql`을 적용한다.
   신규 볼륨 원본은 `db/init/24_walk_entry_contexts.sql`이다. SQL은 재실행할 수 있다.
3. 웹과 전용 워커에 `DAENGS_WALK_ENTRY_CONTEXT_ENABLED=true`를 설정한다.
   워커는 웹과 같은 DB 및 `DAENGS_PLACE_SEARCH_BASE_URL`에 접근해야 한다.
4. 같은 backend 프로젝트 환경에서 전용 Celery worker와 Beat **하나**를 실행한다.
   기존 crawler/activity 큐에 태스크를 보내거나 별도 라이브러리를 import하지 않는다.

```powershell
# backend/에서, 서버의 기존 앱 환경변수 및 REDIS_URL을 사용한다.
uv run celery -A daengs_backend.tasks.walk_entry_context:app worker --pool=solo --concurrency=1 -Q walk-entry-context --loglevel=INFO
# 별도 프로세스에서 실행. Beat 저장 파일은 다른 Beat와 공유하지 않는다.
uv run celery -A daengs_backend.tasks.walk_entry_context:app beat --schedule walk-entry-context-beat --loglevel=INFO
```

한 태스크는 최대 12개 작업을 순차 처리하고 Beat 주기는 30초다. Place HTTP 연결/읽기 제한은
3초다. 기온 수집은 기존 Life 전송·캐시 예산 위에 15초 상한을 적용한다.
워커·Beat를 운영 프로세스 관리자에 등록하는 배포는 수행하지 않았다.
중단하려면 웹/워커 flag를 false로 바꾸고 전용 worker/Beat를 정지한다.
진행 중 응답은 DB의 버전/삭제 검사 대상이고, 재시작 시 남은 예약과 만료 lease를 다시 처리한다.
새 테이블·삭제 트리거는 유지한다. 기존 산책 기록 API는 flag=false에서 수집 테이블에 의존하지 않는다.

## 검증

로컬 변경 범위: 기록 서비스·라우터·모델 등록·설정·수집과 신규 SQL이다.
전체 기본 CI는 그대로 유지하고 신규 `walk entry context DB` CI에서 PostgreSQL 17을 사용한다.
실제 SQL을 두 번 적용하고 신규 볼륨 SQL/verify도 실행한 뒤 중복 예약·롤백·삭제 cascade·
lease 재획득·버전 차단·SKIP LOCKED를 검증한다. 기존 migration 하네스에도 삭제 트리거 및
FK 변조를 검출하는 케이스를 등록했다.

```powershell
uv run pytest tests/walk/context/test_walk_entry_context.py tests/walk/context/test_walk_entry_context_db.py tests/walk/entries/test_walk_entry_http.py tests/walk/entries/test_walk_entries.py tests/test_config.py tests/test_config_env.py -q
```

DB 통합 테스트는 `WALK_CONTEXT_TEST_DATABASE_URL`로 지정한 localhost의 `walk_context_test`만
허용한다. 없으면 skip하며 운영 설정으로 fallback하지 않는다. 로컬 첫 실행은 **57 passed,
3 skipped**였다. 소유자별 임시 스키마를 만들고 해당 스키마만 정리한다.
실제 Place HTTP/Redis 워커/운영 DB 적용/실기기 연결은 이 로컬 결과의 검증 범위가 아니다.

## 이어받을 곳

**2026-09-11 현재 기온 연결:** `environment.weather`는 기존 Life 어댑터를 통해 기록 시각 이하의
기상청 격자 기온을 저장한다. 봉투의 `temporal_basis`는 `source_observation`이며 `payload`의
관측 시각·격자·출처를 보존한다. 새로운 행동/글 revision의 기존 큐를 사용하고, 미연결로 이미
완료된 과거 job을 자동 재실행하지 않는다. 키·격자 조건·시간 정책·검증 범위는
[파트 슬롯 서비스 연결 4단계](diary-part-slots.md)를 따른다. 기온 외 항목은 후속이다.

- 5단위: 소유자·공간 범위·기간·개수 제한이 있는 근접 기록 조회. 현재 API는 원본 하나 단위다.
- 6단위: App 후보 캐시/카드와 서버 원본 연동.
- **사진:** 현재 Dev에는 산책 사진 원본 동기화가 없다. App 로컬 사진과 Geo의 합성 photo
  참조를 서버 원본이라고 가정하지 않는다. 이미지 업로드/메타데이터/버전/삭제 계약을 먼저
  연결한 뒤 이와 같은 예약·fencing 구조를 적용한다. 이번 PR의 실제 자동 수집 대상은 행동·글이다.
- 공원·하천은 [공공 배경 공급](public-context.md), 기온은 위 현재 연결을 따른다.
- 7단위: 필요한 봉투를 LLM에 공급하는 선택/변환. 현재는 원문을 재서술하지 않는다.
