# 공동 돌봄 — 한 강아지, 여러 보호자

한 마리를 여러 사람이 번갈아 돌본다. 출근 전에는 아빠가, 퇴근 후에는 내가 밥·약·간식·산책을
챙긴다. 지금 서버는 강아지가 계정 하나에 매달려 있어서 이것이 불가능하다.

케어 로그(#332, `docs/care-events.md`) 위에 얹는 후속이다. 앱 짝 PR 이 따로 필요하다 —
한쪽만 고치지 말 것.

## 결정 셋

| 축 | 결정 |
| --- | --- |
| 소유 경계 | **돌봄은 강아지 것, 성취는 사람 것.** `care_events`·`walks` 는 강아지에 붙고 누가 했는지는 actor 로만 남는다. `territory_claims`·`dog_cards` 는 지금처럼 사람 것이다 — 아빠가 걸어서 먹은 땅은 아빠 것 |
| 권한 | **대표 1명 + 돌보미 N명.** 초대·내보내기·프로필 수정·배웅·삭제는 대표만. 돌보미는 기록하고 본다 |
| 실시간성 | **읽기 정확 + 약만 확인.** 소켓도 푸시도 안 넣는다. 교대가 시간으로 갈려 있어 둘이 동시에 앱을 보는 순간이 거의 없다 — 필요한 것은 push 가 아니라 "앱을 여는 순간 오늘 일어난 일이 빠짐없이 보이는 것" 이다 |

## 이 문서 전체를 관통하는 함정

**`app_users` 행은 탈퇴해도 안 지워진다.** `services/app_auth.py:322` 의 `withdraw()` 는
`status='withdrawn'` 으로 바꾸고 암호문만 NULL 로 만든다 (`app_auth.py:367`). 재가입할 때
같은 사람으로 알아보기 위해서다 — 실제로 다시 로그인하면 **같은 행이 되살아난다**
(`app_auth.py:239`).

그래서 `app_users` 를 가리키는 FK 의 `ON DELETE CASCADE` · `SET NULL` 은 **영영 돌지 않는다.**
파기는 전부 `withdraw()` 안의 명시 삭제(`app_auth.py:346-347`)와 트리거가 한다.

이 저장소는 이미 같은 문제를 트리거로 풀어 놨다 — `db/init/21_activity_game.sql:99` 의 주석이
그대로 이 이야기다 (*"app_users survives withdrawal. Privacy cleanup must still run with the
feature disabled."*). 이 문서의 트리거 둘은 그 선례를 따른다.

## 하지 않는 것 넷

- **실시간 산책 중계를 안 한다.** WebSocket·SSE 계층이 통째로 새로 생기고, `walks` 가
  "끝난 산책만 올라온다"는 지금 설계(`models/walk.py` 머리말)를 개방해야 한다.
- **푸시 알림을 안 한다.** 저장소에 디바이스 토큰도 FCM 도 없다. 읽기 정확만으로 시나리오의
  대부분이 풀린다.
- **`walks` 의 소유권을 안 옮긴다.** `walks_client_session_unique(app_user_id, client_session_id)`
  가 그 컬럼에 걸려 있고 좌표·점령지가 매달려 있다. **읽기만** 연다 (§2).
- **가구(household) 개념을 안 만든다.** 강아지 단위 초대로 충분하고, `app_users.room_name`
  (미니룸 이름표)과 개념이 겹쳐 헷갈린다.

---

## §1 스키마

### `pet_members` (신규)

```sql
CREATE TABLE IF NOT EXISTS pet_members (
    pet_id      UUID NOT NULL REFERENCES pets(id)      ON DELETE CASCADE,
    -- ⚠️ 이 CASCADE 는 **안 돈다.** 탈퇴가 app_users 행을 안 지우기 때문이다(위 "함정").
    --    탈퇴한 돌보미를 지우는 것은 아래 트리거 ① 이다. 이 FK 는 언젠가 행을 진짜로
    --    지우는 날을 위한 안전망일 뿐, 여기에 기대면 유령 돌보미가 남는다.
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (pet_id, app_user_id)
);

-- PK 가 (pet_id, …) 라 "내가 돌보는 강아지 전부" 를 못 탄다. 그 조회가 제일 잦다
-- (앱을 켤 때마다). 이 인덱스를 지우지 말 것.
CREATE INDEX IF NOT EXISTS idx_pet_members_app_user ON pet_members (app_user_id);
```

**대표는 여기 없다.** `pets.app_user_id` 가 대표이고 이 표는 돌보미만 담는다. 구성원은 둘의
합집합이다. `role` 칸을 두지 않은 이유가 이것이다 — 한 사람이 양쪽에 동시에 있을 수 없으니
**어긋날 자리가 없다.** 칸을 두는 순간 두 곳을 맞춰야 한다.

`invited_by` 도 없다. 초대는 대표만 하므로 값이 항상 대표라 정보량이 0 이다.

### `pet_invites` (신규)

```sql
CREATE TABLE IF NOT EXISTS pet_invites (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pet_id     UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    -- 대표가 바뀌면 이전 대표가 뿌린 초대를 무효로 보는 데 쓴다. pet_members 와 달리 여기서는
    -- 값이 변하므로 정보량이 있다. 이 CASCADE 도 위와 같은 이유로 안 돈다 — 트리거 ① 이 지운다.
    invited_by UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    token_hash CHAR(64) NOT NULL UNIQUE,   -- 평문은 저장하지 않는다 (refresh_tokens 와 같은 규칙)
    expires_at TIMESTAMPTZ NOT NULL,       -- 24시간
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pet_invites_pet ON pet_invites (pet_id);
```

토큰은 `core/token.py` 의 `generate_refresh_token()`(`secrets.token_urlsafe(32)`)과
`hash_refresh_token()` 짝을 그대로 쓴다 — **새 암호 코드를 한 줄도 안 쓴다.**

`accepted_at` 칸이 없다. **수락하면 행을 지운다** — 그것만으로 일회용이 되고, "이미 쓴 초대"와
"없는 토큰"이 같은 404 가 되어 정보도 덜 샌다.

### `care_events` 변경

```sql
ALTER TABLE care_events RENAME COLUMN app_user_id TO actor_app_user_id;
ALTER TABLE care_events ALTER COLUMN actor_app_user_id DROP NOT NULL;
-- FK 도 CASCADE → SET NULL. '소유자' 였을 땐 같이 지우는 게 맞았지만, '챙긴 사람' 은
-- 떠나도 "그날 밥을 먹은 사실" 은 강아지의 것으로 남아야 한다.
```

**이름을 반드시 바꾼다.** 안 바꾸면 `models/care_event.py:45` 의 *"`pets.app_user_id` 와 같은 값"*
주석을 믿은 다음 사람이 이것을 소유자로 읽고 권한 검사를 잘못 짠다.

안 건드려도 되는 것 둘 — 멱등키 `UNIQUE(pet_id, client_event_id)` 는 이미 강아지 기준이라 두
사람의 앱이 각자 만든 UUID 가 안 부딪힌다. `idx_care_events_pet_occurred` 도 조회 축이 이미
강아지라 공동 돌봄에서 오히려 더 맞는다.

### 트리거 둘

① `app_users` 는 탈퇴해도 살아남으므로 FK 로는 절대 안 지워진다.

```sql
CREATE OR REPLACE FUNCTION pet_membership_owner_cleanup() RETURNS trigger LANGUAGE plpgsql AS $func$
BEGIN
    IF NEW.status = 'withdrawn' THEN
        DELETE FROM pet_members WHERE app_user_id = NEW.id;
        DELETE FROM pet_invites  WHERE invited_by  = NEW.id;
    END IF;
    RETURN NEW;
END $func$;
DROP TRIGGER IF EXISTS pet_membership_owner_cleanup ON app_users;
CREATE TRIGGER pet_membership_owner_cleanup AFTER UPDATE OF status ON app_users
FOR EACH ROW EXECUTE FUNCTION pet_membership_owner_cleanup();
```

② 대표가 돌보미로도 들어오는 것을 DB 가 거절한다. 서비스 검증과 겹치지만, 겹치는 것이
정합성 제약의 목적이다.

```sql
CREATE OR REPLACE FUNCTION pet_members_not_owner() RETURNS trigger LANGUAGE plpgsql AS $func$
BEGIN
    IF NEW.app_user_id = (SELECT app_user_id FROM pets WHERE id = NEW.pet_id) THEN
        RAISE EXCEPTION '대표는 돌보미가 될 수 없다 (pet=%, user=%)', NEW.pet_id, NEW.app_user_id;
    END IF;
    RETURN NEW;
END $func$;
DROP TRIGGER IF EXISTS pet_members_not_owner ON pet_members;
CREATE TRIGGER pet_members_not_owner BEFORE INSERT OR UPDATE ON pet_members
FOR EACH ROW EXECUTE FUNCTION pet_members_not_owner();
```

### 마이그레이션

`db/init/24_pet_members.sql` 에 위 전부를 두고, **이미 도는 DB 용으로 `db/migrations/` 에 같은
내용을 하나 더** 둔다 (`db/init/` 은 볼륨이 빌 때만 돈다). 버전 테이블이 없어 **여러 번 돌려도
안전해야 하므로**, 멱등하지 않은 `RENAME` 은 `information_schema` 확인으로 감싼다.

**backfill 이 없다.** 기존 강아지의 대표는 `pets.app_user_id` 에 그대로 있고 `pet_members` 는
빈 채로 시작한다.

---

## §2 접근 판정

`Pet.app_user_id == <부른 사람>` 으로 접근을 판정하는 곳은 리포지토리 다섯 파일뿐이다. 서비스도
라우터도 이 조건을 직접 안 쓴다. **한 층에서 갈아끼울 수 있다는 뜻이다.**

`get_owned` 를 고치지 않고 옆에 하나를 더 둔다 — 이름이 갈려 있어야 잘못 부른 것이 눈에 띈다.

```python
# repositories/pet.py
def _is_member(app_user_id):          # 대표 ∪ 돌보미
    return or_(
        Pet.app_user_id == app_user_id,
        Pet.id.in_(select(PetMember.pet_id).where(PetMember.app_user_id == app_user_id)),
    )

async def get_owned(...)      -> Pet | None:   # 대표만. 지금 그대로 (repositories/pet.py:58)
async def get_accessible(...) -> Pet | None:   # 구성원. 새로 추가
```

> **`delete_all_for_owner` 는 대표 기준을 유지한다.** 여기를 구성원으로 바꾸면 **돌보미가
> 탈퇴할 때 남의 강아지를 지운다.**

| 위치 | 판정 | 이유 |
| --- | --- | --- |
| `services/pet.py` 조회 | 구성원 | 프로필 보기 |
| `services/pet.py` 수정·배웅·삭제·사진 | **대표만** | 되돌리기 어려운 것들 |
| `services/care_event.py:61` `_owned_pet` | 구성원 | 기록·조회. 이름도 `_accessible_pet` 으로 |
| `repositories/chat.py:17,26` | 구성원 | "이 아이 얘기를 해도 되나". 대화 **세션**의 소유는 `chat_sessions.app_user_id` 로 따로 걸려 아빠 대화가 나에게 안 샌다 |
| `repositories/gait_record.py:27` | 구성원 | 보행은 강아지의 건강 데이터 |
| `repositories/walk_entry.py:40` | 구성원 | 산책 기록 편집 |
| `repositories/territory_claim.py:38` | 구성원 | 아빠가 걸어서 점령하려면 그 아이에 닿아야 한다. 점령 **결과**는 `territory_claims.app_user_id` 라 여전히 아빠 것 |

### 산책 읽기 — 한 줄

`repositories/walk.py:127` 의 `Walk.app_user_id == app_user_id` 를 **뺀다** (구성원 조건으로 갈지
않고 삭제). 부르는 쪽인 `care_event.day_summary` 가 이미 강아지 접근 권한을 확인한 뒤라, 여기서
다시 사람으로 거르면 **아빠의 산책만 빠진다.** `walk_pets` 조인이 이미 "그 아이가 나간 산책" 을
정확히 집는다. 함수 docstring 의 *"소유자 조건은 `walks.app_user_id` 로 겁니다"* 도 같이 고친다 —
그 문장이 이 결정으로 거짓이 된다.

**산책 쓰기는 안 건드린다.** 각자 자기 산책을 올리고 요약에서만 합쳐 보인다.

### 마릿수 상한의 의미가 바뀐다

`MAX_PETS_PER_USER`(`services/pet.py:29`)는 남용 한도가 아니라 **미니룸 렌더링 제약**이다 —
주석이 *"12×12 격자에 가구가 차 있고 강아지 간격이 1.3칸"* 이라고 적어 놨다.

그러면 상한은 소유가 아니라 **"내 방에 서는 아이 수"** 여야 한다. **등록과 초대 수락**에서
구성원 기준(`count_accessible`)으로 검사한다. 수락을 안 보면 아빠가 자기 강아지 5마리를
키우면서 초대를 수락해 방이 6마리가 되는 길이 열린다.

**승계는 다르다.** 승계는 이미 방에 서 있는 아이의 소유만 옮기므로 `count_accessible` 로
보면 **항상 통과한다** — 거기서 늘어나는 것은 소유뿐이라 `count_for_owner` 로 봐야 한다.
같은 상한값(`MAX_PETS_PER_USER`)을 쓰되 세는 대상이 다르다.

> **전제**: 돌보미의 미니룸에도 그 아이가 선다. 앱이 "내가 등록한 아이만 방에 세운다" 로 가면
> 상한은 소유 기준으로 남고 **승계에서만** 검사하면 된다. 앱 결정이므로 두 갈래를 다 적어 둔다.

---

## §3 초대 · 승계 · 탈퇴

### API

| 메서드 · 경로 | 권한 |
| --- | --- |
| `POST /app/pets/{pet_id}/invites` | 대표 |
| `DELETE /app/pets/{pet_id}/invites/{id}` | 대표 |
| `GET /app/pets/{pet_id}/members` | 구성원 |
| `POST /app/pet-invites/accept` | 로그인 사용자 |
| `DELETE /app/pets/{pet_id}/members/{user_id}` | 대표 또는 본인 |
| `POST /app/pets/{pet_id}/owner` | 대표 |

수락 경로가 `/app/pets/{pet_id}/…` 아래가 **아닌 것이 의도다.** 수락 전에는 그 강아지에 아무
권한이 없어서, URL 에 `pet_id` 를 실으면 남의 강아지 id 를 넣어 보는 자리가 생긴다. 토큰만 받는다.

새 파일 `routers/pet_member.py` · `services/pet_member.py` · `repositories/pet_member.py`.

### 초대 생성

유효 초대는 강아지당 **3개**까지. 새 초대를 만들 때 그 강아지의 **만료된 행을 같이 지운다** —
아무도 링크를 안 누르면 `pet_invites` 가 영원히 쌓인다.

### 수락 — 검증 순서

**`pets` 행에 `SELECT … FOR UPDATE` 를 걸고 시작한다.** 수를 세고 INSERT 하는 사이에 다른 수락이
끼면 상한을 넘긴다. 락 대상이 `pet_members` 가 아니라 **`pets` 여야** 아래 탈퇴 가드와 같은
자원을 두고 줄을 선다.

| # | 조건 | 응답 |
| --- | --- | --- |
| 1 | `token_hash` 없음 | 404 |
| 2 | `expires_at` 지남 | 410 + 행 삭제 |
| 3 | `invited_by ≠ pets.app_user_id` (그새 대표가 바뀜) | 410 |
| 4 | 수락자가 이미 `pets.app_user_id` | 409 |
| 5 | 이미 `pet_members` 에 있음 | **200** (멱등 — 카톡 링크는 두 번 눌린다) |
| 6 | 구성원이 `MAX_MEMBERS_PER_PET`(5) 이상 | 409 |
| 7 | 수락자의 미니룸 상한 초과 | 409 |

수락 부수효과 — 수락자의 `primary_pet_id` 가 NULL 이면 그 아이로 채운다.
`services/pet.py:99` 에 이미 같은 코드가 있다 (강아지 등록 때).

돌보미로 참여할 수 있는 강아지 수에는 **상한을 두지 않는다.** 참여는 초대를 받아야만 되므로
남용 경로가 아니다.

### 승계 (`POST /app/pets/{pet_id}/owner`)

대상은 이미 `pet_members` 에 있어야 한다 — 승계와 초대를 한 번에 하지 않는다.

> **순서가 중요하다.** 옛 대표를 `pet_members` 에 먼저 넣으면 트리거 ②가 터진다. 그 순간
> 옛 대표는 아직 `pets.app_user_id` 다.

1. `SELECT … FOR UPDATE` on `pets`
2. 새 대표의 **소유** 상한 검사(`count_for_owner >= MAX_PETS_PER_USER`) → 넘으면 409.
   여기만 `count_accessible` 이 아닌 이유는 §2 끝에 있다
3. **`pets.app_user_id` = 새 대표** ← 먼저
4. 새 대표의 `pet_members` 행 DELETE
5. 옛 대표를 `pet_members` 에 INSERT
6. 이 아이의 남은 `pet_invites` 삭제 (`invited_by` 가 옛 대표)

**옛 대표의 `primary_pet_id` 는 건드리지 않는다.** 승계 후에도 돌보미로 남아 그 아이에 계속
접근하므로 그 값은 여전히 유효하다. (`services/pet.py:343-348` 의 승계 로직은 강아지 *삭제* 용이라
여기 갖다 쓰면 안 된다.)

### 퇴장 · 내보내기

`DELETE /app/pets/{pet_id}/members/{app_user_id}` 하나로 둘 다 한다 — 대표가 부르면 내보내기,
본인이 부르면 나가기. **대표는 이걸로 자기를 못 뺀다** (승계 엔드포인트로 가야 한다).

부수효과 — 나간 사람의 `primary_pet_id` 가 그 아이였으면 비운다. FK 가
`ON DELETE SET NULL`(`db/init/05_pets.sql:201`)이지만 **강아지 행은 안 지워지므로 안 돈다.**
대체할 아이는 남은 **구성원** 강아지 중 `list_for_owner` 의 정렬(`created_at, id`)로 고른다.

### 탈퇴 가드

`services/pet.py:369` 가 이미 `list_for_owner_for_update` 로 `pets` 를 잠근다. **그 락 안쪽에**
가드를 넣는다 (`withdraw()` 앞부분이 아니다 — 검사와 삭제 사이에 돌보미가 들어오면 강아지를
날린다).

> 내가 대표인 강아지 중 `pet_members` 에 사람이 남은 것이 하나라도 있으면 → **409**,
> 메시지에 그 강아지 이름들.

**409 메시지는 반드시 두 출구를 같이 준다** — ⓐ 승계하고 탈퇴, ⓑ 돌보미를 내보내고 탈퇴(그러면
강아지는 지금처럼 함께 파기). 탈퇴를 영구히 막는 모양이 되면 안 된다. 앱은 이 메시지를 그대로
화면으로 옮긴다.

돌보미로만 참여 중인 사람의 탈퇴는 **안 막는다.** 그냥 나가고 트리거 ①이 정리한다.

### 이름 표시 규칙

> **이름은 그 사람이 *지금* 이 강아지의 구성원일 때만 보여 준다. 아니면 "이전 보호자".**

`withdrawn` 체크가 따로 필요 없다 — 트리거 ①이 탈퇴 시 `pet_members` 를 지우므로 탈퇴자는 자동으로
비구성원이 된다. **재가입해도** 다시 초대받기 전엔 "이전 보호자" 다. 이 규칙이 없으면 탈퇴자가
나중에 재가입할 때(같은 행이 되살아난다) 옛 기록이 갑자기 그 사람의 **새 닉네임**으로 뜬다 —
우리 집 화면에 지금은 남인 사람의 현재 닉네임이 뜨는 것이다.

`GET …/members` 와 케어 로그 응답의 `actor` 필드가 같은 헬퍼를 쓴다.

---

## §4 약 중복 방지

```python
# services/care_event.py
#: 약 중복 확인 창. 새 기록의 occurred_at 앞뒤로 이만큼 안에 다른 약 기록이 있으면 확인을 받는다.
#:
#: **하루(서울 자정) 가 아닌 이유**는 1일 2회 투약이 정상이기 때문이다 — 하루로 잡으면 저녁 약마다
#: 경고가 떠서 사람이 경고를 안 읽고 누르는 습관이 든다. 실제 위험은 교대 경계의 짧은 중복이라
#: 6시간이면 잡히고 12시간 간격은 안 걸린다.
MEDICATION_CONFIRM_WINDOW = timedelta(hours=6)

#: 확인을 받는 종류. **밥·간식은 안 받는다** — 한 번 더 줘도 위험하지 않고, 경고가 잦으면 정작
#: 약 경고가 안 읽힌다. 종류를 늘리려면 이 집합만 고친다.
CONFIRM_KINDS = frozenset({"medication"})
```

### `record()` 안의 순서

```
1. 강아지 접근 확인 (§2 의 _accessible_pet)
2. client_event_id 로 조회 → 있으면 200 그대로 반환   ← 중복 검사를 하지 않고 빠져나간다
3. kind 가 CONFIRM_KINDS 이고 body.confirm 이 False 면
       occurred_at ± 6시간 안의 같은 kind 조회 → 있으면 409
4. INSERT
```

**2번이 3번보다 먼저여야 한다.** 뒤집으면 멱등성이 깨진다 — 사용자가 확인하고 기록한 직후
네트워크가 끊겨 앱이 재시도하면, 방금 자기가 만든 행을 중복으로 보고 409 를 낸다.

창은 **`occurred_at` 기준이고 앞뒤 대칭**이다. "아침에 먹였는데 저녁에 적는" 경우가 있어서
(`db/init/23_care_events.sql`), 늦게 적은 아침 약도 이미 적힌 아침 약과 부딪혀야 한다.

### 앱 계약

409 를 받고 사용자가 "그래도 기록" 을 누르면, **`client_event_id` 를 그대로 두고** `confirm: true`
만 붙여 다시 보낸다. 새 키를 만들면 재시도가 두 줄이 된다. `confirm` 은 쿼리가 아니라 body 에 둔다.

### 409 응답

```json
{ "detail": {
    "message": "오늘 08:15에 이미 약을 챙겼습니다.",
    "conflicts": [ { "id": "…", "occurred_at": "…", "note": "심장사상충",
                     "actor": { "app_user_id": "…", "nickname": "아빠" } } ] } }
```

`actor` 는 §3 의 이름 표시 규칙을 따른다 (구성원이 아니면 `null`).

dict `detail` 은 **이 저장소에 없던 모양**이다 — 지금 라우터들은 `HTTPException(status, "문자열")`
만 쓴다. 이 API 에서 처음 들인다.

### 일부러 안 하는 것

**동시 기록은 안 막는다.** 둘이 같은 순간에 약을 기록하면 둘 다 창이 비어 있어 둘 다 들어간다.
락을 안 거는 이유는 이것이 **불변식이 아니라 경고**이기 때문이다 — `care_events` 는 하루에 약 두
건을 애초에 허용한다(다른 약일 수 있다). 진짜 시나리오는 교대 경계의 몇십 분 차이지 동시 탭이
아니다. §3 에서 `pets` 락을 넣은 것과 판단이 다른 이유가 이것이다 — 저긴 정합성이고 여긴 친절이다.

### 읽기 쪽

`GET /app/care-events/today` 가 "퇴근 후 앱 열기" 화면을 완성한다 — 각 이벤트에 `actor` 추가,
§2 의 `walk.py:127` 수정으로 **아빠의 산책이 `walk` 수에 잡힌다.** 창은 그대로 서울 자정 기준.

---

## §5 테스트

이 저장소는 **가짜 리포지토리가 기본**이다 (`test_care_events.py` 머리말: *"DB 는 쓰지 않습니다"*).
그런데 §1 의 트리거 둘은 가짜로 증명할 수 없다 — 그것이 트리거를 고른 이유 자체다.

### 층 1 — 가짜 기반 (규칙)

**`test_pet_members.py` (신규)**

| 무엇 | 기대 |
| --- | --- |
| 초대 생성 | 대표만 · 유효 3개 상한 409 · 만료건이 같이 지워짐 |
| 수락 | 404 · 410(만료) · 410(대표 바뀜) · 409(이미 대표) · **200(이미 구성원)** · 409(구성원 상한) · 409(미니룸 상한) |
| 수락 부수효과 | `primary_pet_id` 가 NULL 이던 신규 돌보미에게 그 아이가 채워짐 |
| 퇴장·내보내기 | 대표가 내보냄 · 본인이 나감 · 제3자 403 · **대표가 자기를 못 뺌** |
| 퇴장 부수효과 | 그 아이가 `primary_pet_id` 였으면 남은 **구성원** 강아지로 교체 |
| 승계 | 대표만 · 대상이 구성원이어야 · 옛 대표가 돌보미로 남음 · 새 대표 미니룸 상한 409 · 남은 초대 삭제 |
| **승계 회귀** | **옛 대표의 `primary_pet_id` 는 그대로 유지** |

**`test_care_events.py` (확장)** — 회귀 둘이 중요하다:

- **멱등이 중복 검사보다 먼저**: `confirm=true` 로 201 을 받은 뒤 **같은 `client_event_id`** 재전송
  → `200` 이어야 하고 `409` 면 실패.
- **하루 요약이 아빠 산책을 셈**: `walk.py:127` 수정이 되돌아가면 여기서 걸린다.
- 그 외 — 6시간 창 안 → 409 + `conflicts` 본문 / 12시간 간격 → 통과 / 밥·간식은 6시간 안에도 409
  없음 / `actor` 가 현재 구성원이면 닉네임, 아니면 `null`.

**`test_pets.py` (확장)** — 돌보미가 조회는 되고 수정·배웅·삭제·사진은 403. 상한이 구성원 기준.

**`test_app_auth.py` (확장)** — 대표 탈퇴 + 돌보미 있음 → 409 **이고 강아지가 안 지워짐**(이것이
진짜 검증 대상). 409 메시지에 두 출구가 다 있음. 돌보미로만 참여 중이면 통과. 승계 후 / 돌보미
내보낸 후 각각 통과.

### 층 2 — 진짜 PostgreSQL (보장)

`test_release_fk_postgres.py` 가 이 목적의 선례다 — loopback DSN 만 허용(공유 DB 보호), 스키마
없으면 skip, **모든 쓰기를 한 트랜잭션에서 검사하고 rollback**. 그 틀로
`test_pet_membership_postgres.py` 를 만든다.

- 트리거 ①: `app_users.status='withdrawn'` 으로 **UPDATE** 하면 `pet_members`·`pet_invites` 행이
  사라진다 ← 이것의 유일한 증명
- 트리거 ②: 대표를 INSERT 하면 예외
- `idx_pet_members_app_user` 가 존재한다 ← 마이그레이션에서 누락되는 것을 막는다
- `pet_invites.token_hash` UNIQUE
- **`db/migrations/` 파일을 두 번 연속 적용해도 에러가 없다** ← 버전 테이블이 없어 재실행
  안전성이 규칙인데 지금 그것을 검사하는 테스트가 하나도 없다

### CI

`.github/workflows/backend-tests.yml` 에 postgres 서비스가 없어 층 2 가 통째로 skip 된다.
**개인정보 파기 보장 둘이 거기 걸려 있으므로 이 PR 에서 워크플로에 postgres 서비스와
`db/init/*.sql` 적용 단계를 붙인다.** 덤으로 지금 조용히 skip 되고 있는
`test_release_fk_postgres.py` 도 같이 살아난다.

---

## 열린 것

- **미니룸이 돌보미의 강아지를 세우는가** — §2 의 상한 의미가 여기 달렸다. 앱 결정.
- **앱 짝 PR** — 초대 링크 딥링크 처리, 탈퇴 409 화면, 약 확인 다이얼로그, `actor` 표시.
