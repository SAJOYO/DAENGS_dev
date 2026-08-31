-- =====================================================================
-- 06_walks.sql
-- 앱 회원의 산책 기록
-- 실행 순서: 01_schema -> 02_trigger -> 03_auth -> 04_crawl_runs -> 05_pets -> 06_walks
-- =====================================================================
--
-- app_users 와 pets 를 FK 로 참조하므로 05_pets 뒤에 온다.
--
-- **왜 서버에 두나.** 지금까지 산책은 기기 안 SQLite 에만 있었다. 앱을 지우거나
-- 폰을 바꾸면 걸은 기록이 통째로 사라진다. 강아지는 계정에 붙어 있는데(pets)
-- 산책만 안 붙어 있었다.

-- ---------------------------------------------------------------------
-- walks : 산책 한 번. **끝난 것만 올라온다.**
-- ---------------------------------------------------------------------
-- 기기가 강제 종료되어 열린 채 남은 세션은 기록이 아니라 사고의 흔적이라
-- 앱이 올리지 않는다. 그래서 ended_at 이 NOT NULL 이다.
--
-- **거리와 시간을 저장하지 않는다.** 좌표에서 다시 계산하는 값이라 여기에 적어
-- 두면 계산 규칙(앱의 흔들림 필터 문턱값)을 고쳤을 때 저장된 숫자와 새로 계산한
-- 숫자가 갈라진다 — 그때 어느 쪽이 맞는지 아무도 모른다. 목록이 무거워지면
-- 그때 캐시 컬럼을 더한다.
CREATE TABLE IF NOT EXISTS walks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 계정이 지워지면 산책도 같이 지운다. pets 와 같은 이유다 — 탈퇴가
    -- 개인정보를 파기하는데 위치 기록이 남으면 파기가 반쪽이다.
    -- 산책 경로는 집과 생활권을 그대로 드러낸다.
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    -- 누구와 걸었나. **강아지를 지워도 산책은 남는다** — 무지개다리를 건넌 아이와의
    -- 산책이 그 아이를 지웠다고 없던 일이 되면 안 된다. 기록은 사람의 것이다.
    pet_id UUID REFERENCES pets(id) ON DELETE SET NULL,

    -- 기기가 만든 세션 id 를 그대로 받는다. 기기의 로컬 DB 와 **같은 값**이라
    -- 올릴 때도 되찾을 때도 같은 id 로 맞춰 본다.
    client_session_id UUID NOT NULL,

    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,

    -- 나갈 때의 날씨. WMO 원본 코드다 (https://open-meteo.com/en/docs).
    -- **접어서 저장하지 않는다** — "비"로만 남기면 소나기였는지 뇌우였는지 못
    -- 되살린다. 못 받았으면 셋 다 NULL 이고, 그걸 "맑음"으로 채우지 않는다.
    weather_code INTEGER,
    is_day BOOLEAN,
    temperature_c NUMERIC(4, 1),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT walks_time_order CHECK (ended_at >= started_at),

    -- **재시도가 안전해야 한다.** 앱은 네트워크가 끊기면 다음에 다시 올리는데,
    -- 그때 같은 산책이 두 건이 되면 안 된다. 이 제약이 그걸 DB 에서 막는다.
    CONSTRAINT walks_client_session_unique UNIQUE (app_user_id, client_session_id)
);

-- 목록은 늘 "내 것, 최근 순"이다.
CREATE INDEX IF NOT EXISTS walks_owner_started_idx
    ON walks (app_user_id, started_at DESC);

-- ---------------------------------------------------------------------
-- walk_points : 기기가 준 **원본 좌표**
-- ---------------------------------------------------------------------
-- 화면에 그리는 선은 흔들림을 걸러낸 것이지만 여기 올라오는 것은 거르기 전이다.
-- 문턱값은 나중에 바뀔 수 있고, 그때 버린 점을 되살릴 수 있어야 한다 —
-- 기기의 로컬 DB 가 원본만 남기는 것과 같은 이유다.
CREATE TABLE IF NOT EXISTS walk_points (
    walk_id UUID NOT NULL REFERENCES walks(id) ON DELETE CASCADE,

    -- 기기가 한 산책 안에서 0부터 매긴 순번. **PK 의 일부다** — 같은 점을 두 번
    -- 보내도 한 줄이다. 앱이 좌표를 나눠 올리게 되어도 이 성질이 유지된다.
    client_seq INTEGER NOT NULL,

    -- 일시정지나 GPS 점프 뒤에 증가한다. 값이 다른 두 점을 직선으로 이으면
    -- 걷지 않은 길이 그려지므로, 서버도 이 값을 그대로 보관한다.
    chain_index INTEGER NOT NULL,

    at TIMESTAMPTZ NOT NULL,
    lat NUMERIC(9, 6) NOT NULL,
    lng NUMERIC(9, 6) NOT NULL,
    accuracy_m REAL,

    -- 가상 위치로 만든 기록. 지우지 않고 표시만 해 둔다 — 나중에 점수나 랭킹이
    -- 생기면 걸러야 할 값이고, 그때 원본이 없으면 가릴 수가 없다.
    is_mock BOOLEAN NOT NULL DEFAULT FALSE,

    PRIMARY KEY (walk_id, client_seq)
);
