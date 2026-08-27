---
status: exploring
depends-on: refine-loop.md, 팀 인증(owner_id) 확정
---
# 검색 세션 기록 — 도달한 과정을 남기고 다시 연다

**발상 (사용자)**: 이 한 번의 검색 과정을 세션으로 저장해두고, 화면을 나가면 자동으로
저장돼서 요약된 제목으로 목록에 뜨고, 열람하면 지도 화면과 도달한 과정을 다시 볼 수 있게.

## 지금은 화면을 나가면 증발한다

무상태 서버라 `EditableState`는 클라이언트가 왕복시킬 뿐이고, 이 레포의 테이블은
`place`와 `ingest_state` 둘뿐이다. 할매 데리고 계단 없는 안과를 30초 걸려 좁혀놔도
앱을 닫으면 처음부터다.

버려지는 게 조건만이 아니다. `target.exclude_ids`("여기는 별로였음")와 `pin_ids`는
**쓸수록 정확해지는 개인 데이터인데 매 세션 버려진다.** 그리고 이 레포의 컨셉이
"평시엔 산책 랜드마크, 유사시 병원 모드"(overview.md)인데, 유사시에 제일 빠른 경로는
새로 검색하는 게 아니라 **저번에 도달했던 조건을 그대로 다시 여는 것**이다.

## history 를 늘리는 것으로는 안 된다

성격이 다른 물건이다.

| | `history` (지금 있는 것) | 세션 기록 (이 갈래) |
|---|---|---|
| 목적 | undo — 작업 중 되돌리기 | 열람 — 나중에 다시 열기 |
| 수명 | 10턴 넘으면 버림 | TTL 까지 보존 |
| 사는 곳 | 클라이언트 왕복 | 서버 DB |
| 단위 | 한 턴 (6df8136 이후) | 한 세션 |

`history`를 안 버리게 만들면 왕복 페이로드가 계속 커진다. **undo 스택은 지금처럼
왕복시키고, 기록은 서버로 따로 뺀다.**

## 조건만 저장한다 (레시피) — 결과 스냅샷 아님

두 갈래가 있고 완전히 다른 물건이 된다.

- **A. 조건만** — 열면 그 state 로 재검색. 저장이 가볍고 병원 정보가 최신이다.
  그때 본 목록과 다를 수 있는데, 그게 **맞는 동작**이다 — 어제 영업중이던 곳이
  오늘도 영업중일 리 없고, 영업 판정은 요청 시각 기준이다(hours.py).
- **B. 결과까지** — 그때 본 목록·거리·경로가 그대로. 열었을 때 닫힌 병원 목록을
  보여줄 위험이 있고, 저장할 것도 크다.

**A + 과정 로그**를 택한다. 발화와 `changes`를 시간순으로 남기면 "도달한 과정"은
텍스트로 보존되고, 지도 화면은 state 의 좌표·반경으로 재현된다.

근거(`evidence`) 저장 문제는 사라졌다 — 커뮤니티 근거 자체가 결정 #63 으로 기각됐다.
001_init.sql 의 "제공사 로컬검색 결과 저장 금지"는 그대로 적용된다.

## 스키마

```sql
CREATE TABLE search_session (
    id             UUID PRIMARY KEY,
    owner_id       TEXT,           -- 선결 (아래)
    dog_id         TEXT,
    title          TEXT,           -- 첫 utterance 그대로. 없으면 changes 조립
    last_state     JSONB,          -- 복원점. state_version 포함해 그대로 박는다
    started_at     TIMESTAMPTZ NOT NULL,
    last_active_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE search_turn (
    session_id      UUID REFERENCES search_session(id) ON DELETE CASCADE,
    seq             INT,
    utterance       TEXT,
    applied         JSONB,         -- ToolCall 목록
    changes         TEXT[],
    state           JSONB,         -- 턴 종료 스냅샷 (snapshot() = history 제외)
    profile_version INT,           -- 그 시점의 진실 버전. 판정 재현에 필요하다
    at              TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_id, seq)
);
```

한 행 = 한 턴 = undo 한 칸. 6df8136 이 undo 단위를 턴으로 맞춰놔서 두 층의 단위가 같다.

## "화면 나감"은 서버가 감지할 수 없다

무상태라 세션 종료를 모른다. 앱이 나갈 때 `POST` 를 부르는 방식은 강제 종료·네트워크
끊김에서 유실된다. **매 요청 upsert** 하면 "화면 나감"이라는 개념 자체가 필요 없어진다
(LLM 서비스들도 대화 종료를 감지하는 게 아니라 매 턴 저장한다).

```python
# 응답을 막지 않는다. 기록 실패 = 로그 한 줄, 검색은 정상
asyncio.create_task(record_turn(body.session_id, st, r, profile))
```

핫패스는 여전히 무상태 — **검색은 이 테이블을 읽지 않는다.** 코드는 `app/record/` 로.
refine 도 journey 도 아닌 별도 관심사다.

`session_id` 는 `EditableState` 안이 아니라 요청/응답 바디에 둔다. 세션 식별자는
사용자가 편집하는 조건이 아니라 전송 메타다(출처 축 — planning/facts.py). 첫 요청에
없으면 서버가 발급해 응답에 실어준다.

## 복원에 새 엔드포인트는 필요 없다

```
GET /sessions?dog_id=   → [{id, title, last_active_at}]
GET /sessions/{id}      → {last_state, turns:[{utterance, changes, at}]}
```

클라이언트가 `last_state` 를 기존 `POST /hospital/search` 에 넣으면 끝 — 결정 #19 의
딥링크 진입과 **같은 경로**다. 저장된 옛 state 는 `state_version` 마이그레이션(564272c)이
받아주고, 경계 검증도 그대로 통과한다.

## 제목은 LLM 으로 만들지 않는다

LLM 서비스들이 제목을 LLM 으로 뽑는 건 대화가 자유형이라 달리 방법이 없어서다. 여기는
다르다 — `changes` 가 이미 **서버가 결정론적으로 만든 사람 읽는 문장**이다.

```
"눈이 뿌옇고 걸어서 갈 데"          ← 첫 발화 그대로 (사용자 자신의 말이라 제일 알아보기 쉽다)
안과 · 도보 · 계단 제외 · 1km       ← 발화 없이 필터만 만진 세션은 changes 조립
```

결정 #7("판정은 코드, LLM은 서술/파싱만")과 같은 선이다. 제목 생성은 LLM 의 새 용도를
하나 여는 건데 얻는 게 없다.

## TTL

`last_active_at` 기준 90일. 환경은 굳이 계속 들고 있을 필요가 없다는 원칙(facts.py)을
기록층에도 적용한 것. 청소는 mois 증분 동기화와 같은 자리에서.

## 검증

`/dev` 콘솔에 세션 목록 패널을 얹어 루프(저장 → 목록 → 복원)를 눈으로 본다.
"화면에 올려야 뭘 버릴지 정해짐"(결정 #23).

## 선결 — owner_id

**이 레포에 사용자 ID 가 없다.** 프로필도 외부 계약으로 소비하고(결정 #4) 인증 코드가
0줄이다. `dog_id` 만으로 임시 운영하면 "같은 개를 보는 두 가족"이 세션을 섞는다.
backlog.md 의 "팀에 요청/확인"(프로필 API·인증)에 얹어 확정된 뒤 시작한다.

## 미결
- [ ] owner_id 출처 — 팀 인증이 무엇을 주는가
- [ ] 세션 경계: 며칠 뒤 같은 조건으로 다시 검색하면 새 세션인가 이어붙이는가
- [ ] 목록에 보일 개수·정렬, 삭제(사용자 요청 삭제는 즉시 hard delete)
- [ ] 산책도 같은 기록층을 쓰는가 (스키마가 hospital 전용 이름을 안 쓴 이유)
