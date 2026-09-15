# 앱 사용자용 AI 도감 카드 생성 — 설계

2026-09-14 · PR #537 · 선행 #496 (`README.md` · `plan-2026-09-14-phase1.md` Task 10)

## 목표

관리자 콘솔에서만 쓰던 카드 생성(`POST /admin/cardimage/generate`, 저장 없음)을 **앱 사용자가**
쓰게 한다. 앱은 사진 한 장·달·이름을 보내고, 서버가 카드를 만들어 보관하며, 앱은 나중에 조회한다.

같은 PR 에서 생성 로직을 `daengs_backend` 밖의 패키지로 뺀다.

## 정한 것 (2026-09-14 사용자 결정)

| # | 항목 | 결정 | 기각한 것 |
| --- | --- | --- | --- |
| 1 | 패키지 경계 | 생성 로직만 `daengs_cardimage`. 사용자·저장·API 는 `daengs_backend` | 라우터·모델까지 새 패키지(screening 방식) · 별도 컨테이너/Cloud Run(지금은) |
| 2 | 앱 계약 | **비동기** — POST 는 202 + id, 앱이 GET 으로 조회 | 동기(POST 가 30~60초 대기) |
| 3 | 실행 위치 | backend **프로세스 안** 백그라운드 작업 | Celery 워커 |
| 4 | 이미지 규격 | 994×1582(5:8) PNG 그대로, `width`·`height` 를 응답에 | 서버가 3:4 로 자르거나 채우기 |
| 5 | 한도 | 사용자별 동시 1장 + KST 하루 `ready` N장(기본 **1**). 규칙은 함수 하나 — 나중에 통째로 교체 | 제품 규칙 확정(후속) |

**왜 이렇게 나눴나.** 나중에 생성만 Cloud Run 같은 별도 서비스로 뗄 수 있다. 그때 옮길 것은
"사진 → PNG" 부분뿐이고, 그것을 지금 `daengs_cardimage` 로 묶어 두면 떼는 날 할 일은 ① 패키지 앞에
HTTP 한 장 ② backend 의 호출 한 곳을 HTTP 로 바꾸기 둘이다. D-070(실시간 산책 Cloud Run 분리)이
`DAENGS_REALTIME_URL` 갈림길로 같은 일을 했다. 앱 계약이 비동기라 서버 안에서 누가 만드는지
(프로세스 안 → 워커 → 외부 서비스)가 바뀌어도 앱은 모른다.

## 1. 패키지 `daengs_cardimage`

`backend/src/daengs_backend/services/cardimage/` 6개 파일을 `backend/src/daengs_cardimage/` 로 옮긴다.

| 모듈 | 책임 | 바뀌는 것 |
| --- | --- | --- |
| `catalog` | 달 → 틀·카드명·장면·제목판 | 없음 |
| `photo` | 사진 검증·EXIF 회전·1600px 축소 | 없음 |
| `title` | Pillow 제목 얹기 | 없음 |
| `engine` | `CardImageEngine` Protocol + Nano Banana 2 어댑터 | 없음 |
| `judge` | `CardJudge` Protocol + flash-lite 검수 | 없음 |
| `generate` | `generate_card()` 파이프라인 | **`settings` import 와 `default_engine()`·`default_judge()` 를 뺀다** |

- **규칙: `daengs_cardimage` 는 `daengs_backend`·FastAPI·SQLAlchemy 를 import 하지 않는다.**
  설정은 전부 인자로 받는다(`generate_card` 는 이미 그렇다).
- 엔진·검수 객체를 설정에서 만드는 두 함수는 backend 의 `services/ai_card_engine.py`(새 파일)로
  옮긴다. 관리자 라우터와 앱 경로가 둘 다 이것을 부른다 — **backend 에서 생성을 부르는 곳은 이
  모듈 하나**이고, 나중의 서비스 분리 갈림길도 여기다.
- 옛 경로 호환 껍데기는 두지 않는다. 부르는 곳이 `routers/admin_cardimage.py`, `tests/test_cardimage_*`
  7개 + `tests/cardimage_fakes.py`, `tools/cardimage_title.py` 뿐이라 import 만 고친다.
- `backend/pyproject.toml` 의 휠 패키지 목록에 `daengs_cardimage` 를 추가한다(안 적으면 휠에서 조용히
  빠진다). 의존성 변화는 없다(Pillow·google-genai 는 이미 backend 의존성).
- 경계는 테스트로 지킨다: `daengs_cardimage` 아래 모든 모듈의 import 에 `daengs_backend` 가 없음을
  확인하는 테스트 하나.

## 2. DB — 표 `ai_cards`

`db/init/38_ai_cards.sql` + `db/migrations/2026-09-14_ai_cards.sql` + `db/migrations/verify_2026-09-14_ai_cards.sql`.
기본 DB 의 스키마 원본은 SQL 이고, `models/ai_card.py` 가 따라간다.

```sql
CREATE TABLE IF NOT EXISTS ai_cards (
    id            UUID PRIMARY KEY,                       -- 서버가 만든다 (POST)
    app_user_id   UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    dog_id        UUID REFERENCES pets(id) ON DELETE SET NULL,
    month         SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    dog_name      VARCHAR(40) NOT NULL CHECK (length(btrim(dog_name)) > 0),
    title         VARCHAR(80) NOT NULL,
    status        VARCHAR(16) NOT NULL CHECK (status IN ('generating', 'ready', 'failed')),
    error_code    VARCHAR(32),                            -- failed 일 때만
    storage_key   VARCHAR(200) UNIQUE,                    -- ready 일 때만
    generation    VARCHAR(64),
    size_bytes    INTEGER CHECK (size_bytes IS NULL OR size_bytes > 0),
    width         SMALLINT,
    height        SMALLINT,
    likeness      SMALLINT CHECK (likeness IS NULL OR likeness BETWEEN 1 AND 5),
    attempts      SMALLINT CHECK (attempts IS NULL OR attempts BETWEEN 1 AND 2),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status <> 'ready' OR (storage_key IS NOT NULL AND size_bytes IS NOT NULL
                                 AND width IS NOT NULL AND height IS NOT NULL)),
    CHECK (status <> 'failed' OR error_code IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ai_cards_owner_created_idx ON ai_cards (app_user_id, created_at DESC);
-- 사용자별 동시 1장을 DB 가 보장한다 (앱 두 번 누르기가 동시에 들어와도 한 행만)
CREATE UNIQUE INDEX IF NOT EXISTS ai_cards_one_generating_idx ON ai_cards (app_user_id) WHERE status = 'generating';
```

- 마이그레이션은 `IF NOT EXISTS` 라 여러 번 돌려도 안전하다. 배포 **전에** 개발서버·GCP DB 에 손으로
  적용한다(DB 먼저, 코드 나중). 버리는 Postgres 로 도는 검사는 `docs/ci/README.md` 「마이그레이션 변조 하네스」.
- `app_users` 행은 탈퇴해도 남아 CASCADE 가 안 돈다 — 정리는 §6.

## 3. API — `/app/ai-cards`

라우터 `routers/ai_card.py`, 스키마 `schemas/ai_card.py`, 서비스 `services/ai_card.py`, 리포지토리 `repositories/ai_card.py`.
인증은 `CurrentAppUser`. 업로드는 이 저장소 관례대로 **요청 본문 원시 바이트**(multipart 없음), 메타는 쿼리.

| 요청 | 성공 응답 |
| --- | --- |
| `POST /app/ai-cards?month=&dog_name=&dog_id=` (본문: 사진) | `202 AiCardResponse` (`status: "generating"`) |
| `GET /app/ai-cards` | `200 {cards: [AiCardResponse]}` — 최근 것부터, `image_url` 은 항상 `null` |
| `GET /app/ai-cards/{id}` | `200 AiCardResponse` — `ready` 면 `image_url` 포함 |
| `DELETE /app/ai-cards/{id}` | `204` |
| `GET /app/ai-cards/_bridge/download/{storage_key}` | 로컬 저장소일 때 PNG (스키마 비노출, `dogcard.py` 와 같은 방식) |

`AiCardResponse`: `id, dog_id, month, dog_name, title, status, error_code, likeness, attempts, width, height, created_at, image_url`.

### POST 의 순서 — 돈이 나가기 전에 거를 수 있는 것은 전부 동기로 거른다

1. 본문 스트리밍 수신 + 크기 상한(관리자 라우터의 `_read_body` 를 공용으로 옮겨 같이 쓴다) → `413`
2. `dog_name` 공백 → `400 bad_name`
3. `catalog.require_open(month, settings.cardimage_months)` → 닫힌 달 `404 month_closed`
4. 카드 생성 키가 비었거나 틀·글꼴이 없음 → `503 unavailable` (사용자용 문장, 운영자용 예외 메시지를 내보내지 않는다)
5. 저장소가 설정 안 됨(`NotConfiguredStorage`) → `503`
6. `dog_id` 가 있으면 `pet_repo.get_accessible` (공동 돌봄 구성원 포함) — 아니면 `404`
7. `photo.prepare_photo` (스레드) → 형식·디코드 실패 `400`. 결과 JPEG 를 백그라운드로 넘긴다
8. 한도 `check_quota` (§5) → `409 already_generating` / `429 limit_reached`
9. 행 INSERT(`generating`, `title` 은 `title.title_text` 로 미리 계산) + commit. 부분 유니크 인덱스 위반 → `409 already_generating`
10. 백그라운드 작업 시작(§4) → `202`

오류 본문은 기존 관례 `{"code": ..., "message": ...}` 이다. `message` 는 앱이 그대로 띄울 수 있는 한국어 문장.

### 조회 시 상태 정리

`GET` 두 경로는 응답 전에 `generating` 인데 `updated_at` 이 **정리 기준**(§4)보다 오래된 자기 행을
`failed` / `error_code = "interrupted"` 로 바꾸고 commit 한다.

## 4. 실행 — backend 프로세스 안 백그라운드

`services/ai_card.py` 가 모듈 수준에 두 가지를 가진다.

- `_tasks: set[asyncio.Task]` — 작업 참조를 쥐어 GC 로 사라지지 않게 한다 (완료 콜백에서 제거).
- `_slots = asyncio.Semaphore(settings.cardimage_concurrency)` — 서버 전체 동시 생성 수. 기본 **2**.

작업 한 건:

1. 세마포어를 잡는다.
2. `asyncio.to_thread(generate_card, photo=<준비된 JPEG>, content_type="image/jpeg", ...)`.
   엔진·검수는 `ai_card_engine.default_engine()`·`default_judge()`.
3. 성공: 키 `ai-cards/{app_user_id}/{card_id}.png` 로 PNG 저장 → 새 세션(`SessionLocal`)으로 행을
   `FOR UPDATE` 로 잡아 **아직 `generating` 이면** `ready` + 저장 칸 채움 + commit.
   **행이 없거나(삭제·탈퇴) 이미 `failed`(정리됨) 면 방금 쓴 객체를 지운다.**
4. 실패: 행이 아직 `generating` 이면 `failed` + `error_code` 로 갱신. 코드 매핑:
   `EngineError.code`(`upstream`·`no_image`) 그대로, `CardImageUnavailable` → `unavailable`,
   `StorageNotConfiguredError` → `storage`, 그 밖 → `internal` (로그에 예외, 사용자에게는 코드만).

**저장.** `StoragePort` 에는 서버가 바이트를 쓰는 메서드가 없다. `services/ai_card.py` 의
`_store_png(key, data)` 가 `LocalBridgeStorage.write_if_absent` / `GcsStorage.upload_bytes` 를
`isinstance` 로 갈라 부르고, 그 뒤 `stat()` 으로 `generation`·`size_bytes` 를 얻는다.

**정리 기준(stale).** 한 건의 최악 소요는 엔진·검수가 각각 `cardimage_timeout_ms`(기본 120초)를 다 쓰고
재시도까지 하는 경우 `2 × 2 × 120s = 8분` 이다. 그래서 기준은 설정값에서 계산한다:
`stale = 4 × cardimage_timeout_ms + 60초` (기본 **9분**). 이보다 짧으면 정상 진행 중인 작업을
실패로 덮는다.

**재시작.** 배포 재시작과 겹친 작업은 사라지고, 행은 정리 기준이 지난 뒤 조회에서 `interrupted` 가 된다.
사용자는 다시 누른다(실패는 한도에 안 센다).

**워커로 옮길 조건** (D-076 에 같이 적는다): 재시작으로 인한 `interrupted` 가 실제로 보일 때 ·
동시 생성이 backend 응답을 느리게 만들 때 · 서버가 자동 재시도해야 할 때.

## 5. 한도

`services/ai_card_quota.py` 의 `async def check_quota(session, app_user_id, *, now) -> None` 하나.
제품 규칙이 정해지면 **이 함수만** 바꾼다.

- 같은 사용자의 `generating` 행(정리 기준 안쪽)이 있으면 `AiCardBusyError` → `409 already_generating`
- KST 오늘(`created_at` 기준) `status = 'ready'` 행 수 ≥ `settings.cardimage_daily_limit` 이면
  `AiCardLimitError` → `429 limit_reached`
- 설정 `DAENGS_CARDIMAGE_DAILY_LIMIT`(기본 1, `0` 이면 한도 없음), `DAENGS_CARDIMAGE_CONCURRENCY`(기본 2)
- `failed` 는 세지 않는다 — 한도가 1장이라 실패 한 번으로 그날 기회가 사라지면 안 된다. 연타는 동시 1장이 막는다.
- 전체 지출의 바닥은 카드 생성 키의 **별도 GCP 프로젝트 지출 상한**이다(README 「정해진 것 · 키」).
- 관리자 경로 `/admin/cardimage/generate` 는 한도를 받지 않는다(점검용, 저장 없음).

### 2026-09-15 제품 규칙으로 교체 (#543, D-077)

위 규칙은 테스트 단계용이었고 `check_quota` 를 통째로 바꿨다. 지금 규칙:

- 동시 1장 → `409 already_generating` (그대로)
- 같은 `app_user_id`·`dog_id`·`month` 에 `ready`/`generating` 카드가 있으면 `AiCardMonthTakenError` → `409 month_taken`
  「{이름}{은/는} 이미 {달}월 카드가 있어요.」(마지막 글자가 한글이면 받침에 따라 은/는, 아니면 은(는)). `dog_id` 가
  없으면 안 본다. 동시 1장 다음·하루 한도 앞.
- KST 오늘 **`ai_card_usage`** 줄 수 ≥ `daily_limit` 이면 `429 limit_reached`. 줄은 `_finish_ready` 가 `ready` 로 바꾸는
  같은 트랜잭션에서 남기고, 카드를 지워도 남는다(`card_id` FK 없음). 표는 `db/init/39_ai_card_usage.sql`. 하루의
  경계는 카드가 ready 가 된 시각(`used_at`)이다 — 23:59 에 시작해 00:01 에 끝난 카드는 다음 날로 센다.
- 돈 나간 실패 하루 5번 → `429` (그대로)
- `POST` 쿼리 `title_name`(선택, 40자) — 제목에만. `GET /app/ai-cards` 에 `daily_limit`·`daily_remaining`(무제한이면 `null`).
- 탈퇴 정리(`cleanup_for_owner`)가 사용 기록도 지운다.

## 6. 삭제·탈퇴

- `DELETE`: `get_owned(for_update)` → `storage_key` 가 있으면 **객체 먼저** 지우고 행 삭제(dogcard 와 같은
  순서). `generating` 중 삭제도 허용 — 백그라운드가 §4-3 에서 행이 없음을 보고 객체를 지운다.
- 탈퇴: `services/app_auth.py` 가 `card_service.cleanup_for_owner` 를 부르는 자리 옆에서
  `ai_card_service.cleanup_for_owner` 를 부른다. 저장된 객체를 지우고 행을 지운다. 지울 객체가 없으면
  저장소를 건드리지 않는다.

## 7. 테스트

실제 Gemini 는 어떤 테스트도 부르지 않는다 (`tests/cardimage_fakes.py` 의 `FakeEngine`·`FakeJudge`).

| 무엇 | 파일 |
| --- | --- |
| `daengs_cardimage` 가 `daengs_backend` 를 import 하지 않음 | `tests/test_cardimage_boundary.py` |
| POST 202 → 백그라운드 완료 → GET 이 `ready` + `image_url` + 994×1582 | `tests/test_ai_cards.py` |
| 엔진 실패 → `failed` + 코드, 한도에 안 셈 | 〃 |
| 동시 1장 409 · 하루 한도 429 · 한도 0 이면 무제한 · KST 경계 | `tests/test_ai_card_quota.py` |
| 정리 기준 지난 `generating` → 조회 시 `interrupted` | `tests/test_ai_cards.py` |
| 남의 카드·남의 `dog_id` → 404 | 〃 |
| 생성 중 삭제 → 완료 시 객체가 남지 않음 | 〃 |
| 탈퇴 정리에 AI 카드 포함 (`cleanup_for_owner` 단위) | `tests/test_ai_cards.py` |
| 닫힌 달 404 · 키 없음 503 · 사진 400 이 **행을 만들지 않음** | 〃 |
| 부분 유니크 인덱스·CHECK 제약 | 버리는 Postgres 하네스 (`docs/ci/README.md`) — `uv run pytest` 는 건너뛴다 |

기존 `test_cardimage_*` 7개는 import 경로만 바뀌고 그대로 통과해야 한다.

## 8. 설정·문서·배포

- `config.py`: `cardimage_daily_limit`, `cardimage_concurrency` 두 개 추가. `backend/.env.example` 에 주석과 함께.
- `docs/decisions.md` **D-076**: 패키지 경계(생성 로직만 분리, 도메인 패키지는 순수 로직) + 프로세스 안
  비동기 + 워커로 옮길 조건.
- `docs/cardimage/README.md`: 「지금 상태」·「정해진 것」 갱신, 「안 정해진 것」에 제품 규칙. `worklog.md` 한 절.
- CLAUDE.md 폴더 표에 `backend/src/daengs_cardimage/` 한 줄, `cardimage/` 줄의 경로 설명 갱신.
- 배포 영향: `db/init/` 변경 + 마이그레이션 손 적용(개발서버·GCP, 코드보다 먼저). 새 환경 변수는 기본값이
  있어 서버 `.env` 수정은 선택. nginx: `/api/app/ai-cards` 는 즉시 응답이라 기존 `/api/` 블록으로 충분하다.

## 범위 밖

DAENGS_APP 화면 · 제품 규칙(몇 장·어떤 조건) · Celery 워커 · Cloud Run 분리 · 3:4 파생본 · 새 달 추가.

## 설계 대화 뒤 코드에서 확인해 고친 것

- **정리 기준 5분 → 설정에서 계산(기본 9분).** 엔진·검수 타임아웃이 각각 120초이고 재시도가 있어 최악
  8분이다. 5분이면 정상 작업을 실패로 덮는다.
- **`dog_id` 확인은 `get_accessible`(공동 돌봄 구성원 포함).** 카드는 만든 사람 것이지만, 돌보는 강아지로도
  만들 수 있게 했다. 대표만 허용하려면 `get_owned` 로 바꾸면 된다 — 검토 때 정해 주세요.
