# 케어 로그 API — `/app/care-events` (#332)

강아지별 **밥 · 약 · 간식** 기록. 저장소 탭이 "개인비서" 가 되려면 오늘 무엇을 언제 챙겼는지가
남아야 하는데, 서버에는 산책(`walks`)과 대화 요약만 있었다. 이 API 가 그 자리다.

앱 짝 PR 은 **SAJOYO/DAENGS_APP#201** 이고, 응답 모양의 정본은 `backend/src/daengs_backend/schemas/care_event.py` 다.
한쪽만 고치지 말 것.

## 하지 않는 것 셋

- **산책은 여기 안 적는다.** `walks` 가 이미 진실이라 `kind` 에 `walk` 가 없다 — 한 사실이 두 곳에
  있으면 반드시 어긋난다. 하루 요약이 `walks` 를 세어 같이 보여 줄 뿐이다.
- **이 API 자체는 오케스트레이터를 모른다.** 비서가 이 표를 읽는 것은 #344 가 따로 놓았다 —
  `services/care_log_context` 가 `day_summary` 를 건수·마지막 시각으로 좁혀 `context["care_log"]`
  에 얹고, general 답변 프롬프트의 `CARE_LOG_TODAY` 로만 간다. `note` 와 이벤트 목록은 안 넘어간다
  (`docs/orchestration/contracts.md` §1).
- **채팅으로 기록하는 쓰기 능력은 처음부터 안 한다** (2026-09-08 사람 결정, #331 메모). 순서는
  로그·화면 → 채팅에서 기록 화면으로 HANDOFF → 확인 단계 있는 자동 쓰기.

## 경로

전부 앱 회원 전용(`CurrentAppUser`). 내 강아지가 아니면 **404** — 403 으로 나누면 그 id 가 존재한다는
것을 알려 주는 셈이라 없는 것과 남의 것을 같은 404 로 뭉갠다 (`/app/pets` 와 같은 규칙).

| 메서드 · 경로 | 하는 일 | 응답 |
| --- | --- | --- |
| `POST /app/care-events` | 기록 한 건 | **201** 새로 만듦 · **200** 같은 `client_event_id` 가 이미 있어 있던 것을 돌려줌 |
| `GET /app/care-events?pet_id&from&to` | 기간 조회, 최근 먼저 | `{pet_id, start, end, events[]}` — 창을 같이 돌려준다 |
| `GET /app/care-events/today?pet_id&day` | 하루 요약 | `{day, timezone, start, end, meal, medication, snack, walk, events[]}` |
| `DELETE /app/care-events/{id}` | 지움 | **204** · 내 것 아니면 404 |

### 기록 본문

```json
{
  "pet_id": "…",
  "kind": "meal | medication | snack",
  "occurred_at": "2026-09-08T08:00:00+09:00",
  "note": "사료 반만",
  "client_event_id": "…"
}
```

- `occurred_at` 은 **앱이 보낸 시각**이지 서버가 받은 시각이 아니다 — "아침에 먹였는데 저녁에 적는"
  경우가 있다. 받은 시각은 응답의 `created_at`. **timezone 이 없으면 422** — 없는 채로 받으면 어느
  하루에 넣을지 서버가 추측하게 된다. 지금보다 10분 넘게 앞서면 422 — 계획은 알림(F5)의 일이다.
- `note` 는 120자, 공백뿐이면 `null` 로 접는다.
- **`client_event_id` 는 앱이 만든다** (`walks.client_session_id` 와 같은 규칙). 탭 두 번·재시도가 두 줄이
  되면 "밥 2번" 이 되고 아무 에러도 안 난다. 강아지 안에서만 유일하면 된다 — DB UNIQUE `(pet_id,
  client_event_id)`. **같은 키로 다른 내용이 와도 덮어쓰지 않는다** — 고치려면 지우고 다시 적는다.

### 기간 조회

- `from`/`to` 는 timezone 필수. 안 보내면 **최근 7일**, 한 번에 **31일**까지 (`services/care_event.py` 의
  `DEFAULT_RANGE` · `MAX_RANGE`). 넘거나 뒤집히면 422.
- **배웅한 아이의 기록도 그대로 보인다.** 배웅(`farewell_on`)은 행을 안 지우고 있었던 일을 적어 두는
  것이라, 그 아이의 기록도 남는다. 강아지를 **지우면** 기록도 같이 지워진다(CASCADE).

### 하루 요약

- `day` 를 안 보내면 **서울 기준 오늘**이고, 하루의 경계도 서울 자정이다 (`DAY_TIMEZONE`). 서버 시간(UTC)으로
  자르면 밤 9시 뒤의 저녁밥이 "내일" 로 간다. 여행 중 사용자까지 맞추는 `tz` 쿼리는 앱이 필요해지면 그때.
- `walk` 는 `walks` 에서 센다 — 그 아이가 나간 산책의 `started_at` 이 그날인 것.

## 스키마

- `db/init/23_care_events.sql` (새 DB) · `db/migrations/2026-09-08_care_events.sql` + `verify_` (서버 DB, 사람이 적용).
  `tools/check_migration_verification.py` 의 `CHECKS` 에 등록돼 있어 CI 가 멱등키 UNIQUE·kind CHECK·FK 탈락을
  verify 가 잡는지 본다.
- 새 표라 옛 앱·옛 코드에 영향이 없다 — **코드보다 먼저 적용해도 안전하다.**

## 코드

| 층 | 파일 |
| --- | --- |
| Model | `models/care_event.py` |
| Schema | `schemas/care_event.py` |
| DAO | `repositories/care_event.py` · `repositories/walk.py` 의 `count_for_pet_between` |
| Service | `services/care_event.py` — 소유권·멱등·기간 상한·하루 경계 |
| Controller | `routers/care_event.py` |
| 테스트 | `tests/test_care_events.py` — `fakes.py` 를 안 건드리고 이 파일 안의 가짜를 쓴다 (#331 과 파일이 안 겹치게) |
