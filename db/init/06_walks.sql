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

    -- 누구와 걸었나는 walk_pets 에 있다 (아래). 한 번에 두 마리를 데리고 나가므로
    -- 한 칸으로는 못 담는다.

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

    -- collecting 동안만 좌표 입력을 바꿀 수 있다. derived는 현재 입력이 봉인됐다는
    -- 뜻이고, 계산 세대 자체는 walk_analyses가 따로 식별한다.
    analysis_state VARCHAR(16) NOT NULL DEFAULT 'collecting',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT walks_time_order CHECK (ended_at >= started_at),
    CONSTRAINT walks_analysis_state_check
        CHECK (analysis_state IN ('collecting', 'derived')),
    CONSTRAINT walks_weather_code_range CHECK (weather_code BETWEEN 0 AND 99),
    CONSTRAINT walks_temperature_c_range CHECK (temperature_c BETWEEN -100 AND 100),

    -- **재시도가 안전해야 한다.** 앱은 네트워크가 끊기면 다음에 다시 올리는데,
    -- 그때 같은 산책이 두 건이 되면 안 된다. 이 제약이 그걸 DB 에서 막는다.
    CONSTRAINT walks_client_session_unique UNIQUE (app_user_id, client_session_id)
);

-- 목록은 늘 "내 것, 최근 순"이다.
CREATE INDEX IF NOT EXISTS walks_owner_started_idx
    ON walks (app_user_id, started_at DESC);

-- ---------------------------------------------------------------------
-- walk_point_chunks : 기기가 준 **원본 좌표**를 묶음으로
-- ---------------------------------------------------------------------
-- 화면에 그리는 선은 흔들림을 걸러낸 것이지만 여기 올라오는 것은 거르기 전이다.
-- 문턱값은 나중에 바뀔 수 있고, 그때 버린 점을 되살릴 수 있어야 한다 —
-- 기기의 로컬 DB 가 원본만 남기는 것과 같은 이유다. **하나도 안 버린다.**
--
-- **왜 점마다 한 줄이 아닌가.** 예전에는 walk_points 가 좌표 한 점에 한 줄이었다.
-- 실기기 실측으로 초당 1.02점이 쌓여서, 30분 산책이면 1,842줄이다. 그런데 이 좌표를
-- 조건으로 거는 질의가 **하나도 없다** — 늘 "한 산책의 전부"를 통째로 읽어 JSON 으로
-- 내보낼 뿐이다. 점당 실제 데이터는 12바이트쯤인데 행 하나에 124바이트(힙 88 +
-- PK 인덱스 32)를 내고 있었다. 90%가 행 헤더였다.
--
-- **왜 jsonb 이고, 왜 배열인가.** 이진(bytea)으로 담으면 더 작지만 우리가 평생
-- 관리할 인코더·디코더와 형식 버전이 생긴다. jsonb 는 psql 에서 눈으로 보이고,
-- 숫자가 numeric 이라 부동소수점 반올림 걱정도 없다. 다만 **키를 점마다 반복하면
-- 안 된다** — 객체로 담으면 일곱 개 키가 1,842번 들어가 압축 전 151바이트/점이다.
-- 위치 배열로 담으면 62바이트/점이고, TOAST 압축까지 거치면 22바이트/점이다.
--
--   실측(131점 트랙) : 점당 한 줄 124 B → jsonb 배열 22 B  (5.6배)
--
-- ⚠️ TOAST 는 값이 2KB 를 넘어야 압축한다. 아주 짧은 산책은 압축 없이 남는다.
--
-- **보관 기간은 탈퇴 시까지다.** 아래 CASCADE 가 그것을 보장한다 — 따로 만료
-- 배치를 두지 않는다 (2026-09-02 팀 결정).
CREATE TABLE IF NOT EXISTS walk_point_chunks (
    walk_id UUID NOT NULL REFERENCES walks(id) ON DELETE CASCADE,

    -- 이 묶음이 담은 첫 순번. **PK 의 일부다** — 앱이 같은 묶음을 다시 보내도
    -- 한 줄이고, 순서가 뒤바뀌어 도착해도 결과가 같다. 예전 client_seq 가 하던 일을
    -- 묶음 단위로 옮긴 것이다.
    seq_from INTEGER NOT NULL,

    -- 이 묶음이 담은 마지막 순번. 재시도 판정을 payload 를 풀지 않고 하려고 둔다.
    seq_to INTEGER NOT NULL,

    -- 묶음 안의 점 개수. 세려고 payload 를 풀지 않게 한다.
    point_count INTEGER NOT NULL CHECK (point_count > 0),

    -- 좌표 묶음. 모양은 아래 한 벌로 고정한다.
    --
    --   {"v": 1,
    --    "cols": ["seq", "chain", "at", "lat", "lng", "acc", "mock"],
    --    "pts": [[0, 0, 1788174280387, 37.566500, 126.977900, 5.7, 0], ...]}
    --
    -- `at` 은 epoch 밀리초, `acc` 는 미터(없으면 null), `mock` 은 0/1 이다.
    -- **cols 를 같이 적는 이유**: 위치 배열은 스스로를 설명하지 못한다. 나중에 칸이
    -- 늘거나 순서가 바뀌어도 읽는 쪽이 v 와 cols 를 보고 맞출 수 있어야 한다.
    payload JSONB NOT NULL,

    CONSTRAINT walk_point_chunks_seq_order CHECK (seq_to >= seq_from),

    PRIMARY KEY (walk_id, seq_from)
);

-- ---------------------------------------------------------------------
-- walk_analyses : 봉인된 입력을 한 계산 세대로 해석한 불변 결과
-- ---------------------------------------------------------------------
-- 같은 원본도 계산 정책이 바뀌면 새 행으로 쌓는다. Paint는 이 결과를 소비하는 별도
-- 세대라 아래 walk_cellophane_sheets가 맡는다 — Paint만 바뀌었다고 Facts를 복제하지 않는다.
CREATE TABLE IF NOT EXISTS walk_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    walk_id UUID NOT NULL REFERENCES walks(id) ON DELETE CASCADE,

    input_fingerprint VARCHAR(71) NOT NULL,
    point_count INTEGER NOT NULL,
    terminal_client_seq INTEGER,

    facts_record_version INTEGER NOT NULL,
    calculation_version INTEGER NOT NULL,
    receipt_version INTEGER NOT NULL,
    observation_version INTEGER NOT NULL,

    -- 목록·집계에서 먼저 쓸 값만 밖으로 꺼내고 전체 계약은 아래 JSONB로 보존한다.
    moving_distance_m INTEGER NOT NULL,
    moving_s INTEGER NOT NULL,
    stop_count INTEGER NOT NULL,

    facts JSONB NOT NULL,
    measurement_receipt JSONB NOT NULL,
    motion_events JSONB NOT NULL,
    micro_observations JSONB NOT NULL,
    derived_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT walk_analyses_input_fingerprint_check
        CHECK (input_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT walk_analyses_point_count_check CHECK (point_count >= 0),
    CONSTRAINT walk_analyses_terminal_sequence_check CHECK (
        (point_count = 0 AND terminal_client_seq IS NULL)
        OR (point_count > 0 AND terminal_client_seq = point_count - 1)
    ),
    CONSTRAINT walk_analyses_versions_positive CHECK (
        facts_record_version > 0
        AND calculation_version > 0
        AND receipt_version > 0
        AND observation_version > 0
    ),
    CONSTRAINT walk_analyses_summary_nonnegative CHECK (
        moving_distance_m >= 0 AND moving_s >= 0 AND stop_count >= 0
    ),
    CONSTRAINT walk_analyses_facts_object CHECK (jsonb_typeof(facts) = 'object'),
    CONSTRAINT walk_analyses_receipt_object
        CHECK (jsonb_typeof(measurement_receipt) = 'object'),
    CONSTRAINT walk_analyses_events_array CHECK (jsonb_typeof(motion_events) = 'array'),
    CONSTRAINT walk_analyses_observations_array
        CHECK (jsonb_typeof(micro_observations) = 'array'),
    CONSTRAINT walk_analyses_identity_unique UNIQUE (
        walk_id,
        input_fingerprint,
        facts_record_version,
        calculation_version,
        receipt_version,
        observation_version
    )
);

CREATE INDEX IF NOT EXISTS walk_analyses_walk_derived_idx
    ON walk_analyses (walk_id, derived_at DESC);

-- ---------------------------------------------------------------------
-- walk_cellophane_sheets : 분석 결과를 한 Paint spec으로 칠한 compact sheet
-- ---------------------------------------------------------------------
-- 셀당 한 행은 아직 만들지 않는다. 현재 필요한 것은 한 산책의 장 전체를 쓰고 읽는
-- 경로뿐이라, 정렬된 [q,r,occupancy_s,peak] 배열을 JSONB 한 건으로 보존한다.
CREATE TABLE IF NOT EXISTS walk_cellophane_sheets (
    analysis_id UUID NOT NULL REFERENCES walk_analyses(id) ON DELETE CASCADE,
    paint_fp VARCHAR(128) NOT NULL,
    sheet_schema_version INTEGER NOT NULL,
    paint_version INTEGER NOT NULL,
    grid_version VARCHAR(64) NOT NULL,
    radius_u DOUBLE PRECISION NOT NULL,
    profile VARCHAR(128) NOT NULL,
    profile_fp VARCHAR(128) NOT NULL,
    sample_step_m DOUBLE PRECISION NOT NULL,
    cell_count INTEGER NOT NULL,
    sheet_fingerprint VARCHAR(71) NOT NULL,
    payload JSONB NOT NULL,
    derived_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT walk_cellophane_versions_positive
        CHECK (sheet_schema_version > 0 AND paint_version > 0),
    CONSTRAINT walk_cellophane_spec_positive CHECK (radius_u > 0 AND sample_step_m > 0),
    CONSTRAINT walk_cellophane_identity_nonempty CHECK (
        paint_fp <> '' AND grid_version <> '' AND profile <> '' AND profile_fp <> ''
    ),
    CONSTRAINT walk_cellophane_cell_count_check CHECK (cell_count >= 0),
    CONSTRAINT walk_cellophane_fingerprint_check
        CHECK (sheet_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT walk_cellophane_payload_object CHECK (jsonb_typeof(payload) = 'object'),

    PRIMARY KEY (analysis_id, paint_fp)
);

CREATE INDEX IF NOT EXISTS walk_cellophane_paint_fp_idx
    ON walk_cellophane_sheets (paint_fp);

-- ---------------------------------------------------------------------
-- walk_capsules : 한 분석 원판이 공간 기억 소비에 준비됐다는 1:1 seal
-- ---------------------------------------------------------------------
-- Facts·Receipt·Observation·Cellophane을 다시 복제하지 않는다. analysis_id 하나로
-- 그 불변 결과를 가리키고, 당시 환경 원자와 읽을 수 있는 관측 세대만 덧붙인다.
-- 강아지는 capsule 컬럼이 아니라 walk_pets로 연결한다. 한 산책에 여러 마리가
-- 나갈 수 있고, 이후 개체별 일기는 그 연결 위에서 별도 기록해야 하기 때문이다.
CREATE TABLE IF NOT EXISTS walk_capsules (
    analysis_id UUID PRIMARY KEY REFERENCES walk_analyses(id) ON DELETE CASCADE,
    capsule_version INTEGER NOT NULL,
    context_version INTEGER NOT NULL,
    capabilities JSONB NOT NULL,
    trail_context JSONB NOT NULL,
    sealed_at TIMESTAMPTZ NOT NULL,

    CONSTRAINT walk_capsules_versions_positive CHECK (
        capsule_version > 0 AND context_version > 0
    ),
    CONSTRAINT walk_capsules_capabilities_array CHECK (
        jsonb_typeof(capabilities) = 'array'
        AND jsonb_array_length(capabilities) > 0
    ),
    CONSTRAINT walk_capsules_context_object
        CHECK (jsonb_typeof(trail_context) = 'object')
);

-- ---------------------------------------------------------------------
-- walk_pets : 그 산책에 누가 나갔나
-- ---------------------------------------------------------------------
-- **한 번에 여러 마리를 데리고 나간다.** walks.pet_id 한 칸이던 것을 조인으로
-- 옮긴 이유다 — 두 마리를 데리고 나갔는데 한 아이의 기록만 남으면, 나중에 챗봇이
-- "이 아이 이번 주 운동량"을 말할 때 나머지 아이의 산책이 통째로 빈다.
--
-- 아무도 안 붙은 산책이 있을 수 있다. 강아지를 등록하기 전에 걸었거나, 고르지
-- 않고 나선 경우다. **그래도 산책은 기록이다** — 사람이 걸은 것은 걸은 것이다.
CREATE TABLE IF NOT EXISTS walk_pets (
    walk_id UUID NOT NULL REFERENCES walks(id) ON DELETE CASCADE,

    -- **여기서는 CASCADE 다.** 단수 pet_id 일 때는 SET NULL 이었다 — 무지개다리를
    -- 건넌 아이와의 산책이 없던 일이 되면 안 되니까. 그 뜻은 그대로다: 강아지를
    -- 지우면 이 연결만 사라지고 **산책 자체는 남는다.** 조인 행에 NULL 을 남기면
    -- "누군지 모를 아이" 라는 뜻 없는 줄이 쌓인다.
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    -- 같은 아이를 두 번 붙여도 한 줄이다. 앱이 재시도해도 연결이 안 늘어난다.
    PRIMARY KEY (walk_id, pet_id)
);

-- "이 아이가 나간 산책" 을 되짚는 쪽. 챗봇이 아이별 운동량을 물을 자리다.
CREATE INDEX IF NOT EXISTS walk_pets_pet_idx ON walk_pets (pet_id);
