# 케어 로그 API — `/app/care-events` (#332)

강아지별 **밥 · 약 · 간식** 기록. 저장소 탭이 "개인비서" 가 되려면 오늘 무엇을 언제 챙겼는지가
남아야 하는데, 서버에는 산책(`walks`)과 대화 요약만 있었다. 이 API 가 그 자리다.

앱 짝 PR 은 **SAJOYO/DAENGS_APP#201** 이고, 응답 모양의 정본은 `backend/src/daengs_backend/schemas/care_event.py` 다.
한쪽만 고치지 말 것.

## 하지 않는 것 둘 — 그리고 **하기로 바꾼 것 하나**

- **산책은 여기 안 적는다.** `walks` 가 이미 진실이라 `kind` 에 `walk` 가 없다 — 한 사실이 두 곳에
  있으면 반드시 어긋난다. 하루 요약이 `walks` 를 세어 같이 보여 줄 뿐이다.
- **이 API 자체는 오케스트레이터를 모른다.** 비서가 이 표를 읽는 것은 #344 가 따로 놓았고,
  비서가 **쓰는** 것은 D-074 가 따로 놓았다 (아래 「채팅에서 기록하기」). 둘 다 이 파일의
  라우터·서비스를 안 고쳤다 —
  `services/care_log_context` 가 `day_summary` 를 건수·마지막 시각으로 좁혀 `context["care_log"]`
  에 얹고, general 답변 프롬프트의 `CARE_LOG_TODAY` 로만 간다. `note` 와 이벤트 목록은 안 넘어간다
  (`docs/orchestration/contracts.md` §1).
- ~~**채팅으로 기록하는 쓰기 능력은 처음부터 안 한다**~~ — **2026-09-13 에 열었다 (D-074).**
  #331 메모가 같이 적어 둔 순서(로그·화면 → 채팅에서 기록 화면으로 HANDOFF → 확인 단계 있는
  자동 쓰기)의 **남은 두 칸**이 그 카드다. "처음부터 안 한다" 가 가리킨 것은 확인 없는
  쓰기였고, 세 번째 칸은 처음부터 열려 있었다. 아래 「채팅에서 기록하기」 절을 보라.

## 경로

전부 앱 회원 전용(`CurrentAppUser`). **그 아이의 구성원(대표 ∪ 돌보미)이 아니면 404** — 403 으로
나누면 그 id 가 존재한다는 것을 알려 주는 셈이라 없는 것과 남의 것을 같은 404 로 뭉갠다
(`/app/pets` 와 같은 규칙). 구성원 판정은 `docs/co-care.md` §2 가 원본이다 — 공동 돌봄 전
에는 대표 1인 소유였고, 여기 적힌 것은 그 뒤의 모양이다.

| 메서드 · 경로 | 하는 일 | 응답 |
| --- | --- | --- |
| `POST /app/care-events` | 기록 한 건 | **201** 새로 만듦 · **200** 같은 `client_event_id` 가 이미 있어 있던 것을 돌려줌 · **409** 약 중복(아래) |
| `GET /app/care-events?pet_id&from&to` | 기간 조회, 최근 먼저 | `{pet_id, start, end, events[]}` — 창을 같이 돌려준다 |
| `GET /app/care-events/today?pet_id&day` | 하루 요약 | `{day, timezone, start, end, meal, medication, snack, walk, events[]}` |
| `DELETE /app/care-events/{id}` | 지움 | **204** · **적은 사람 또는 그 아이의 대표**만. 그 밖에는 404 (돌보미끼리도 못 지운다) |

각 이벤트 응답에 `actor` 가 붙는다 — 누가 챙겼는지다. `{app_user_id, nickname}` 이고, 그 사람이
**지금** 그 강아지의 구성원이 아니면 `nickname` 은 `null` 이다("이전 보호자", `docs/co-care.md`
§3 "이름 표시 규칙"). 이 컬럼보다 먼저 쌓인 기록은 `actor` 자체가 없을 수 있다.

### 약 중복 확인 (409)

`kind`가 `medication`이고 `confirm`을 안 보냈는데, `occurred_at` 앞뒤 6시간 안에 같은 강아지의
다른 약 기록이 있으면 **409** 다 — 밥·간식은 대상이 아니다. 몸이 다치지 않게 막는 불변식이
아니라 **경고**라 동시 기록은 막지 않는다. 자세한 창·순서·동시성 판단은
`docs/co-care.md` §4.

```json
{ "detail": {
    "message": "오늘 08:15에 이미 약을 챙겼습니다.",
    "conflicts": [ { "id": "…", "occurred_at": "…", "note": "심장사상충",
                     "actor": { "app_user_id": "…", "nickname": "아빠" } } ] } }
```

그래도 기록하려면 **같은 `client_event_id` 를 그대로 두고** `confirm: true` 만 붙여 다시
보낸다 — 새 키를 쓰면 재시도가 두 줄이 된다.

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
- `walk` 는 `walks` 에서 센다 — **소유자 조건 없이**, 그 아이가 나간 산책의 `started_at` 이
  그날인 것. 아빠가 올린 산책도 강아지가 같으면 잡힌다(`repositories/walk.py` 의
  `count_for_pet_between`) — 공동 돌봄 전에는 부른 사람 소유의 산책만 셌다. 근거는
  `docs/co-care.md` §2 "산책 읽기".

## 채팅에서 기록하기 (D-074)

`"방금 밥 먹였어"` 라고 하면 비서가 되묻고, 승낙하면 이 API 와 **같은 서비스 함수**
(`services/care_event.record`)로 한 줄이 남는다. 채팅으로 들어온 기록이 화면으로 들어온
기록과 다른 규칙을 통과하는 경로는 없다.

```
사용자: 방금 밥 먹였어
비서  : 14:32에 밥 먹인 걸로 기록할까요?      ← CLARIFY. 아무것도 안 썼다
사용자: 네
비서  : 14:32에 밥 기록했어요.                ← 여기서 care_events 한 줄
```

**이 경로에 모델 호출이 0회다.** 기록 진술인지 · 무엇인지 · 승낙인지를 전부 결정론 어휘가
본다 (`orchestration/care_log.py`). 의미 라우터는 이 기능을 모른다 — `emergency.py` 와 같은
배치이고, 이유는 그쪽보다 강하다: 판정의 결과가 답변이 아니라 DB 행이다.

**기록되는 값에 앱도 모델도 관여하지 않는다:**

| 값 | 출처 |
| --- | --- |
| `kind` | 결정론 어휘. `"밥이랑 약 먹였어"` 처럼 둘이 잡히면 **안 쓴다** |
| `occurred_at` | **서버 시계.** 사용자가 확인 문장에서 그 값을 보고 승낙한다 |
| `client_event_id` | 서버가 만든 제안 id — 같은 제안에 두 번 "네" 해도 한 줄 |
| `note` | **없다.** 사용자가 적지 않은 텍스트를 서버가 지어내지 않는다 |

### 안 쓰는 갈래 (기본값이 이쪽이다)

전부 **기록 화면 HANDOFF** (`target: "care_log"`, `reason: "care_log_entry_required"`) 로
끝난다 — 추측해서 쓰지 않고 사람이 화면에서 적게 보낸다.

- 질문·걱정·긴 발화 → 애초에 이 경로에 안 들어온다 (평소대로 라우팅). `"밥 먹였는데 계속
  낑낑거려"` 가 대표 사례다 — 그것은 기록 의도가 아니라 걱정이고, 가로채면 물은 것에 답이 없다
- 종류가 애매하거나 둘 이상 · 활성 강아지를 모름
- `DAENGS_CARE_LOG_WRITE` 가 꺼짐 (**기본값**) — 이때 기록 의도는 HANDOFF 까지만 돈다
- **약 중복 창 안**(앞뒤 6시간) → ABSTAINED. 채팅이 그 창을 못 건너뛴다: 우리가 받은 확인과
  다른 확인이고, 충돌 목록을 보여 줘야 하는 판단이라 화면의 일이다 (`docs/co-care.md` §4)
- 묵은 제안(1시간, `care_log.PROPOSAL_TTL`)에 뒤늦은 승낙 → 아무 일도 안 일어난다
- 승낙이 아닌 말 → 거절은 고정 문구, 무관한 발화는 제안을 흘리고 평소 라우팅

### 앱이 할 일

`handoffs[].target == "care_log"` 를 받으면 **기록 화면을 연다** (피부·보행 핸드오프와 같은
자리). `clarify.missing == ["care_log_confirmation"]` 은 확인 되묻기라, 승낙/거절 버튼을
붙이면 어휘에 의존하지 않는다 — 버튼이 보내는 값도 그냥 `"네"`/`"아니요"` 면 된다.

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
| Service | `services/care_event.py` — 구성원 판정·멱등·기간 상한·하루 경계·삭제 자격 |
| Controller | `routers/care_event.py` |
| 채팅 쓰기 (D-074) | `orchestration/care_log.py`(결정론 게이트) · `orchestration/planner.py`(`resolve_care_log_route`·`resolve_care_log_write`) · `orchestration/adapters/care_log.py`(쓰는 자리) · `routers/assistant.py`(어댑터 주입) |
| 테스트 | `tests/test_care_events.py` — `fakes.py` 를 안 건드리고 이 파일 안의 가짜를 쓴다 (#331 과 파일이 안 겹치게) |
| 테스트 (채팅 쓰기) | `tests/test_assistant_care_log_write.py` — 같은 꼴의 가짜를 쓴다. 절반이 **안 쓰는** 갈래다 |
| 테스트 (채팅 읽기) | `tests/test_assistant_care_log.py` (#344) |
