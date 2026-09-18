# 설계 — 콘솔 카드 테스트(저장·조회)와 과일·채소 카드 (#592)

2026-09-18 · PR #592 · 결정권자 사용자(09-18 대화) · 구현은 서브에이전트, 리뷰·push 는 컨트롤러

## 무엇을 / 왜

지금 도감 카드는 **달 1~12** 뿐이고, 관리자 콘솔(`/console/search` 「카드」 탭)은 **4월·9월만** 고를 수 있으며
결과를 **저장하지 않는다.** 그래서 ① 달마다 틀이 제대로 도는지 사람이 확인하기 어렵고 ② 과일·채소 카드를
시험할 자리가 없다.

이 카드는 **콘솔에서** 1~12월과 **딸기·상추** 두 장을 뽑아 보고, 뽑은 결과를 **DB 에 저장해 다시 볼 수 있게** 한다.
앱 경로(`/app/ai-cards`)와 앱 계약은 **건드리지 않는다**.

## 정해진 것 (사용자 09-18)

| # | 결정 |
| --- | --- |
| 1 | 과일·채소는 **콘솔 전용**. 앱 API·`ai_cards` 표·한도 규칙·`DAENGS_APP` 계약은 그대로 |
| 2 | 콘솔 결과는 **콘솔 전용 표**에 저장한다. 앱 표(`ai_cards`)를 재사용하지 않는다 |
| 3 | 딸기·상추 틀은 **994×1582(5:8)** 로 맞춘다 — 09-18 채택 완료(`0284c41f`) |
| 5 | 콘솔 목록은 **콘솔 권한을 가진 관리자 전원**이 서로 본다 |
| 6 | 목록에 **삭제**를 둔다 (행 + 저장된 PNG) |
| 7 | 콘솔에서 **엔진을 고른다**(Nano Banana 2 / FLUX.2-klein-4B). GPU 경로일 때만 seed 직접 입력 가능(비우면 자동) |
| 8 | 과일·채소 프롬프트는 **얼굴만 교체**. 딸기 앞말은 **`BERRY`**, 상추는 `LETTUCE` |
| 9 | 딸기 제목판 윗선은 **`top_y=62`**(측정값 59 를 쓰면 글자가 3px 내려간다) |

같은 PR 에 들어가는 제목 작업(앞서 결정): 1·3·5월 앞말 `SEBAE`·`SCHOOL`·`HOME`, 제목 축소 판정 바로잡기,
높이 26 → 장평 70% → 높이 20 순서, 그래도 넘치면 그대로 둔다.

## 구조

### ① 카드 식별자 — 달 정수에 문자열 종류를 더한다

지금 `daengs_cardimage` 는 **달 정수**가 곧 카드 키다(`_CARDS: dict[int, MonthCard]`, `template_path(month)`,
`require_open(month, open_months)`). 과일·채소는 달이 아니므로 키를 넓힌다.

```python
CardSelector = int | str        # 1~12 = 달, "strawberry" · "lettuce" = 종류
```

- `MonthCard` 는 이름을 `CardDef` 로 바꾸지 않는다(변경 폭을 줄인다). 대신 필드를 둘 더한다.
  - `face_only: bool = False` — 본문에 **얼굴만** 보이는 틀(딸기·상추). 10월의 `face_hidden` 과 짝이다.
  - `kind: str = ""` — 달이 아닌 카드의 키. 달 카드는 빈 문자열.
- `_KIND_CARDS: dict[str, MonthCard]` 를 새로 두고, `month=0` 으로 채운다.
- `catalog.resolve(selector) -> MonthCard` 하나만 새로 만든다. 정수면 기존 `_CARDS`, 문자열이면 `_KIND_CARDS`.
  없으면 `MonthNotOpenError`.
- `require_open(month, open_months)` 는 **달 카드 전용 그대로** 둔다 — 앱 경로가 쓰는 잠금이다.
  종류 카드는 `open_months` 를 보지 않는다(콘솔 전용이라 잠글 대상이 아니다).
- `template_path(selector)`: 달이면 `{stem}_template.webp`, 종류면 같은 규칙(`strawberry_template.webp`).

`generate.generate_cards(...)` 는 `month: int` 대신 `card: CardSelector` 를 받는다. 호출자는 둘뿐이다
(`services/ai_card_engine.py`, `backend/tools/*`). 앱 경로는 계속 정수를 넘긴다. `GeneratedCard` 는
`month: int`(달이 아니면 0)와 `card_key: str`(달이면 `"4"`, 종류면 `"strawberry"`)를 함께 갖는다.

### ② 딸기·상추 카드 정의

```python
STRAWBERRY_PLATE = Plate(center_y=105, edge=((62, 749), (149, 676)), top_y=62)   # 09-18 실측, top_y 는 +3 보정
LETTUCE_PLATE    = Plate(center_y=94,  edge=((50, 790), (138, 717)), top_y=47)   # 09-18 실측
```

| | 딸기 | 상추 |
| --- | --- | --- |
| `kind` | `strawberry` | `lettuce` |
| `stem` | `strawberry` | `lettuce` |
| `card_name` | `BERRY` | `LETTUCE` |
| `badge` | `NEO-S0824` | `NEO-0824` |
| `subtitle` | `FRUIT DOG` | `VEGGIE DOG` |
| `face_only` | `True` | `True` |
| `seeds` | `()` (기본 1~6) | `()` |

`scene` 은 틀 그림 그대로 적는다(딸기: 잎 낙하산·금색 줄·분홍 궤적·금색 씨앗·파스텔 하늘·분홍 구름,
상추: 물방울 맺힌 곱슬 상추잎·떠다니는 잎 조각·무지개 광선·바닥 반사). `outfit` 은 종류 카드에서 쓰지 않고
빈 문자열로 둔다 — 아래 `face_only` 앞부분이 소품 금지 문장을 직접 갖는다.

### ③ 프롬프트 — `face_only`

`build_prompt(..., face_hidden=False, face_only=False)`. 셋은 배타이고, 앞부분만 갈린다.

- 기본(달 카드): 지금 그대로.
- `face_hidden`(10월): 09-18 커밋 `4aab9373` 그대로.
- `face_only`(딸기·상추): 본문에 보이는 것은 **구멍 속 얼굴뿐**이다.
  - 사진 강아지의 얼굴로 **그 얼굴만** 바꾼다(견종·털색·귀 모양·주둥이·눈 색·얼굴 무늬).
  - 좌상단 배지 초상화도 같은 얼굴로 바꾼다.
  - **딸기·상추 몸통, 잎 낙하산·줄기, 구멍 모양과 크기는 그대로.** 몸·다리·발을 새로 그리지 않는다.
  - 사진의 목줄·하네스·옷은 옮기지 않는다.
  - 뒷부분(무대·테두리·빈 제목판·배지 글자·부제·아래 패널)은 지금과 같다. 배지 글자는 `NEO-…` 를 그대로 둔다.

### ④ 콘솔 API

`routers/admin_cardimage.py` 를 넓힌다. 권한은 지금과 같은 `Perm.SEARCH_INSPECT`.

| 메서드 | 경로 | 내용 |
| --- | --- | --- |
| POST | `/admin/cardimage/generate` | `card`(달 `1`~`12` 또는 `strawberry`·`lettuce`) · `dog_name` · `engine`(`gemini`\|`cardgen`, 기본 `gemini`) · `seed`(`cardgen` 일 때만) · 본문 사진. 생성 → 저장 → `CardImageResponse` + `id` |
| GET | `/admin/cardimage/cards` | 최근 목록(기본 50). 관리자 전원 공용. 이미지 바이트는 안 싣는다 |
| GET | `/admin/cardimage/cards/{id}/image` | PNG 스트리밍(인증 필요) |
| DELETE | `/admin/cardimage/cards/{id}` | 행 + 저장된 PNG 삭제 |

- 생성은 지금처럼 **동기**(nginx 300s). 앱 경로의 세마포어·한도·큐는 쓰지 않는다.
- `engine=cardgen` 인데 `DAENGS_CARDGEN_URL` 이 비어 있으면 **503 `cardgen_disabled`**.
- 저장 실패는 생성 결과를 죽이지 않는다 — 카드 PNG 는 응답으로 주고, 저장 실패는 응답 플래그(`stored=false`)와 로그로 남긴다.
- `<img>` 는 Bearer 헤더를 못 싣는다. 콘솔은 **blob fetch**(`apiFetch` → `URL.createObjectURL`)로 그린다.
  앱의 bridge(`/_bridge/download/...`, 무인증)는 쓰지 않는다.

### ⑤ 저장 — 새 표 `admin_ai_cards`

```sql
CREATE TABLE IF NOT EXISTS admin_ai_cards (
    id             UUID PRIMARY KEY,
    admin_user_id  UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    card_key       VARCHAR(20)  NOT NULL,     -- "1".."12" · "strawberry" · "lettuce"
    dog_name       VARCHAR(40)  NOT NULL,
    title          VARCHAR(80)  NOT NULL,
    engine         VARCHAR(20)  NOT NULL,     -- "gemini" · "cardgen"
    seed           INTEGER,
    attempts       SMALLINT     NOT NULL,
    likeness       SMALLINT,
    judge_note     VARCHAR(200),
    storage_key    VARCHAR(200) NOT NULL,
    size_bytes     INTEGER      NOT NULL,
    width          INTEGER      NOT NULL,
    height         INTEGER      NOT NULL,
    elapsed_ms     INTEGER      NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ON admin_ai_cards (created_at DESC);
CREATE UNIQUE INDEX ON admin_ai_cards (storage_key);
```

- 앱 표와 **칸을 공유하지 않는다.** 한도·동시 생성 방어·탈퇴 정리 어느 것도 걸리지 않는다.
- 저장 키는 `admin-ai-cards/{admin_user_id}/{card_id}.png`, 저장 구현은 앱과 같은 `GAIT_STORAGE`
  (`local` 이면 `GAIT_LOCAL_STORAGE_DIR`, `gcs` 면 버킷). `none` 이면 저장을 건너뛰고 `stored=false`.
- `db/init/41_admin_ai_cards.sql` 과 `db/migrations/2026-09-18_admin_ai_cards.sql` + `verify_2026-09-18_admin_ai_cards.sql`
  를 같이 만든다(이미 도는 DB 에는 마이그레이션으로 들어간다. 적용은 사람이, 배포 뒤).

### ⑥ 콘솔 화면

`frontend/app/components/cardimage-inspect.tsx` 한 파일 안에서:
- 카드 고르기: 1~12월 + 딸기 + 상추 (14개). 이름은 `catalog` 와 같은 표기.
- 엔진 고르기: Nano Banana 2 / FLUX.2-klein-4B. 서버가 GPU 경로를 못 쓰면 뒤쪽은 비활성 + 이유 문구.
  그 여부는 새 `GET /admin/cardimage/options` 가 알려 준다(카드 목록·엔진 가능 여부·사진 안내 문구).
  `MONTHS` 하드코딩과 `PHOTO_GUIDANCE` 복제를 이 응답으로 대체한다.
- GPU 경로일 때만 seed 입력칸.
- 생성 결과 아래에 **저장된 카드 목록**(만든 사람·카드·엔진·seed·닮음·시각·미리보기·삭제).

## 하지 않는 것 (YAGNI)

- 앱에 과일·채소 열기, 앱 계약 변경, `ai_cards` 표 변경
- 콘솔 생성의 비동기화·큐·한도
- 딸기·상추 seed 목록 확정(운영 엔진은 seed 를 안 쓴다 — 나중에 GPU 경로를 쓸 때)
- 나머지 과일·채소 21종

## 시험

- `daengs_cardimage`: `resolve` 가 달·종류·없는 키를 가르는지, 종류 카드가 자기 틀·plate·`face_only` 프롬프트를
  쓰는지, 달 카드 프롬프트가 dev 와 **글자까지 같은지**(회귀).
- 제목: 딸기·상추 틀에서 `_plate_shift == 0`, 짧은 이름이 판 안에 들어가는지.
- 콘솔 API: 권한 없으면 403, 없는 카드면 404, `cardgen` 인데 URL 이 비면 503, 생성→목록→이미지→삭제 왕복,
  저장 실패해도 200 + `stored=false`.
- 마이그레이션: `db/init` 과 `db/migrations` 짝(루트 `tools/` 검사), 버리는 Postgres 로 `verify_` 하네스.
- 프론트: `npm run lint`, 로컬에서 콘솔 화면 실제 생성 1장.

## 배포 영향

- dev 머지 시 자동 배포. **`db/migrations/2026-09-18_admin_ai_cards.sql` 을 사람이 적용해야** 콘솔 저장이 돈다
  (안 하면 생성은 되고 저장만 실패하며 `stored=false`).
- 앱 사용자 동작은 바뀌지 않는다. 단 같은 PR 의 제목 작업(앞말·장평)은 **머지 즉시 새 카드 제목에 반영**된다.
