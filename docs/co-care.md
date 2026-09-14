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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 수락 영수증 (2026-09-10, 앱 계약 #388 · #261). 수락은 더 이상 이 행을 안 지운다 —
    -- 대신 이 둘을 채운다. 같은 사람이 같은 토큰으로 다시 오면 이 값으로 그때의 응답을
    -- 그대로 돌려준다(200). 다른 사람이면 여전히 404 다. 수명은 새 칸을 안 두고 위
    -- expires_at 그대로 쓴다 — 만료건 청소가 수락 여부를 안 가린다. SET NULL 은
    -- care_events.actor_app_user_id 와 같은 이유다(영수증은 사람만 비운다).
    accepted_at TIMESTAMPTZ,
    accepted_by UUID REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_pet_invites_pet ON pet_invites (pet_id);
```

토큰은 `core/token.py` 의 `generate_refresh_token()`(`secrets.token_urlsafe(32)`)과
`hash_refresh_token()` 짝을 그대로 쓴다 — **새 암호 코드를 한 줄도 안 쓴다.**

`accepted_at`·`accepted_by` 는 2026-09-10 에 더해진 영수증이다(§3 "수락 영수증"). **수락은
더 이상 행을 지우지 않는다** — 대신 이 둘로 "누가 언제 받았는지" 를 남긴다. 응답을 못
받은 재시도가 같은 토큰으로 다시 오면, 같은 사람에게는 그때 응답을 그대로 주고(200), 다른
사람에게는 여전히 404 를 줘 "이미 쓴 초대"와 "없는 토큰"의 구별이 안 새게 한다.

### `care_events` 변경

```sql
ALTER TABLE care_events RENAME COLUMN app_user_id TO actor_app_user_id;
ALTER TABLE care_events ALTER COLUMN actor_app_user_id DROP NOT NULL;
-- FK 도 CASCADE → SET NULL. '소유자' 였을 땐 같이 지우는 게 맞았지만, '챙긴 사람' 은
-- 떠나도 "그날 밥을 먹은 사실" 은 강아지의 것으로 남아야 한다.
--
-- ⚠️ 다만 **이 SET NULL 이 탈퇴를 처리해 주지는 않는다** — 위 "함정" 그대로, 행이 안 지워져
--    영영 안 돈다. 탈퇴 때 실제로 이 칸을 비우는 것은 아래 트리거 ① 이다. 여기 SET NULL 은
--    언젠가 행을 진짜로 지우는 날을 위한 안전망이다 (`pet_members` 의 CASCADE 와 같다).
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
        -- 케어 로그의 actor 도 여기서 비운다. 위 `SET NULL` 은 **안 돈다** — 행이 안 지워지니까.
        -- 안 비우면 탈퇴한 돌보미의 id 가 **남의 집** 케어 로그에 영원히 남는다. 행은 남긴다:
        -- "그날 밥을 먹은 사실" 은 강아지의 것이다. 남는 흔적은 닉네임이 아니라 가명 id 하나다
        -- (이름은 `actor_label` 이 비구성원에게 이미 안 낸다).
        UPDATE care_events SET actor_app_user_id = NULL WHERE actor_app_user_id = NEW.id;
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
def member_condition(app_user_id):    # 대표 ∪ 돌보미. 네 리포지토리가 가져다 쓰므로 공개 이름이다
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
| `repositories/screening.py` (`get_accessible`/`list_accessible`, Task 14) | 구성원 — **단, `pet_id` 가 있을 때만** | 강아지에 붙은 피부 이력. 소유(창작자)는 그대로 두고 조회 바닥만 연다 — `pet_id IS NULL` 인 개인 기록은 이 판정 바깥이라 창작자만 본다 |
| `repositories/walk_entry.py:40` | 구성원 | 산책 기록 편집 |
| `repositories/territory_claim.py:38` | 구성원 | 아빠가 걸어서 점령하려면 그 아이에 닿아야 한다. 점령 **결과**는 `territory_claims.app_user_id` 라 여전히 아빠 것 |

### 보행 **생성**은 구성원이 연다 (Task 12)

위 표에서 `repositories/gait_record.py` 가 "구성원" 인 것은 처음엔 **읽기**뿐이었다. 새 기록을
여는 `services/gait.py::start_analysis` 가 `get_owned` 를 쓰던 동안은 **돌보미가 보행 영상을
못 올렸다** — 결정 ②("돌보미는 기록하고 본다")와 어긋나는 자리라고 알고 남겨 뒀던 후속이,
최종 리뷰 뒤 이 브랜치에서 열렸다. 지금은 `pet_repo.get_accessible` 이다 — 구성원(대표 ∪
돌보미)이면 새 보행 기록을 열 수 있다.

**연 것은 "새로 시작하는" 쪽뿐이다.** 상태를 바꾸거나 지우는 쪽은 그대로 대표만이다 —
`confirm_upload`·`soft_delete`(`repositories/gait_record.py` 의 `_owned`)가 그것이다. 바닥을
하나로 합치면 돌보미가 남의 집 보행 영상을 지운다 — 그 경계는 안 건드렸다.

**보행이 열리는 이유는 `gait_records` 가 `pet_id → pets.app_user_id` 로 소유를 유도하기
때문이다.** 누가 올렸든 그 기록은 곧 "그 강아지의" 기록이라, 생성을 구성원으로 열어도
대표가 그대로 본다 — 결정 ②의 "기록하고 본다" 가 통째로 성립한다.

### 스크리닝도 이제 구성원이 연다 — 바닥을 둘로 가른다 (Task 14)

`services/screening.py::start_record` 는 Task 12 에서 `get_accessible`(구성원)로 한 번 열었다가
**대표만**(`get_owned`)으로 되돌렸다. 이유는 구조 차이였다: `screening_records` 는 `gait_records`
와 달리 소유가 강아지에서 유도되지 않는다. `ScreeningRecord.app_user_id` 에 **만든 사람의
id 가 직접 저장**되고, 그때의 `repositories/screening.py` 는 `pet_repo.member_condition` 을 한
번도 쓴 적이 없는 레포지토리였다 — 조회·확정·삭제가 전부 그 값으로만 걸렸다. 그래서 돌보미의
생성만 열면 **대표가 영영 못 보는 스크리닝 기록이 생겼다.**

**Task 14 가 그 구조 차이를 없앤 것이 아니라, `gait_record.py` 가 이미 쓰던 답을 그대로
가져왔다** — 소유는 그대로 두고 **쿼리 바닥을 둘로 가른다.** `pet_id` 가 nullable 이라
`gait_record.py` 처럼 `Pet` 을 조인 하나로 못 쓰므로, `repositories/screening.py` 는 "내가
구성원인 강아지 id 집합" 을 서브쿼리로 뽑아 `pet_id IN (...)` 으로 문다:

- **`get_owned` / `list_for_owner`** — 그대로 창작자만. `confirm_record`(판정을 실제로
  진행·확정하는 자리)가 계속 쓴다. **여기는 손대지 않았다** — 확정을 열면 대표가 돌보미의
  판정 파이프라인 상태를 바꿀 수 있다.
- **`get_accessible` / `list_accessible`** (신규) — 창작자이거나, **`pet_id` 가 있고** 내가 그
  아이의 구성원이면. `start_record`(아이 소유권 확인)·`list_records`·`get_record` 가 쓴다.
  `pet_id IS NULL` 인 개인 기록은 어떤 `IN` 서브쿼리에도 안 걸리므로 **자동으로** 창작자만
  본다 — 이것이 "제품 결정이 먼저다" 라고 미뤄 뒀던 질문("개인 기록을 구성원 판정에서 어떻게
  다룰지")의 답이다: 강아지가 없으면 구성원이라는 개념 자체가 안 걸리게 두는 것으로 정했다.
- **`get_deletable`** (신규) — `care_repo.get_deletable` 과 같은 모양: **창작자 또는 그 아이의
  대표.** 볼 수 있는 사람 전체(구성원)에 지우기를 열면 돌보미끼리 서로의 기록을 지운다.
  `delete_record` 가 쓴다.

**닫아 둘 때의 판단**("대표가 못 보는 기록이 쌓이는 것은 되돌릴 수 없다")은 여전히 맞는
말이었다 — 그래서 여는 조건이 "리포지토리를 다시 짜서 강아지에 붙은 기록은 구성원 전체가
보게 한다" 였고, 이번에 그 조건을 채웠다. 개인 기록의 프라이버시(창작자만)가 그 전제를
지키는 성질이라 **회귀 테스트로 고정했다**
(`test_아이를_안_고른_개인_기록은_구성원이어도_못_본다`, `backend/tests/test_screening_records.py`).

**파기 경로는 이 결정과 무관하고, 손대지 않았다.** `gait_service.cleanup_for_pets` 는
`pet_id` 로 걸려 있어 돌보미가 만든 것도 강아지 삭제·대표 탈퇴 때 같이 지워진다.
`screening_records` 는 다른 길을 이미 갖고 있다 — `screening_records.pet_id` 의 FK 가
`ON DELETE SET NULL`(`db/init/09_screening_records.sql`)이라, 강아지를 지워도 행은
안 지워지고 **`pet_id` 만 NULL 이 된다.** 그 순간 그 기록은 정확히 "아이를 안 고르고
찍은 개인 기록" 과 같은 모양이 된다 — 창작자(`ScreeningRecord.app_user_id`)와
`photo_storage_key` 는 그대로 있고, `screening_service.cleanup_for_owner` 가 그 창작자
탈퇴 때 행과 사진 파일을 **함께** 지운다. 고아로 남는 파일이 없다 — 이것이 스크리닝을
D-052 가 점령지 사진에 대해 지적한 문제("저장소에는 FK 가 없어서 아무도 안 치운다")의
반례로 만든 자리다: 사진은 언제나 DB 행 하나에 물려 있고, 그 행이 지워질 때만 같이
지워진다.

**결과로 알아 둘 것 하나** — 강아지에 붙어 있던 기록이 그 강아지가 지워지면 **개인
기록으로 되돌아가 창작자에게만 보인다.** 이전에는(대표만 만들 수 있어서) 이것이 "대표
자신의 기록이 대표 자신에게 남는" 일이었다. **이번 결정으로 돌보미도 만들 수 있게 되면서**,
돌보미가 만든 기록도 강아지가 지워지면 그 돌보미에게만 남고 대표는 더 이상 못 본다 —
강아지가 있을 때 보이던 기록이 사라지는 것이 아니라 **범위가 좁아지는** 것이다. 이미 있던
`ON DELETE SET NULL` 이 그대로 만들어 내는 동작이라 이번 변경이 새로 만든 것이 아니고,
고칠 결함도 아니다.

**거울상도 하나 있다 — 이번엔 강아지가 아니라 사람이 나간다.** 돌보미가 **탈퇴**하면
`screening_service.cleanup_for_owner` 가 그 사람이 창작한 기록의 **행과 사진을 함께**
지운다(`app_user_id` 기준 — §1 트리거와 달리 이건 원래부터 있던 탈퇴 파기 경로다). 그
기록이 강아지에 붙어 있었고 대표가 방금까지 구성원 목록·컨텍스트로 보고 있었더라도
예외가 아니다 — 대표가 볼 수 있던 기록이 그 돌보미의 탈퇴 한 번으로 사라진다. 강아지
삭제가 "범위가 좁아지는" 쪽이라면, 돌보미 탈퇴는 "통째로 없어지는" 쪽이다.

### 산책 읽기 — 두 줄

`repositories/walk.py:127` 의 `Walk.app_user_id == app_user_id` 를 **뺀다** (구성원 조건으로 갈지
않고 삭제). 부르는 쪽인 `care_event.day_summary` 가 이미 강아지 접근 권한을 확인한 뒤라, 여기서
다시 사람으로 거르면 **아빠의 산책만 빠진다.** `walk_pets` 조인이 이미 "그 아이가 나간 산책" 을
정확히 집는다. 함수 docstring 의 *"소유자 조건은 `walks.app_user_id` 로 겁니다"* 도 같이 고친다 —
그 문장이 이 결정으로 거짓이 된다.

**둘째 줄은 `repositories/walk_entry.py` 의 `profile_walks` 다** — 같은 자리가 하나 더 있었다.
게이트(`services/walk_entry.py::profile`)만 구성원으로 열고 질의를 그대로 두면 돌보미가
**200 을 받으면서 내용은 빈** 응답을 받는다 — 예전에는 404 였으므로 "권한이 없다" 가
"기록이 없다" 로 조용히 바뀌는 셈이다. 기록 프로필은 **강아지의 행동 요약**이지 사람의
성과가 아니므로(결정 ①) 같은 모양으로 소유자 조건을 뺀다. 산책의 **소유와 편집**은
그대로다 — `owned_walk` 가 계속 `Walk.app_user_id` 를 본다.

**산책 쓰기도 연다 — 계획을 뒤집었다.** 처음 판단은 "각자 자기 산책을 올리고 요약에서만
합쳐 보인다" 였는데, 그러면 **돌보미가 대표의 강아지를 산책에 태그할 수 없다.** 산책을
못 태그하면 그 강아지가 나온 산책 자체가 만들어지지 않고, 그러면 하루 요약이 셀 것이
애초에 없다 — §2 끝의 "하루 요약이 아빠 산책을 셈" 수정이 통째로 무의미해진다.

그래서 `repositories/pet.py` 의 `accessible_ids()` 가 **구성원 조건**으로 "누구를 태그할 수
있나" 를 연다 — 밥·약을 적을 수 있는 사람이면 같이 걸었다고 적을 수도 있어야 한다는
뜻이다. 산책 **소유**는 그대로 올린 사람 것이다(`walks.app_user_id` 는 안 건드린다) —
바뀐 것은 "누구를 태그할 수 있나" 뿐이고, "누구 산책인가" 는 여전히 갈리지 않는다.

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
| `GET /app/pets/{pet_id}/invites` | 대표 |
| `DELETE /app/pets/{pet_id}/invites/{id}` | 대표 |
| `GET /app/pets/{pet_id}/members` | 구성원 — 연결된 강아지는 **논리 그룹 전체** 보호자를 한 사람당 한 번씩, 대표(`is_owner`)는 **그룹 주보호자**. 조회만 넓고 관리 권한은 그대로 |
| `POST /app/pet-invites/accept` | 로그인 사용자 |
| `DELETE /app/pets/{pet_id}/members/{user_id}` | 대표 또는 본인 |
| `POST /app/pets/{pet_id}/owner` | 대표 |

수락 경로가 `/app/pets/{pet_id}/…` 아래가 **아닌 것이 의도다.** 수락 전에는 그 강아지에 아무
권한이 없어서, URL 에 `pet_id` 를 실으면 남의 강아지 id 를 넣어 보는 자리가 생긴다. 토큰만 받는다.

새 파일 `routers/pet_member.py` · `services/pet_member.py` · `repositories/pet_member.py`.

### 초대 생성

유효 초대는 강아지당 **3개**까지. 새 초대를 만들 때 그 강아지의 **만료된 행을 같이 지운다** —
아무도 링크를 안 누르면 `pet_invites` 가 영원히 쌓인다. 이 상한은 **아직 쓸 수 있는** 초대만
센다 — 수락된 행(영수증, 아래)은 안 센다. 안 세면 3명이 수락한 강아지가 그 영수증이
`expires_at` 까지 살아 있는 최대 24시간 동안 새 초대를 못 보내게 된다.

### `GET /app/pets/{pet_id}/invites` — 초대 목록

**대표만.** 평문 토큰은 발급 응답(`InviteCreated`)에 딱 한 번만 실린다 — 그래서 나중에
그 초대를 찾아 취소하려면 이 목록이 유일한 길이다. 항목마다 `id` · `expires_at` ·
`created_at` · `accepted_at` 만 준다 — **토큰도 해시도 절대 안 준다.** 서버는 해시만
들고 있어서, DB 읽기 권한이 있는 사람이라도 이 응답으로 살아 있는 초대를 대신 쓸 수 있게
하면 안 된다. **수락된 것도 포함한다** — 대표가 "이건 이미 썼다" 를 구분해서 볼 수 있어야
한다(`accepted_at != null`).

### `DELETE /app/pets/{pet_id}/invites/{invite_id}` — 초대 취소

**대표만.** 돌보미·제3자는 강아지 자체가 안 보이므로 **404** 다(403 이 아니다 — 403 은
"강아지는 있는데 내 것이 아니다" 를 확인해 준다). 남의 강아지의 초대 id 를 넣어도 404 —
`pet_id` 와 `invite_id` 가 실제로 짝인지까지 DB 에서 같이 걸기 때문에, 존재하지 않는
조합·남의 초대·이미 없는 id 셋이 같은 응답이라 정보가 안 샌다. 이미 수락된(영수증) 초대도
지울 수 있다 — 취소는 "이 행을 없앤다" 는 뜻일 뿐, 토큰이 살아 있는지는 안 가린다.

### 수락 — 검증 순서

**`pets` 행에 `SELECT … FOR UPDATE` 를 걸고 시작한다.** 수를 세고 INSERT 하는 사이에 다른 수락이
끼면 상한을 넘긴다. 락 대상이 `pet_members` 가 아니라 **`pets` 여야** 아래 탈퇴 가드와 같은
자원을 두고 줄을 선다.

**수락은 더 이상 `pet_invites` 행을 지우지 않는다** (2026-09-10, 앱 계약 SAJOYO/DAENGS_dev#388 ·
SAJOYO/DAENGS_APP#261). 대신 `accepted_at`·`accepted_by` 를 채워 영수증으로 남긴다 — 아래
"수락 영수증" 참고.

| # | 조건 | 응답 |
| --- | --- | --- |
| 1 | `token_hash` 없음 | 404 |
| 2 | `expires_at` 지남 | 410 + 행 삭제 |
| 3 | 이미 이 토큰으로 수락됐고, **같은 사람**이 다시 보냄 | **200** (영수증 — 그때와 같은 `{pet_id, name}`) |
| 4 | 이미 이 토큰으로 수락됐는데 **다른 사람**이 보냄 | 404 (없는 토큰과 같은 응답) |
| 5 | `invited_by ≠ pets.app_user_id` (그새 대표가 바뀜) | 410 |
| 6 | 수락자가 이미 `pets.app_user_id` | 409 |
| 7 | 이미 `pet_members` 에 있음 | **200** (멱등 — 동시 수락 경쟁. 영수증도 여기서 채운다) |
| 8 | 구성원이 `MAX_MEMBERS_PER_PET`(5) 이상 | 409 |
| 9 | 수락자의 미니룸 상한 초과 | 409 |

**행 3/4 가 이 개정의 핵심이다.** 예전에는 수락이 초대 행을 지웠다 — 그래서 응답을 못 받은
재시도(네트워크 끊김 등)가 같은 토큰으로 다시 오면 `token_hash` 를 못 찾아 **404** 였다.
그런데 그 사람이 이미 다른 강아지를 여러 마리 돌보고 있거나, 다른 기기가 그새 목록을
바꿨다면 "이번 요청이 실패했다" 와 "이미 성공했는데 응답만 못 받았다" 를 앱이 구분할
방법이 없었다 — `GET /app/pets` 를 다시 불러도 **이 토큰으로** 성공했는지는 안 알려준다.
지금은 행을 안 지우므로 같은 사람의 재시도는 그때 응답을 그대로 다시 준다. **다른 사람**이
같은(이미 쓴) 토큰을 보내면 여전히 404 다 — 그 토큰이 한 번이라도 유효했다는 사실 자체가
안 새야 한다(§1 의 일반 원칙과 같다).

⚠️ **행 3/4 은 그 사이 다른 일이 있었는지 다시 확인하지 않는다.** "응답을 못 받은 재시도" 는
거의 곧바로 다시 오는 것을 전제한다 — 그래서 그 사이 그 사람이 실제로 나갔거나 내보내졌어도
재시도는 그대로 200 을 돌려준다(다만 실제 구성원 상태는 안 되돌린다). 며칠 뒤에 같은
토큰으로 다시 들어오고 싶으면 새 초대를 받아야 한다 — 이 분기는 "몇 초 안의 네트워크
재시도" 를 위한 것이지 "언제든 다시 쓸 수 있는 만능 열쇠" 가 아니다.

**행 7 은 "두 번 누른 카톡 링크" 를 안 막는다 — 그건 1번(다른 사람이 이미 지운 뒤라면
2/4번)에서 이미 걸린다.** 행 7 이 지키는 것은 **동시 요청**이다 — 두 기기가 같은 순간에
같은(아직 `accepted_by` 가 안 채워진) 초대 행을 읽어서, 하나가 먼저 구성원으로 넣고
커밋하는 사이에 다른 하나도 그 초대 객체를 들고 있는 경우다(그 객체는 승자가 채운
`accepted_by` 를 다시 읽지 않으므로 행 3/4 분기로는 못 걸린다). 진 쪽은 `is_member` 가
참이라 예외 없이 200 을 받고, **여기서도 영수증을 채운다** — 그래야 이 사람의 **다음**
재시도부터는 행 3(빠른 경로)으로 들어온다. 이것을 지우면(예: 409로 바꾸면) 진 쪽
사용자에게 "실패" 화면이 뜨는데 실제로는 이미 구성원이 된 뒤다.

### 수락 영수증

`accepted_at`(수락 시각) · `accepted_by`(수락자) 두 칸이 그것이다. **수명은 새 칸을 안 두고
기존 `expires_at` 을 그대로 쓴다** — `delete_expired_invites` 가 만료건을 청소할 때 수락
여부를 안 가리므로, 이 둘을 더해도 그 함수는 안 바뀐다(만료되면 영수증째로 지워진다). 즉
재시도로 복구할 수 있는 창은 원래 초대 유효기간과 같은 최대 24시간이다.

수락 부수효과 — 수락자의 `primary_pet_id` 가 NULL 이면 그 아이로 채운다.
`services/pet.py:99` 에 이미 같은 코드가 있다 (강아지 등록 때). 재시도(행 3) 로는 이 부수효과가
**다시 실행되지 않는다** — 이미 첫 성공에서 끝난 일이다.

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
6. 이 아이의 남은 `pet_invites` 삭제 (`invited_by` 가 옛 대표) — **수락 영수증도 같이
   지워진다.** 손대지 않은 동작이다: 옛 대표의 흔적을 한 번에 지우는 것이 이 단계의
   목적이므로, 승계 직후 옛 링크로 재시도 창을 남겨 두는 것보다 우선한다

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

### 직접 삭제 — 탈퇴 가드와 다른 모양의 안전장치 (Task 13)

**비대칭이 있었다 — 탈퇴는 막고 직접 삭제는 안 막았다.** 이제 결정됐다: `DELETE /app/pets/{pet_id}`
도 돌보미가 남았으면 신호를 준다. 하지만 **탈퇴 가드를 그대로 복사하지 않는다** — 둘은 성격이
다르다.

| | 탈퇴 (`OwnerHasCarersError`) | 직접 삭제 (`PetHasCarersError`) |
| --- | --- | --- |
| 강아지 파괴는 | **부수효과.** 사람은 서비스를 떠나는 것이지 강아지를 생각하고 있지 않다 | **목적.** 대표가 그 강아지를 겨냥해 지운다 |
| 모르는 것 | 없음 — 그래서 **하드 블록** + 두 출구(승계 / 돌보미 내보내기)를 안내한다 | "누가 돌보고 있는가" 뿐 — 그래서 **정보만 주고 확인**을 받는다 |
| `confirm` | 없음. 절대 안 뚫린다 | `?confirm=true` 로 다시 부르면 그대로 진행한다 |

탈퇴 가드는 **손대지 않았다** — `services/pet.py::delete_all_for_owner` 는 여전히 무조건
409 고 우회할 방법이 없다. 새로 생긴 것은 `delete_pet` 안의 별도 게이트다:

1. 소유 확인(`get_owned`, 대표만 — 이 확인 자체는 안 바뀌었다. 돌보미·제3자는 여전히 404)
2. **어떤 cleanup 도 하기 전에** `pet_members` 를 조회해 돌보미가 있고 `confirm=False`
   면 `PetHasCarersError`(강아지 이름 + `(app_user_id, nickname)` 목록)를 던진다
3. `confirm=True` 거나 돌보미가 없으면 그대로 진행 — gait·사진·산책 정리, 돌보미
   `primary_pet_id` 수선까지 지금까지와 동일하다

라우터(`routers/pet.py::delete_pet`)는 이것을 409 로 바꾼다:

```json
{ "detail": {
    "message": "다른 보호자가 맥스을(를) 돌보고 있어요. 정말 지울까요?",
    "pet_name": "맥스",
    "carers": [ { "app_user_id": "…", "nickname": "아빠" } ] } }
```

`confirm` 은 **쿼리 파라미터**(`?confirm=true`)다. §4 의 약 중복 확인은 `confirm` 을 body 에
두는데, 그건 같은 `client_event_id` 로 재전송하는 멱등 재시도 계약이 있어서다. `DELETE` 에는
그런 계약이 없고 `DELETE` 요청에 body 를 싣는 것도 어색해, 모양이 다른 것은 **의도**다.

돌보미의 `primary_pet_id` 뒤처리는 그대로다 — `pets` 행이 진짜로 지워져 그쪽 FK 의
`SET NULL` 이 이번엔 실제로 돌기 때문에, 확인을 지난 뒤에는 여전히 돌보미들의 `primary_pet_id`
를 퇴장과 같은 규칙으로 갈아 준다.

### 이름 표시 규칙

> **이름은 그 사람이 *지금* 이 강아지의 구성원일 때만 보여 준다. 아니면 "이전 보호자".**

`withdrawn` 체크가 따로 필요 없다 — 트리거 ①이 탈퇴 시 `pet_members` 를 지우므로 탈퇴자는 자동으로
비구성원이 된다. **재가입해도** 다시 초대받기 전엔 "이전 보호자" 다. 이 규칙이 없으면 탈퇴자가
나중에 재가입할 때(같은 행이 되살아난다) 옛 기록이 갑자기 그 사람의 **새 닉네임**으로 뜬다 —
우리 집 화면에 지금은 남인 사람의 현재 닉네임이 뜨는 것이다.

`GET …/members` 와 케어 로그 응답의 `actor` 필드는 **같은 결과를 내되 코드는 다르다.**
목록 쪽은 이미 "구성원 명단" 을 손에 들고 있어 `app_user_repo.nicknames_by_ids` 를 곧장
부르고, 케어 로그 쪽은 행마다 남은 `actor_app_user_id` 가 **지금도 구성원인지 모르는** 값이라
`services/pet_member.py::actor_label` 을 거친다 — 위 규칙을 거는 것은 그 하나뿐이다.

### 케어 로그 삭제 권한

**지운 사람 본인, 또는 그 강아지의 대표만 지운다 — 다른 돌보미는 못 지운다.**

기록의 주인은 사람이 아니라 강아지다(결정 ①). 그래서 대표는 돌보미가 잘못 적은 기록을
지울 수 있어야 하고, 반대로 누구든 자기가 적은 기록은 스스로 고칠 수 있어야 한다. 구성원
전체에 열면 돌보미끼리 서로의 기록을 지우게 되고, `actor_app_user_id` 하나만 보면(적은
사람만) 대표가 돌보미의 오기록을 영영 못 지운다.

조건은 쿼리 안에서 건다(`repositories/care_event.py` 의 `get_deletable` — `pets` 를 조인해
`actor_app_user_id = 나` 또는 `pets.app_user_id = 나` 를 OR 로 묶는다). 행을 먼저 꺼내 놓고
밖에서 비교하면 부르는 쪽이 그 비교를 잊는 자리가 생긴다 — `pet_repo.get_owned` 와 같은
이유다.

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
- 트리거 ①의 둘째 몫: 같은 UPDATE 로 `care_events.actor_app_user_id` 가 **NULL 이 되고 행은 남는다**
  ← 남의 집 케어 로그에 탈퇴자의 id 가 남는 것을 막는 유일한 증명
- 트리거 ②: 대표를 INSERT 하면 예외
- **진짜 `transfer_owner` 를 진짜 DB 에서 부른다** ← 생 SQL 로 "트리거가 순서에 민감하다" 만
  재는 것으로는 서비스가 그 순서를 지키는지를 못 본다. 지금 그 정확성은 SQLAlchemy autoflush 가
  `pets` UPDATE 를 `pet_members` INSERT 보다 먼저 내보내는 것에 기대고 있는데, 그것을 재는 자리다
- **돌보미가 `profile_walks` 로 대표의 산책을 본다** ← 이 질의는 가짜 대역이 통째로 갈아치워서
  진짜 DB 에서만 증명된다
- `idx_pet_members_app_user` 가 존재한다 ← 마이그레이션에서 누락되는 것을 막는다
- `pet_invites.token_hash` UNIQUE
- **`db/migrations/` 파일을 두 번 연속 적용해도 에러가 없다** ← 버전 테이블이 없어 재실행
  안전성이 규칙인데 지금 그것을 검사하는 테스트가 하나도 없다

### CI

`.github/workflows/backend-tests.yml` 에 postgres 서비스가 없어 층 2 가 통째로 skip 된다.
**개인정보 파기 보장 둘이 거기 걸려 있으므로 이 PR 에서 워크플로에 postgres 서비스와
`db/init/*.sql` 적용 단계를 붙인다.** 덤으로 지금 조용히 skip 되고 있는
`test_release_fk_postgres.py` 도 같이 살아난다.

⚠ **그런데 그 워크플로는 `ubuntu-latest` 라 이 팀에서는 실행되지 않는다** — GitHub 호스티드
러너를 안 쓰기로 한 팀 결정 때문이다. 그래서 위에서 붙인 postgres 서비스는 지금 아무도 안
띄운다. 층 2 를 실제로 돌리는 방법은 로컬 `cd backend && uv run pytest` 뿐이다 — 위 "머지 전에
할 일" 참고.

---

## §6 다중 강아지 초대 · 논리 연결 (2026-09-12)

초대 하나에 강아지 여러 마리를 담고, 받는 사람이 **이미 등록한 같은 강아지를 직접 골라
논리적으로 연결**할 수 있게 한 뒤에 생긴 규칙들이다. 기준 문서는 `공동돌봄_다중초대_MVP_
결정_2026-09-12` 이고, 여기 적은 것은 **실제로 구현된 계약**이다.

**논리 연결은 물리 병합이 아니다.** 기존 `pets` 행도 13개 표의 `pet_id` 기록도 옮기거나
지우지 않는다. 여러 `pet_id` 가 같은 실제 강아지라는 **관계만** 더하고, 화면과 공동 조회에서
한 마리처럼 보이게 한다. 그래서 되돌리기가 값싸다 — 연결 관계만 지우면 각자의 강아지와
기록이 그대로 남는다.

### 스키마 둘

```sql
CREATE TABLE pet_identities (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,  -- 그룹의 앵커
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE pets ADD COLUMN identity_id UUID
    REFERENCES pet_identities(id) ON DELETE SET NULL;               -- NULL = 연결 안 됨
CREATE UNIQUE INDEX pets_identity_one_per_user
    ON pets (identity_id, app_user_id) WHERE identity_id IS NOT NULL;

CREATE TABLE pet_invite_pets (
    invite_id     UUID NOT NULL REFERENCES pet_invites(id) ON DELETE CASCADE,
    pet_id        UUID NOT NULL REFERENCES pets(id)        ON DELETE CASCADE,
    linked_pet_id UUID          REFERENCES pets(id)        ON DELETE SET NULL,  -- 영수증
    PRIMARY KEY (invite_id, pet_id)
);
ALTER TABLE pet_invites ADD COLUMN pet_count SMALLINT NOT NULL DEFAULT 1;
```

**그룹 주보호자를 사람이 아니라 `owner_pet_id`(pet 행)로 가리킨다.** `pet_members` 에 `role`
칸을 안 둔 것과 같은 이유다 — 주보호자의 원본은 `pets.app_user_id` 하나뿐이라, 여기에 사람
id 를 또 두면 승계 때 두 곳이 어긋난다. 그룹 주보호자는 언제나
`pets[owner_pet_id].app_user_id` 로 **유도**한다.

**`pets.identity_id` 의 FK 는 반드시 `SET NULL` 이다.** CASCADE 로 바꾸면 그룹 행 하나가
사라질 때 사람들의 강아지와 기록이 통째로 딸려 간다 — 논리 연결의 전제가 그 한 칸에 걸려
있다. 부분 UNIQUE 는 "한 사람은 한 그룹에 행 하나" 를 DB 가 보장하는 자리이고, 이것이
① 기존 강아지를 초대 강아지 둘에 연결하는 것을 막고 ② 목록 접기가 언제나 카드 한 장을
내놓는 근거다.

**`pet_invites.pet_id` 는 앵커로 남는다.** `idx_pet_invites_pet`·`delete_invites_for_pet`
(승계)·`delete_expired_invites`·`count_valid_invites` 가 전부 그 칸을 보고, NOT NULL 을
떼면 `verify_2026-09-09_pet_members.sql` 과 그 변조 목록이 깨진다.

**`pet_count` 가 묶음 불변성을 지탱한다.** `pet_invite_pets.pet_id` 가 CASCADE 라 강아지가
지워지면 자식 줄이 **조용히** 사라진다 — 그러면 남은 강아지만 부분 수락되는데, 그것이 제품이
금지한 동작이다. 수락할 때 자식 수와 이 값을 견줘 다르면 묶음 전체를 410 으로 막는다.
`DEFAULT 1` 은 배포 창 때문이다 (§6 "배포 순서").

### 읽는 자리가 둘로 갈린다

| 무엇 | 어느 행에서 | 누가 고칠 수 있나 |
| --- | --- | --- |
| 이름 · 프로필 사진 · **이후 API 에 쓸 `pet_id`** | 각 보호자의 **표시용 행** | 그 행의 대표 |
| 견종 · 성별 · 생일 · 몸무게 · 급식 | 그룹의 `owner_pet_id` 행 | **그룹 주보호자만** |
| 지병 · 알레르기 · 상시 복용약 | 그룹의 `owner_pet_id` 행 | **그룹 주보호자만** |
| 배웅 상태 | 그룹의 `owner_pet_id` 행 | **그룹 주보호자만** |
| 케어 · 산책 기록 | 그룹 **합산** | 모든 공동 보호자가 작성 |

투영은 **읽을 때만** 한다. 연결 전 각 행에 있던 값은 덮어쓰지도 지우지도 않으므로, 연결이
풀리면 각자의 원본이 그대로 돌아온다. 구현은 `services/pet_identity.py::collapse`·`common_of`
이고, AI 비서의 건강 컨텍스트(`services/dog_context.py`)도 같은 규칙을 지난다 — 안 그러면
**같은 강아지에 대해 사람마다 다른 지병으로 답한다.**

목록 접기 규칙은 하나다:

> 같은 그룹 안에 **내가 대표인 행**이 있으면 그 행이 내 카드다. 없으면 그룹의
> `owner_pet_id` 행이다.

그래서 A 의 `롱이씨`와 B 의 `롱롱씨`를 연결하면 A 는 `롱이씨` 한 장, B 는 `롱롱씨` 한 장을
본다. 카드의 `id` 는 **표시용 행**이다 — 앱이 그 값을 케어·산책 요청에 그대로 싣고, 서버가
내부에서 그룹으로 펼쳐 읽는다. 공통 행의 id 를 내보내면 B 가 A 의 행에 기록을 쓰게 된다.

### 상한이 논리 강아지 기준이 된다

```sql
COUNT(DISTINCT COALESCE(pets.identity_id, pets.id))
```

연결 안 된 행은 자기 id 가 그룹 키라, **연결이 없는 계정의 숫자는 이 변경 전과 같다**
(backfill 이 없는 이유). 기존 강아지와 연결하면 수가 안 늘고, 연결 없이 참여하면 하나 는다.
숨긴 아이·배웅한 아이는 그대로 포함한다.

**세기 전에 사용자 행을 잠근다** (`services/pet_identity.py::lock_user`). `pets` 행만 잠그면
모자란다 — 서로 다른 강아지를 겨냥한 동시 요청은 서로 다른 행을 잠그므로 둘 다 "아직
4마리" 를 보고 통과한다. `CurrentAppUser` 의존성이 이미 같은 행을 잠그지만(`core/deps.py`),
상한이라는 도메인 불변식을 **인증 계층에 기대면** 라우터 의존성을 바꾸는 순간 조용히 깨진다.

한 논리 강아지의 보호자도 그룹 전체에서 **중복 제거한** 사용자 5명이다
(`pet_identity.guardian_ids`). pet 행별로 세면 연결할 때마다 그룹 인원이 상한을 넘어 늘어난다.

### 권한 — `is_owner` 와 `is_group_owner`

| 동작 | 행 대표 | 그룹 주보호자 |
| --- | --- | --- |
| 자기 이름 수정 (`PATCH /app/pets/{id}/display`) | 가능 | 가능 |
| 자기 사진 (`/app/pets/{id}/photo*`) | 가능 | 가능 |
| 공통 프로필·건강정보·배웅 (`PUT /app/pets/{id}`) | **불가(409)** | 가능 |
| 초대 생성·목록·취소 | **불가(409)** | 가능 |
| 보호자 내보내기 | **불가(409)** | 가능 |
| 주보호자 승계 | **불가(409)** | 가능 |
| 강아지 삭제 | **불가(409)** | 가능 |

연결 안 된 강아지는 행 대표가 곧 그룹 주보호자라 **두 값이 언제나 같다** — 구 앱이
`is_group_owner` 를 몰라도 지금까지와 똑같이 도는 이유다.

**UI 에서 버튼만 숨기는 것으로는 부족하다.** 구 앱도 호출할 수 있으므로 서버가 모든 그룹 관리
동작에서 다시 본다. 거부는 **404 가 아니라 409** 다(`detail.code = "not_group_owner"`) —
호출자가 그 강아지를 이미 자기 목록에서 보고 있어 감출 것이 없고, 404 면 앱이 "강아지를 찾을
수 없습니다" 라고 거짓말을 그린다. 제3자는 그대로 404 다.

**전체 PUT 을 막고 이름 전용 계약을 새로 연 이유.** `PUT /app/pets/{id}` 는 전체 PUT 이라,
연결된 공동 보호자가 이름만 고치려 해도 화면에 그려진 **주보호자의 공통 정보**가 자기 원본
행에 덮어써진다. 공통 필드를 조용히 무시하는 것도 원본을 덮어쓰는 것도 답이 아니므로 넓은
계약은 409 로 막고, 좁은 계약(`PATCH /app/pets/{id}/display`, 이름 한 칸)을 새로 열었다.
사진은 이미 티켓·confirm 이 따로이고 그쪽은 계속 행 대표다.

### 초대 묶음

- 그룹 주보호자가 자기 아이 1~5마리를 고른다. 묶음은 토큰 하나다.
- **활성 초대는 강아지당이 아니라 주보호자 한 명당 3묶음**이다
  (`count_valid_invites_for_inviter`). 강아지당으로 세면 "묶음 하나 = 활성 하나" 가 깨진다 —
  같은 묶음이 다섯 자리를 먹거나, 강아지마다 3묶음씩 15묶음이 살아 있게 된다.
  **구 경로와 새 경로가 같은 자리를 센다** — 안 그러면 구 앱으로 상한을 우회할 수 있다.
- 배웅한 아이는 초대에도 연결 후보에도 못 넣는다. 배웅을 그룹 공통 상태로 옮기는 것은
  후속이라, 그때까지는 애초에 안 들어가는 쪽이 안전하다.
- 취소·만료·사용 완료는 **묶음 전체**에 적용된다.

| 메서드 · 경로 | 권한 | 상태 |
| --- | --- | --- |
| `POST /app/pet-invites` | 그룹 주보호자 | 신설 — 여러 마리 |
| `GET /app/pet-invites` | 로그인 | 신설 — 내가 보낸 묶음 전부 |
| `DELETE /app/pet-invites/{invite_id}` | 초대한 사람 | 신설 — 묶음 전체 취소 |
| `POST /app/pet-invites/preview` | 로그인 | 신설 — 미리보기 + 연결 후보 |
| `POST /app/pets/{pet_id}/invites` | 그룹 주보호자 | **유지** — 한 마리 묶음을 만든다 |
| `GET /app/pets/{pet_id}/invites` | 그룹 주보호자 | **유지** — 그 아이가 낀 묶음 |
| `DELETE /app/pets/{pet_id}/invites/{id}` | 그룹 주보호자 | **유지** — 묶음 전체 취소 |

경로를 새로 낸 이유는 `{pet_id}` 가 URL 에 있는데 묶음이 여러 마리면 뜻이 모순되고,
**구 서버가 새 body 를 pydantic 기본값(`extra="ignore"`)으로 조용히 무시**하기 때문이다.

구 취소 경로는 "URL 의 두 id 가 짝인가" 를 계속 본다 — 묶음이 되면서 그 "짝" 의 뜻이
**앵커이거나 담긴 아이이거나**로 넓어졌다. 이 검사를 빼면 같은 사람의 다른 아이 경로로 아무
초대나 지울 수 있다.

### 미리보기 — `POST /app/pet-invites/preview`

**GET 이 아니라 POST 다.** 토큰을 URL 에 실으면 nginx access log·Referer·브라우저 히스토리에
평문이 남는다 (수락이 body 로 받는 것과 같은 자리).

응답에 **건강정보가 없다.** 수락 전에는 구성원이 아니라, 토큰 하나로 남의 집 지병·복약을 읽는
자리를 만들면 안 된다. 이름·견종·사진 있음 여부까지가 "이 아이가 맞나" 를 판단하는 데 필요한
전부다.

연결 후보를 같은 응답에 싣는다 — 앱 화면이 "강아지 목록 + 각 줄의 연결 드롭다운" 한 장이라,
나누면 왕복 두 번에 두 응답의 정합성을 앱이 맞춰야 한다.

**연결 후보 네 조건** (`pet_repo.list_link_candidates`):

1. 내가 그 행의 대표다
2. **다른 공동 보호자가 없다**
3. 다른 논리 강아지에 연결되지 않았다
4. 배웅한 아이가 아니다

2번이 핵심이다 — 남의 기록이 이미 얹힌 아이를 연결하면, 그 공동 보호자의 케어·산책이 **동의
없이** 새 그룹에 공개된다. 저장소에 "최초 등록자" 칸이 없어 `대표 + 공동 보호자 없음` 을
"혼자 관리하던 아이" 의 근사 조건으로 쓴다. 승계로 대표가 된 아이는 옛 대표가 돌보미로
남으므로 2번에 걸려 자동으로 빠진다.

### 수락 — `POST /app/pet-invites/accept`

```json
{ "token": "<redacted>",
  "links": [ { "pet_id": "101…", "link_to_pet_id": "202…" },
             { "pet_id": "102…", "link_to_pet_id": null } ] }
```

`links` 는 **선택 필드**다. 없으면 전부 "연결 없이 참여" 이고, 그것이 구 앱 요청
(`{"token": ...}`)과 정확히 같은 동작이다 — **단 한 마리 묶음에서만이다.**

> **토큰만 보내는 옛 계약은 한 마리 묶음에서만 통과한다.** 두 마리 이상이면 **모든 항목의
> 선택값**이 와야 하고, 빠지면 409 `link_selection_required` 다 (`missing_pet_ids` 를 같이
> 낸다).
>
> 왜냐 — 토큰만 보내는 것은 "고를 것이 없다" 는 뜻인데, 묶음에서는 사용자가 고를 것이
> 있는데 **구 앱이 그 화면을 못 그립니다.** 그대로 통과시키면 사용자가 모르는 사이에 전부
> 새 강아지로 들어와 목록이 늘고, 되돌리려면 하나씩 나가야 한다.
> `link_to_pet_id: null` 은 **선택**이지 빠진 것이 아니다 — 그것만 다 채우면 통과한다.

요청이 이 묶음과 안 맞을 때의 코드:

| 무엇 | 상태 | `detail.code` |
| --- | --- | --- |
| 묶음인데 선택이 빠짐 | 409 | `link_selection_required` (+`missing_pet_ids`) |
| 초대에 없는 `pet_id` | 422 | `unknown_invited_pet` |
| 같은 연결 대상을 두 번 | 422 | `duplicate_link_target` |
| 같은 초대 강아지에 선택이 두 번 | 422 | pydantic 검증 (요청을 dict 로 접기도 전에 걸린다) |
| 연결 후보 부적격 | 409 | `link_not_allowed` (+`reason`) |
| 연결 대상이 내 것이 아님 | **404** | — (존재를 확인해 주지 않는다) |

**구성 변경(410)이 선택 검사보다 먼저다.** 구성이 깨진 묶음에 대고 "선택을 더 보내라" 고
하면 앱이 영영 못 고친다.

```json
{ "pet_id": "101…", "name": "롱이씨",
  "pets": [
    { "invited_pet_id": "101…", "display_pet_id": "202…", "name": "롱롱씨", "result": "linked" },
    { "invited_pet_id": "102…", "display_pet_id": "102…", "name": "코코",   "result": "joined" }
  ] }
```

최상위 `pet_id`·`name` 은 **구 앱 호환 앵커**다. 새 앱은 항목별 `display_pet_id` 를 쓰고 수락
뒤 목록을 새로고침한다. **두 id 를 갈라 두는 것이 이 응답의 핵심이다** — 연결했으면 이후
요청에 쓸 id 는 초대에 담겼던 아이가 아니라 받는 사람 자신의 행이다. 하나로 뭉치면 B 가 A 의
행에 기록을 쓰게 된다.

`result` 는 `linked` · `joined` · `already_member` · `already_owner` 넷이다. 이미 구성원이거나
이미 대표인 항목은 **충족된 것**으로 보고 나머지를 진행한다 — 단 **묶음 전부가
`already_owner`** 면 할 일이 하나도 없으므로 409 다. 한 마리 초대의 옛 계약("이미 이 아이의
대표입니다")이 그 자리에 있던 이유이고, 그것을 지키면서 섞인 묶음은 진행시키는 방법이다.

검증 순서 (`services/pet_member.py::accept_invite`):

| # | 보는 것 | 어긋나면 |
| --- | --- | --- |
| 1 | 사용자 행 잠금 | — (직렬화) |
| 2 | 초대 강아지 ∪ 연결 대상을 **id 오름차순**으로 잠금 | — (데드락 방지) |
| 3 | `expires_at` | 410 + 행 삭제 |
| 4 | 영수증(`accepted_by`) | 같은 사람 200(매핑 복원) · 다른 사람 404 |
| 5 | 묶음 구성 (`pet_count` ↔ 자식 수, 주보호자 변경, 배웅) | 410 |
| 6 | 요청 형식 (초대에 없는 id · 연결 대상 중복) | 422 |
| 7 | 연결 대상 소유권 | **404** (존재를 확인해 주지 않는다) |
| 8 | 연결 후보 네 조건 | 409 + `reason` |
| 9 | 그룹 보호자 합집합 ≤ 5 | 409 |
| 10 | 수락 **후** 논리 강아지 수 ≤ 5 | 409 |

**하나라도 실패하면 아무 변경도 남지 않는다.** `session.commit()` 이 함수당 한 번이라, 커밋
전에 예외가 나면 요청 세션이 닫히면서 그때까지의 쓰기가 전부 버려진다 — 부분 수락이 구조적
으로 불가능하다.

**멱등 영수증이 강아지별 매핑까지 복원한다.** `pet_invites.accepted_by` 만으로는 "누가 언제
받았는지" 까지만 알 수 있어 묶음의 항목별 결과를 되살릴 수 없다 — `pet_invite_pets.linked_pet_id`
가 그 자리다. 재시도는 `links` 를 안 보내도 그때의 결과를 그대로 돌려받는다.

⚠️ **앵커 pet 행 자체가 지워지면 초대 행이 통째로 사라진다** (`pet_invites.pet_id` 의 CASCADE).
그 토큰은 410 이 아니라 **404** 다 — 묶음 구성 변경 중 이 한 가지만 응답이 다르다.

### 공동 조회 — 케어와 산책만

읽기는 같은 identity 의 pet id 집합으로 펼치고, **쓰기는 부른 사람의 `display_pet_id` 에**
남긴다. 그래야 연결을 풀었을 때 각자가 적은 것이 제자리에 남는다.

- 케어: `list_between` · `count_by_kind` · `list_kind_between` 이 `pet_id IN (...)`
- **약 중복 확인 창도 그룹 전체**다. 안 넓히면 A 가 아침에 준 약을 B 가 못 봐서 교대 경계의
  중복 투약이 통째로 안 걸린다 — 그것이 §4 의 존재 이유 전부다.
- 산책: `count_for_pet_between` · `activity_for_pet_between` · `profile_walks` 가 묶음을 받는다.
  `profile_walks` 에는 **`distinct()` 가 꼭 있어야 한다** — 그룹의 두 아이가 같은 산책에
  태그되면 조인이 두 줄을 낸다(한 마리로는 만들 수 없던 상황이라 지금까지 없어도 됐다).
  `build_profile` 도 그룹 id 로 걸러야 한다. 산책만 넓히고 거기를 두면 산책은 늘었는데 행동
  수는 그대로인 프로필이 나온다.
- **수행자 표시**: `GET /app/care-events/today` 의 `walk_rows` 에 산책마다
  `{walk_id, started_at, actor}` 가 실린다. `walks.app_user_id` 가 `NOT NULL` 이라 수행자 정보가
  없는 산책은 없다 — 저장이 아니라 **응답**에만 없던 것이다.
- 이름표 판정은 `group_actor_label` 이 **그룹 전체**로 본다. `actor_label` 은 `pet_id` 하나의
  구성원인지를 보는데, 연결된 그룹에서는 A 가 B 의 행의 구성원이 아니라 B 의 화면에서 A 의
  이름이 통째로 빈다.

> ⚠️ **`pet_repo.member_condition` 을 넓히지 않는다.** chat·gait_record·screening·
> territory_claim·walk_entry 다섯이 그것을 같이 쓰므로, 넓히면 이번 MVP 가 **보류한**
> 보행·스크리닝·대화·점령까지 조용히 공유된다. 케어·산책 전용 헬퍼
> (`pet_identity.group_pet_ids_of`)를 따로 둔 이유다.

### 승계 — 연결된 보호자에게는 아직 못 넘긴다

`transfer_owner` 는 앵커 행의 `pets.app_user_id` 를 대상으로 옮긴다. 그런데 **대상이 이미 그
그룹에 자기 행을 갖고 있으면** 한 사람이 한 그룹에 행 둘을 갖게 되어
`pets_identity_one_per_user` 부분 UNIQUE 를 위반한다 — 막지 않으면 500 이다.
(일회용 PostgreSQL 에서 `duplicate key value violates unique constraint` 로 재현했다.)

그래서 **409 `linked_owner_transfer_unsupported`** 로 막는다. 아무것도 안 바뀐다 — 소유·
멤버십·`owner_pet_id` 전부 그대로다.

**연결 없이 참여한 보호자에게 넘기는 것은 그대로 된다** — 대상이 그룹에 자기 행이 없어
충돌할 것이 없다. 앵커도 그대로라 그룹 주보호자가 새 대표를 따라간다.

우회로는 "내보냈다가 다시 초대" 다 — 내보내기가 연결도 같이 풀기 때문이다.

> **완전 지원안은 고도화로 남긴다.** 행 소유를 옮기는 대신 `pet_identities.owner_pet_id` 를
> 대상의 행으로 **옮기면** 된다(101 은 A 가 그대로 갖고, 공통 정보 출처만 202 로 간다).
> 그러면 승계의 뜻이 "행 소유 이전" 과 "앵커 이동" 둘로 갈리므로, 일반 연결 해제와 같이
> 판단할 제품 결정이다.

### 연결 수명주기

나가기·내보내기는 **멤버십 제거와 연결 해제를 한 트랜잭션에서** 한다
(`remove_member` → `detach_user` → `prune`). 멤버십만 지우고 연결이 남으면 나간 사람의 pet 행이
그룹에 그대로 있어 **공동 조회로 남의 집 기록을 계속 읽는다.**

그룹에 pet 행이 하나 이하로 줄면 `prune` 이 연결을 풀고 그룹 행을 지운다 — 연결 이전과 똑같은
모양으로 되돌아간다. 앵커를 지우면 `pet_identities.owner_pet_id` 의 CASCADE 가 그룹 행을 없애고
`pets.identity_id` 의 SET NULL 이 남은 행을 되돌린다. **일반 연결 해제 화면·API 는 후속이다.**

### 배포 순서 — 마이그레이션이 먼저다

`db/migrations/2026-09-12_pet_identities.sql` → `2026-09-12_pet_invite_pets.sql` → 서버 → 앱.

**마이그레이션이 먼저인 것이 이번에는 확실하다** — 덧붙이기만 하므로 구 코드가 그대로 돈다.
머지가 먼저면 `pets.identity_id` 를 찾는 모든 `GET /app/pets` 가 죽는다.

그 사이의 창에서 **옛 서버 코드가 새 스키마에 INSERT** 한다. 그래서 `pet_count` 에 `DEFAULT 1`
이 있어야 하고, 자식 줄이 하나도 없는 초대는 "구성이 바뀐 것" 이 아니라 "옛 초대" 로 보고 앵커
하나짜리 묶음으로 다룬다.

**새 앱이 구 서버를 만나면 조용히 틀린다** — `POST /app/pet-invites/preview` 는 404 지만, 구
서버의 `InviteAccept` 는 pydantic 기본값(`extra="ignore"`)이라 **`links` 를 소리 없이 버리고
200** 을 낸다. 사용자가 연결을 골랐는데 연결 없이 참여된다. 그래서 앱은 **미리보기 성공을 새
계약 사용의 전제**로 삼고, 404 면 단일 흐름으로 폴백해야 한다.

### 롤백 — 🔴 "표시만 퇴행" 이 아니다

되돌리는 길이 둘인데, **둘 다 대가가 있다.** 어느 쪽도 "적용 전으로 돌아간다" 가 아니다.

#### 길 ① 코드 롤백 + 스키마 유지 (1순위)

서버만 옛 코드로 되돌리고 새 표·새 칸은 남긴다. 데이터는 안 지워진다 — 그 점에서 길 ② 보다
낫고, 그래서 1순위다. 옛 코드가 새 스키마에서 도는 것 자체는 배포 순서가 이미 요구하는
성질이라 확인됐다(일회용 PostgreSQL 에서 옛 코드의 초대 생성이 `pet_count DEFAULT 1` 로
그대로 도는 것을 실측).

**그런데 규칙이 함께 되돌아간다.** 아래는 코드를 읽어 정리한 것이고, 실제로 옛 코드를 새
스키마 위에서 돌려 본 범위는 **초대 생성 하나뿐**이다 — 나머지는 **미검증**이다.

| 되돌아가는 규칙 | 무슨 일이 생기나 |
| --- | --- |
| **묶음 수락이 부분 수락이 된다** | 옛 `accept_invite` 는 `pet_invite_pets` 를 모르고 `invite.pet_id` **하나만** 본다. 두 마리 묶음 토큰이 오면 **앵커만 참여시키고 `accepted_by` 를 찍는다.** 그 토큰은 그대로 소진돼, 나중에 새 코드로 돌아와도 영수증 경로가 앵커만 돌려준다 — **나머지 아이는 영영 안 들어온다.** 이번 MVP 가 구조적으로 금지한 바로 그 동작이다 |
| **`link_selection_required` 가드가 사라진다** | 구 앱이 토큰만 보내는 것을 막던 409 가 없어진다. 위와 같은 일이 조용히 일어난다 |
| **그룹 관리 가드가 사라진다** | 옛 `update_pet`·`delete_pet` 에는 `is_group_owner` 개념이 없다. 연결된 행의 **견종·생일·건강정보를 각자 고칠 수 있게 되어 그룹 공통 정보가 갈라진다.** 다시 새 코드로 올리면 앵커 값만 보이므로, 그 사이 남이 고친 값은 **조용히 안 보이게 된다**(지워지지는 않는다) |
| **상한이 물리 행 기준으로 돌아간다** | 옛 `count_accessible` 은 `pets` 행을 센다. 연결한 사람은 5마리 상한을 두 칸 먹고, 상한에 걸려 새 등록이 막힐 수 있다 |
| **목록이 다시 두 장이 된다** | 접기가 없어져 같은 아이가 카드 두 장으로 보인다. 이것만이 "표시 퇴행" 이다 |
| **`PATCH /app/pets/{id}/display` 가 404** | 새 앱이 이름 수정에 실패한다 |
| **케어·산책 공동 조회가 좁아진다** | 그룹으로 보이던 남의 행 기록이 안 보인다. 기록은 남아 있다 |

**그래서 코드 롤백을 하려면 초대 링크부터 끊어야 한다.** 살아 있는 묶음 초대를 남긴 채
롤백하면 위 첫 줄이 실제로 일어난다. 롤백 전에 활성 묶음을 지우는 것이 안전하다:

```sql
-- 두 마리 이상 묶음 중 아직 수락 안 된 것만. 앵커 한 마리짜리는 옛 코드도 정상 처리한다.
DELETE FROM pet_invites WHERE pet_count > 1 AND accepted_by IS NULL;
```

#### 길 ② 스키마까지 DROP (최후)

```sql
ALTER TABLE pet_invites DROP CONSTRAINT IF EXISTS pet_invites_pet_count_check;
ALTER TABLE pet_invites DROP COLUMN IF EXISTS pet_count;
DROP TABLE IF EXISTS pet_invite_pets;
DROP INDEX IF EXISTS pets_identity_one_per_user;
DROP INDEX IF EXISTS idx_pets_identity;
ALTER TABLE pets DROP COLUMN IF EXISTS identity_id;
DROP TABLE IF EXISTS pet_identities;
```

**무손실이 아니다.** 기능이 한 번이라도 쓰인 뒤라면 그 뒤에 생긴 것이 새 표·새 칸에만 있다:

- `pet_identities` · `pets.identity_id` → **사용자가 맺은 연결 전부.** 되살리려면 사람이 다시
  초대·수락해야 한다.
- `pet_invite_pets` → 묶음 구성과 `linked_pet_id` **영수증**. 재시도가 그때의 강아지별 매핑을
  복원하지 못한다.
- `pet_invites.pet_count` → 구성 변경 감지 근거.

**안 사라지는 것**: `pets` 행, `pet_members` 구성원, 13개 표의 `pet_id` 기록. 물리 병합을 안
한 이유가 이것이다 — 되돌려도 **사람과 기록은 남는다.** 하지만 "손실 0" 은 아니다.

지워야 한다면 그 전에 따로 떠 둔다:

```bash
pg_dump -Fc -t pet_identities -t pet_invite_pets -t pets -t pet_invites <db> > before-drop.dump
```

#### 아직 검증하지 않은 것

- 옛 코드로 **묶음 토큰을 실제로 수락**시켜 위 첫 줄을 재현해 보지 않았다. 코드를 읽은
  판단이다.
- 옛 코드로 **연결된 행을 수정·삭제**해 그룹 공통 정보가 갈라지는 것을 재현해 보지 않았다.
- 길 ② 의 DROP 을 실제로 돌려 보지 않았다(돌리면 그 DB 의 연결이 사라지므로, 필요하면
  일회용 DB 에서 따로 본다).

**롤백을 실제로 하게 되면 그 전에 위 셋을 일회용 DB 에서 먼저 재현하고 절차를 확정한다.**

### 고도화로 남긴 것

- 공동 보호자의 공식 건강정보 **변경 요청**과 주보호자 승인 (`변경 요청 → 확인 → 반영`)
- 변경 이력·감사 로그·되돌리기
- 일반 연결 해제 화면/API 와, 해제 시 공통 상태·개인 원본의 충돌 정책
- **연결된 보호자에게의 승계** — 지금은 409 로 막는다 (위 "승계" 절)
- **최초 등록자 보존 컬럼** — 지금은 `대표 + 공동 보호자 없음` 이 근사 조건이다
- 배웅을 그룹 공통 상태로 (지금은 배웅한 아이를 초대·후보에서 빼는 최소 안전안)
- 보행·스크리닝·사진첩·통계의 선택 공유와, 주보호자의 항목별 공유 범위 설정
- App Links 와 웹 안내 페이지

---

## 앱 계약이 바뀐 자리 아홉

한쪽만 고치면 조용히 깨지는 자리다. 앱 짝 PR 이 아홉 다 반영해야 한다.

- **`PetResponse.is_owner`** (기본값 `true`, `schemas/pet.py`). 돌보미로 참여 중인 아이도
  이제 `GET /app/pets` 목록에 섞여 오고, `is_owner` 로만 갈린다. 앱이 이 값을 안 보면
  돌보미의 화면에 수정·배웅·삭제·사진 버튼이 그대로 뜨고, 누르면 대표만 허용된 자리라
  404 가 난다.
- **마릿수 상한의 뜻** — "내가 등록한 아이 수" 가 아니라 **"내 미니룸에 서는 아이 수"**
  (대표 ∪ 돌보미)다. §2 "마릿수 상한의 의미가 바뀐다" 참고.
- **초대 수락의 재시도는 같은 토큰을 그대로 다시 보내는 것이다** — 2026-09-10 의 영수증
  개정으로 바뀐 자리다 (§3 "수락 영수증"). 같은 사람이면 그때의 응답을 그대로 200 으로
  받고, 다른 사람이면 404, 만료면 410 이다. **`GET /app/pets` 목록으로 성공 여부를
  추측하지 말 것** — 그 목록은 다른 초대·다른 기기의 결과일 수 있어 근거가 못 된다.
  (이 항목은 영수증 이전의 "재시도는 404" 를 그대로 적고 있었다. 2026-09-12 에 §6 이 같은
  계약을 다시 건드리면서 바로잡았다.)
- **보행·스크리닝 둘 다 "새 기록" 버튼이 돌보미 화면에 뜬다** (보행은 Task 12, 스크리닝은
  Task 14). 보행 생성은 `get_accessible`(구성원)로 열렸다(§2 "보행 **생성**은 구성원이 연다").
  스크리닝 생성도 같은 함수로 열렸다(§2 "스크리닝도 이제 구성원이 연다") — 단, 아이를
  지정했을 때만이다. `is_owner` 로 가려야 하는 것은 여전히 수정·배웅·삭제·사진(강아지
  프로필)뿐이다 — 그 자리에서 누르면 대표만 허용된 자리라 404 다. **보행·스크리닝의
  확정(개별 기록 상태 변경)은 `is_owner` 로 가릴 수 없다** — Task 19(2026-09-10)로 보행
  쪽 기준이 바뀌었다: 보행은 **업로더 본인 또는 대표**, 스크리닝은 그대로 **창작자**뿐이다.
  어느 쪽도 `is_owner` 하나로는 못 가리므로 아래 `can_confirm`/`can_delete` 항목을 보라.
  **스크리닝의 삭제는
  창작자 또는 대표 둘 다 된다** — 돌보미가 만든, 아이에 붙은 기록을 대표가 지울 수 있다.
  다른 돌보미(창작자도 대표도 아닌)는 볼 수는 있어도 지우지는 못한다(404). **아이를 지정하지
  않은 스크리닝 기록(개인 기록)은 여전히 창작자만 본다** — 목록·상세 어느 쪽에서도 구성원에게
  안 보인다. 앱이 "아이 없이 찍은 사진도 우리 집 모두가 본다"고 잘못 가정하면 안 된다.
- **`DELETE /app/pets/{pet_id}` 가 돌보미 있는 강아지에서 409 를 낼 수 있다** (Task 13, §3
  "직접 삭제"). 약 중복 확인(§4)과 같은 dict `detail` 모양 — `pet_name` 과 `carers`(각각
  `app_user_id`·`nickname`)가 실려 온다. 앱은 이것을 "아빠도 맥스를 돌보고 있어요. 정말
  지울까요?" 로 그리고, 사용자가 그래도 지우겠다고 하면 **같은 요청을 `?confirm=true` 를
  붙여 재전송**해야 한다 — body 가 아니라 쿼리다(§3 에 이유가 있다). 이 409 를 처리하지
  않으면 돌보미가 있는 강아지의 삭제가 항상 실패로 보인다. 탈퇴 409(위 항목들과 별개,
  §3 "탈퇴 가드")와 **혼동하지 말 것** — 그쪽은 승계/내보내기 두 출구를 안내해야 하고
  `confirm` 으로 넘어가지 않는다.
- **`can_confirm` · `can_delete` 가 gait·screening 응답 둘 다에 생겼다** (Task 19,
  `GaitRecordSummary`·`ScreeningRecordResponse`). **서버가 요청자 기준으로 계산해 내려준다**
  — 앱은 `is_owner` 로 이 값을 흉내 내면 안 된다. gait 확정은 대표뿐 아니라 업로더 본인도
  되고, screening 삭제는 창작자 또는 대표 둘 다 되는데 `is_owner` 하나로는 둘 다 못
  가린다. `is_owner` 로 게이트를 걸면 창작자인 돌보미가 자기 기록을 못 지우게 그리거나,
  반대로 전부 보여주면 다른 돌보미가 눌러서 404 를 받는다.
- **`created_by`** — 만든 사람의 닉네임. 케어 로그의 `actor` 라벨과 같은 규칙이다: **지금도
  그 강아지의 구성원일 때만** 닉네임이 실리고, 아니면 `null` 이다(탈퇴·내보내기, 또는 이
  칸이 생기기 전의 옛 gait 기록 — `actor_app_user_id` 가 NULL). 앱이 이것을 "항상 있는
  값"으로 가정하면 탈퇴한 돌보미가 만든 옛 기록에서 이름이 빈다.
- **다중 초대·논리 연결로 계약이 더 바뀌었다 (2026-09-12, §6).** 요약하면 넷이다 —
  ① `PetResponse.is_group_owner` 가 생겼고 **연결 안 된 아이는 `is_owner` 와 언제나 같은 값**
  이라 구 앱은 그대로 돈다. ② 연결된 아이의 `PUT /app/pets/{id}` 와 `DELETE /app/pets/{id}` 가
  비(非)그룹주보호자에게 **409**(`detail.code = "not_group_owner"`)이고, 삭제 쪽은
  `?confirm=true` 로도 안 뚫린다(돌보미 확인 409 와 다른 것이다). ③ 이름은
  `PATCH /app/pets/{id}/display` 로 옮겼다. ④ 수락 응답에 항목별 `invited_pet_id` ·
  `display_pet_id` · `result` 가 생겼고, **이후 요청에 쓸 id 는 `display_pet_id`** 다.
  최상위 `pet_id`·`name` 은 구 앱 호환 앵커로 남는다. 자세한 것은 §6.
- **보행 확정(`POST /app/gait/records/{id}/confirm`)이 업로더 본인에게도 열렸다** (Task 19,
  §2 "보행 carer's gait recording is broken halfway"). 이전에는 티켓 발급·업로드는
  구성원에게 열려 있는데 확정만 대표라, 돌보미가 영상을 다 올리고도 confirm 에서 404 를
  받았다 — "닫혀 있었다"보다 나쁜, 영상만 올라가고 못 끝내는 반쯤 열린 상태였다. 지금은
  "새로 시작"부터 "확정"까지 돌보미 혼자 끝낼 수 있다. **삭제는 그대로 대표만이다** — 이
  변경은 확정에만 해당한다.

---

## 머지 전에 할 일 — 순서가 중요하다

**마이그레이션이 머지보다 먼저다.** 이 변경은 덧붙이기만 하지 않는다 —
`care_events.app_user_id` 를 `actor_app_user_id` 로 **개명**한다. 그래서 두 순서가 각각
다른 것을 깨뜨리는데, 넓이가 다르다.

| 순서 | 깨지는 것 |
| --- | --- |
| 마이그레이션 먼저 | 지금 도는 backend 가 `app_user_id` 를 SELECT 해서 **케어 로그만** 500 |
| 머지 먼저 | 새 코드가 `pet_members` · `actor_app_user_id` 를 찾는다. 없으면 `get_accessible` 을 쓰는 **모든 경로**(케어 · 대화 · 보행 · 산책 기록)가 죽는다 |

**`dev` 머지는 곧 자동 배포다** (`deploy.yml` 이 `push: branches: [dev]`). 그러니 순서는:

1. 로컬에서 `cd backend && uv run pytest` 를 한 번 통과시킨다 — 트리거·FK 층
   (`test_pet_membership_postgres.py`)이 그 안에서 실물 검증된다. **`backend-tests.yml` 은
   `ubuntu-latest` 라 이 팀의 GitHub 호스티드 러너 미사용 결정 때문에 돌지 않는다** — postgres
   서비스가 붙어 있어도 아무도 실행하지 않으니, PR 이 초록이어도 그것으로 착각하지 말 것.
2. Actions 탭에서 **`db-migrate.yml`** 을 돌린다 — 이건 `self-hosted` 라 실제로 돈다.
   `file=2026-09-09_pet_members.sql`, `ref=docs/co-care-design`(**아직 머지 안 된 브랜치도 된다**),
   `verify=true`. 짝 파일(`verify_2026-09-09_pet_members.sql`)이 적용 직후 스키마를 단언으로
   검사하고, 틀리면 시끄럽게 실패한다.
3. 머지한다.

**서버 PC 터미널에서 직접 하지 말 것.** 거기서는 `127.0.0.1` 이 곧 팀 공용 DB 라 테스트의
loopback 가드가 무의미해지고, `db/migrations/README.md` 의 "서버 PC 터미널의 함정 셋"
(compose 가 설정 해석에서 죽는 것 · PowerShell 의 `<` · 파이프가 한글 주석을 깨는 것)이 그대로
기다린다. 같은 내용이 마이그레이션 파일 머리에도 적혀 있다 — 파일 이름을 고르는 사람이
그 순간 읽는 자리라서다.

## 열린 것

- ~~**미니룸이 돌보미의 강아지를 세우는가**~~ — **닫혔다.** 앱이 A안(돌보미의 아이도 내
  미니룸에 선다)으로 확정했고(`DAENGS_APP docs/co-care-contract.md` ⑤), 상한은 구성원
  기준으로 남았다. 2026-09-12 부터는 그 위에 **논리 강아지** 기준이 한 겹 더 얹혔다 (§6).
- **앱 짝 PR** — 초대 링크 딥링크 처리, 탈퇴 409 화면, 약 확인 다이얼로그, `actor` 표시,
  스크리닝 "새 기록" 버튼이 이제 돌보미 화면에도 뜨는 것.
