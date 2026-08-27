# DAENGS_geo

댕스(DAENGS) 반려견 케어 서비스의 **지오 백엔드 + Android 기준 클라이언트**.
두 기능이 하나의 위치 인프라(PostGIS 반경 검색 · 좌표 인덱싱 · 영업시간 판정)를 공유한다.

| 기능 | 성격 | 진입 |
|---|---|---|
| **병원/약국 찾기** | 요청-응답 (검색 + 자연어 파싱) | 챗봇 대화 / 일반 메뉴 |
| **산책 기록** | Android foreground service → Room → 종료 시 서버 업로드 → 세션·사실·시설 occurrence | Android 지도 기능 / `/walk` |

## 상태

**기존 스냅샷 검증: 2026-08-24.** 병원/약국 검색 백엔드와 Android 지도 셸이 동작한다.
산책은 Android에서 Activity와 독립된 location foreground service를, 서버에서 세션 API·원순서
fix·`WalkFacts`·정지/시설 occurrence 파생과 원좌표 purge를 구현했다. Android Room 영속 저장과
종료 시 서버 업로드까지 에뮬레이터에서 한 바퀴 관통을 확인했다(결정 #60). process-death 복구와
배터리 대비 업로드 주기는 아직 구현하지 않았다.

**다음 장소 발견 축: 2026-08-26.** [결정 #65](docs/decisions/2026-08-26-place-first-discovery.md)은
병원·약국·카페·여행·미용·숙박을 공통 `Place`로 보고, 원천 category에서 정규화한 `kind`를
검색 입구로 삼는다. 기본 결과는 사실 기반이고 태그·AI는 사용자가 적용하는 제안층이다.
공통 `POST /v2/places/search`는 종류별 독립 그룹·기본 거리순·반려견 입장 3상태 평가와
명시적인 주차 가능 우선 정렬로 구현됐다. 웹 검증 표면 `/facility-map`은 이 계약의 정렬 순서와
주차 사실 3상태 커버리지를 목록으로 표시한다. Android 병원 화면은 아직 전환하지 않았으며
작은 PR로 이어서 이동한다.

```
app/
├── geo/         search(PostGIS) · hours · tagging · polyline                       공용
├── journey/     engine(route+캐시) · advice(개 계수·시간·기온) · spots · handoff  공용 ← POST /journey
├── providers/   MapProvider 4메서드 — kakao/naver/tmap/fake/null, 모드별 선택      공용
├── profile/     Dog/OwnerProfile 계약 + 개 8마리·견주 5명 페르소나                 공용
├── discovery/   의도 → 편집 → 실행계획. state(target/journey/view) · facts · resolver
│   └── refine/  그 상태를 편집하는 방법 — tools · nl · diff · actions
├── place/       공통 PlaceResult · 의료/시설 resolver 조율 · POST /v2/places/search
├── features/
│   ├── hospital/  POST /hospital/search (편집+검색, transport=estimate만)
│   ├── pharmacy/  GET /pharmacy/search (얇음, companion 기본 none)
│   ├── walk/      수집만 — WalkFacts (contracts/walk-record.md). 판정·서술 없음, 테스트로 고정
│   └── scene/     walk 사실의 소비자 — encounter 기하값 → 판정. 규칙표에 버전이 붙는다
├── api/         legacy GET /places/search · canonical POST /v2/places/search · GET /map/static
├── usage/       실제 외부 호출 Gate — 기본 거부, 제한형 dev 정책, 요청/시간당 사용량
└── core/        config · db · clock
android/         Kotlin/Compose — 위치→검색→NAVER 지도 + walk foreground service
```

## 현재 코어와 parked 구현

현재 코어는 [결정 #51](docs/decisions/2026-08-22-walk-as-spine.md)에 따라 장소 데이터,
위치 인프라, Android 위치→검색→지도 셸, 산책 사실 계약이다. 자연어 refine/LLM,
suggested actions는 코드와 테스트가 있지만 제품 코어에서는
**parked**다. 다시 채택하기 전까지 다음 구현 순서나 제품 차별점으로 세지 않는다.

커뮤니티 근거·홈페이지 추출은 [결정 #63](docs/decisions/README.md)으로 **기각**했다 — 원천이
없어서 재료가 생길 경로가 없다. 코드(`app/enrich/`)와 응답의 `evidence[]` 는 제거했다.

parked된 LLM 경계는 `utterance`가 있을 때만 “말 → 툴 호출” 번역 한 겹으로 동작하며 병원
정보를 생성하지 않는다. UI 필터(`edits`)와 자연어는 같은 툴로 수렴한다.

실제 구현 경계: PostGIS 검색·공공데이터 적재·영업시간 판정·태깅·상태 편집·provider 진실성
계약·Usage Gate는 실제 코드다. LLM·경로·프로필은 기본 가짜 또는 미설정이다.
지도 표면은 키를 넣으면 NAVER Dynamic Map + Static Map, 없으면 `/dev`에서만 OSM으로 내려간다.

## 실행

```bash
cp .env.example .env
docker compose up -d            # PostgreSQL 18 + PostGIS 3.6 + pgvector 0.8.6
docker compose ps               # db/api 모두 healthy인지 확인
uv sync
uv run alembic upgrade head     # 스키마 적용. 몇 번을 다시 돌려도 안전하다
docker compose exec -T db psql -U daengs -d daengs < migrations/dev_seed.sql   # 개발용 시드
uv run uvicorn app.main:app --reload
uv run pytest
```

DB 이미지는 팀 공용 환경과 같은 PostgreSQL 18 · PostGIS 3.6 · pgvector 0.8.6 조합이다
(`docker/postgres/Dockerfile`). PG18 이미지의 VOLUME 은 `/var/lib/postgresql` 이고 PGDATA 는
그 아래 `18/docker` 라, compose 는 상위 경로를 `pgdata` 볼륨에 마운트한다.

`GET /health`는 프로세스 liveness라 DB를 조회하지 않는다. `GET /health/ready`는 DB에
`SELECT 1`을 실행하며 연결할 수 없으면 503을 반환한다. 외부 지도·경로 provider 장애는 컨테이너
재시작 사유가 아니므로 readiness에서 제외한다.

### 스키마 변경

`alembic` 한 경로다. 옛 `migrations/*.sql` 12개는 리비전 `0001`~`0012` 안에 주석까지 그대로
들어 있어서 두 벌이 되므로 지웠다 — 원문은 git history 에 있다
([migrations/README.md](migrations/README.md)). `migrations/` 에 남은 것은 개발용 시드뿐이다.

```bash
uv run alembic upgrade head                      # 현재까지 적용
uv run alembic revision -m "무엇을 바꾸는지"      # 새 변경 (손으로 쓴다 — 아래 참고)
uv run alembic current                           # 이 DB 가 어디까지 왔나
uv run alembic upgrade head --sql                # 실행하지 않고 SQL 만 출력
```

DB URL 은 `alembic.ini` 가 아니라 앱과 같은 `settings.database_url` 에서 온다. 앱은 asyncpg,
마이그레이션은 동기 psycopg 로 붙는다 (`alembic/env.py`).

**`--autogenerate` 는 꺼져 있다.** ORM 에는 `Place` 하나뿐이라 metadata 를 넘기면 facility ·
walk · anchor 를 비롯한 대부분의 테이블을 "삭제 대상"으로 판단한다. 리비전은 손으로 쓴다.
되살리려면 전체 테이블 metadata 와 PostGIS 시스템 객체 제외 필터가 함께 필요하다.

#### alembic 도입 전부터 쓰던 DB

**일괄 `stamp head` 를 하면 안 된다.** 기존 initdb 방식은 볼륨 최초 생성 때만 돌았기 때문에,
그런 DB 는 007 에서 멈췄는지 011 까지 왔는지 각자 다르다. 뒤처진 스키마를 최신으로 위장하면
이 러너를 넣은 이유가 첫날 무너진다. 그래서 먼저 판별한다:

```bash
uv run python -m scripts.detect_schema_revision
```

스키마 지표로 실제 적용 지점을 찾아 칠 명령을 알려준다 (`stamp 0008` 뒤 `upgrade head` 처럼).
빠진 것 뒤에 이미 존재하는 스키마가 있으면 판별을 포기하고 사람에게 넘긴다 — 틀린 stamp 는
stamp 를 안 한 것보다 나쁘다. 판별 규칙은 `app/core/schema_revision.py`, 테스트는
`tests/test_schema_revision.py`.

Android 앱은 [`android/README.md`](android/README.md)를 따른다. 백엔드와 같은 레포에 있지만
Gradle 프로젝트는 `android/` 아래에 독립되어 있어 Python 실행·테스트와 섞이지 않는다.

### 실제 외부 호출 사용량 Gate

NAVER Static Map, TMAP 실측 경로, OpenAI 자연어 파싱은 모두 같은 Usage Gate를 통과한다.
코드 기본값 `DAENGS_USAGE_POLICY=deny-all`에서는 실제 provider를 호출하지 않는다. 로컬에서
실제 키를 검증할 때만 `dev`를 명시한다. `dev_console`과는 독립된 설정이다.

| operation | dev 요청당 | dev 시간당 | 거부 시 |
|---|---:|---:|---|
| Static Map | 1 | 100 | HTTP 403/429 |
| 실측 경로 | 4 | 60 | `estimate`, `status_reason=usage_denied` |
| OpenAI 파싱 | 1 | 30 | HTTP 403/429 |

dev 누적량은 프로세스 메모리에 있어 재시작하면 초기화되고 여러 worker가 공유하지 않는다.
팀 서비스에 합칠 때 `UsagePolicy`와 `UsageLedger` 조립만 인증 컨텍스트·공유 저장소 구현으로
교체한다. provider와 실행 Gate는 그대로 둔다.

### 실제 병원·약국 데이터 동기화

행정안전부 공공데이터포털의 전국 동물병원·동물약국 인허가 데이터를 `place`에 적재한다.
공공 API는 사용자 검색 시 호출하지 않고 배치에서만 호출한다. 좌표(EPSG:5174)는 적재 시
PostGIS에서 WGS84(EPSG:4326)로 변환하며, 검색 후 선택한 목적지만 기존 `/journey`가 TMAP에 넘긴다.

Alembic 도입 전부터 쓰던 기존 볼륨은 Docker 초기화 SQL을 다시 실행하지 않으므로 먼저 실제
스키마 리비전을 판별한 뒤 마이그레이션한다.

```bash
uv run python -m scripts.detect_schema_revision
# 출력된 stamp 명령을 검토·실행한 뒤
uv run alembic upgrade head
```

```bash
# data.go.kr에서 받은 일반(Decoding) 인증키
DAENGS_DATA_GO_KR_SERVICE_KEY=... uv run python -m app.ingest full

# 이후 매일. 마지막 원천 갱신시점에서 3일 겹쳐 변경분을 다시 받는다.
DAENGS_DATA_GO_KR_SERVICE_KEY=... uv run python -m app.ingest incremental

# 한 종류만 동기화
uv run python -m app.ingest full --kind hospital
uv run python -m app.ingest full --kind pharmacy
```

- 원천: [동물병원 조회서비스](https://www.data.go.kr/data/15154952/openapi.do),
  [동물약국 조회서비스](https://www.data.go.kr/data/15155272/openapi.do)
- `SALS_STTS_CD=01`만 `active=true`; 휴업·폐업·취소·삭제·기타는 검색에서 제외한다.
- 영업상태는 인허가 상태이지 현재 영업시간이 아니다. 이 API는 `hours`, 야간·24시간 진료를 제공하지 않는다.
- 좌표가 없거나 변환 후 대한민국 범위를 벗어난 신규 레코드는 저장하지 않는다. 기존 레코드는 좌표가
  사라져도 폐업 등 상태 변경을 반영한다.

**검증 콘솔**: `http://127.0.0.1:8000/dev` (`DAENGS_DEV_CONSOLE=true` 일 때만 — `.env.example` 에 켜져 있다. 코드 기본값은 닫힘) — 페르소나·출발지(지도 클릭)·필터 칩·자연어 입력, 카드 클릭하면 도보 폴리라인 + 반려견 관심 지점(spots) + 따라가기 딥링크. NAVER Web 서비스 URL은 포트 없이 `http://127.0.0.1`을 등록한다.

현재 공급자 선택·폴백·교체 실험 방법은 [`docs/provider-assembly.md`](docs/provider-assembly.md)에
한 표로 관리한다. 현재 실제 조립 범위는 NAVER Dynamic Map + Static Map이고, 검색은 PostGIS,
지오코딩과 실제 경로 공급자는 보류다.

```
POST /hospital/search
{ "dog_id":"halmae", "origin":[37.4979,127.0276] }                                   ← 메뉴 진입(초안)
{ "dog_id":"halmae", "state":{...}, "utterance":"눈이 뿌옇고 걸어서 갈 데", "shown_ids":[..] }  ← 자연어/음성
{ "dog_id":"halmae", "state":{...}, "edits":[{"tool":"set_walk_max_min","args":{"minutes":15}}] }  ← 필터 UI
{ "dog_id":"dubu",   "state":{"state_version":4,"lat":..,"lng":..,
  "target":{"open_now":true,"night_service":true},"journey":{},"sort":"distance","history":[]} }
→ { state, results[{..., tags, transport{walk{min,m,facilities,advice,why}, car{taxi_fare}, transit}}],
    map{preview_url,deeplink,web_url}, changes[], applied[], question?, reply,
    resolution[], show_call_cta, call_reasons[], actions[] }
```
시나리오 드라이버 예시는 커밋 메시지·`docs/research/2026-08-19-skeleton-run.md` 참고.

## 현재 확정 사항 (2026-08-24)

- 백엔드: **FastAPI / Python**, DB: **PostgreSQL + PostGIS** (팀 pgvector와 동거)
- 기준 클라이언트: **Android(Kotlin) 전용** 앱. iOS 없음. `/dev`는 제품 웹이 아니라 검증 콘솔
- 산책 기록의 런타임 소유자는 Android location foreground service. 시작은 보이는 Activity에서만 하며 `ACCESS_BACKGROUND_LOCATION`은 아직 요청하지 않는다
- 산책 세션의 Room/SQLite 영속 저장과 종료 후 서버 업로드는 구현됨. process-death 복구는 아직 미구현
- 반려견 프로필은 이 레포가 소유하지 않는다 → 외부 계약으로 소비 (`docs/contracts/dog-profile.md`)
- 산책 코어는 `WalkFacts`까지의 사실 수집. 목표·보상·개의 목소리·서술은 선택적 소비자
- 판정이 추가되면 코드를 사용하고 LLM은 확정된 사실의 서술이나 자연어 파싱만 담당

## 문서

[docs/README.md](docs/README.md) 가 지도. 확정(`decisions/`) · 계약(`contracts/`) · 갈래(`explorations/`) · 조사(`research/`)로 나뉘고, 갈래는 status(exploring/adopted/parked/rejected)로 상태를 표시한다.

실행 가능한 것은 [scripts/README.md](scripts/README.md) 가 지도. 운영 도구(최상위) ·
관통 검증 하네스(`verify/`) · 갈래별 측정 스파이크(`spikes/<갈래>/`)로 **수명**에 따라
나뉘고, 스파이크는 갈래가 닫히면 폴더째 지운다.
