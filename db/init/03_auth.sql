-- =====================================================================
-- 03_auth.sql
-- 인증/계정 스키마 - admin_users / app_users / refresh_tokens
--
-- refresh_tokens 가 app_users 를 FK 로 참조하므로 순서가 이렇다 (D-016).
-- 실행 순서: 01_schema.sql -> 02_trigger.sql -> 03_auth.sql -> (적재) -> indexes.sql
--
-- 02_trigger.sql 의 set_updated_at() 을 여기서 재사용하므로 02 보다 뒤여야 한다.
-- (트리거는 대상 테이블이 있어야 걸 수 있어서 02 에 넣지 못하고 이 파일 끝에 둔다)
-- =====================================================================


-- ---------------------------------------------------------------------
-- admin_users : 관리자 콘솔 계정. id/pw 로 로그인한다.
--
-- 앱 회원(app_users)과 한 테이블로 합치지 않는다.
--   - 인증 방식이 다르다 (id/pw vs 카카오 소셜)
--   - 수명이 다르다 (관리자는 퇴사까지, 회원은 탈퇴까지)
--   - 개인정보 노출 정도가 다르다 (app_users 는 컬럼이 암호화되어 있다)
-- 합치면 NULL 투성이 컬럼과 "이 행은 어느 쪽이냐" 분기가 계속 따라붙는다.
-- ---------------------------------------------------------------------
CREATE TABLE admin_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 로그인 아이디. 이메일이 아니라 별도 문자열이다.
    -- 관리자는 가입이 아니라 '발급'이라서 이메일 인증 절차가 없다.
    login_id VARCHAR(50) NOT NULL UNIQUE,

    -- Argon2id PHC 문자열 ($argon2id$v=19$m=...$...). 평문/가역 암호화 금지.
    -- 길이는 파라미터와 salt 길이에 따라 변하고, 파라미터를 올리면 더 길어진다.
    -- VARCHAR(n) 으로 잡아 두면 그때 ALTER 가 필요하므로 TEXT.
    password_hash TEXT NOT NULL,

    -- 감사 로그와 화면에 찍을 이름. 사내 계정이라 평문으로 둔다.
    -- (app_users.name_enc 와 다르다 - 그쪽은 서비스 이용자의 개인정보)
    name VARCHAR(50) NOT NULL,

    -- 권한 5단계. PG ENUM 이 아니라 VARCHAR + CHECK 인 이유는 D-014.
    -- 지금 실제로 발급하는 것은 ADMIN 하나이고 나머지 넷은 자리만 잡아 둔다.
    --   ADMIN     계정·권한 관리까지 전부
    --   OPERATOR  운영 데이터 CRUD + 개인정보 복호화 (계정 관리 제외)
    --   CURATOR   지식베이스 문서 + 검색 점검. 개인정보 접근 없음
    --   ANALYST   지표·검색 점검 조회. 쓰기 없음
    --   VIEWER    조회만. 개인정보는 마스킹
    role VARCHAR(20) NOT NULL
        CHECK (role IN ('ADMIN','OPERATOR','CURATOR','ANALYST','VIEWER')),

    -- 계정을 지우지 않고 막는 수단. 행을 지우면 감사 로그의 참조가 끊긴다.
    -- refresh_tokens 폐기는 '이미 발급된 세션'을 끊는 것이고,
    -- 이 컬럼은 '새로 로그인하는 것'을 막는 것이다. 둘 다 필요하다.
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','suspended')),

    last_login_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ---------------------------------------------------------------------
-- app_users : 앱 회원. 카카오 소셜 로그인. role 이 없고 status 만 있다.
--
-- 개인정보 컬럼은 앱단에서 AES-256-GCM 으로 암호화해 BYTEA 로 넣는다.
-- 암복호화 함수 자체는 이 카드가 아니라 짝 카드(core/crypto.py)에 있다.
-- 여기서는 컬럼만 만든다.
-- ---------------------------------------------------------------------
CREATE TABLE app_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 평문이다. 로그인마다 조회하는 조인 키라 암호화하면 blind index 가 또 필요하고,
    -- 카카오 밖에서는 의미가 없는 가명 식별자라 암호화 이득이 적다.
    -- 카카오 회원번호는 64비트 정수 범위라 BIGINT.
    kakao_id BIGINT NOT NULL UNIQUE,

    -- AES-256-GCM 암호문. nonce 와 인증 태그를 포함한 바이트열을 통째로 넣는다.
    email_enc BYTEA,

    -- 검색용 blind index. HMAC-SHA256(값, 전용 pepper) 의 hex 64자.
    --
    -- AES 는 같은 값을 넣어도 매번 다른 암호문이 나온다 (그래야 안전하다).
    -- 그래서 WHERE email_enc = ? 이 성립하지 않는다. 검색이 필요한 컬럼만
    -- 결정적인 해시를 따로 두고 거기에 UNIQUE 를 건다. 조회는 해시로, 표시는 복호화로.
    --
    -- 평문 SHA-256 이 아니라 HMAC 인 것은, pepper 가 없으면 후보값을 넣어 보며
    -- "이 이메일이 가입돼 있나"를 확인할 수 있기 때문이다.
    -- 카카오에서 이메일 동의를 못 받으면 NULL 이라 NOT NULL 을 걸지 않는다
    -- (UNIQUE 는 NULL 끼리 중복으로 보지 않아 여러 행이 NULL 이어도 된다).
    email_hash CHAR(64) UNIQUE,

    -- 검색할 일이 없어 blind index 없이 AES 만 한다.
    -- 나중에 전화번호로 찾아야 하면 그때 _hash 컬럼을 더하면 된다.
    phone_enc BYTEA,
    name_enc  BYTEA,

    -- 평문. 조회 조건으로 쓰고 개인정보가 아니다.
    --   active     정상
    --   suspended  이용 정지 (관리자가 막음)
    --   withdrawn  탈퇴 (개인정보 컬럼은 파기하고 행은 남긴다)
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','suspended','withdrawn')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ---------------------------------------------------------------------
-- refresh_tokens : 재발급 토큰. 강제 로그아웃을 위해 DB 에 둔다.
--
-- access token 은 DB 에 두지 않는다 (짧은 수명 + 검증만으로 끝난다).
-- refresh token 만 DB 에 두는 이유는 '서버가 세션을 끊을 수 있어야' 해서다.
-- 토큰이 self-contained 면 만료 전까지 서버가 손쓸 방법이 없다.
--
-- 관리자와 앱 회원의 세션을 **한 테이블에** 담는다 (D-016). 소유자 컬럼을 둘 두고
-- CHECK 로 '정확히 하나'를 강제한다. 앞서 미뤄 둔 '컬럼을 더할지 / 테이블을 나눌지'의
-- 답이다.
--
-- subject_type + subject_id 한 쌍으로 하면 컬럼은 깔끔해지지만 FK 를 걸 수 없다 -
-- 계정을 지워도 세션이 남고, 그걸 지우는 책임이 앱으로 넘어온다.
--
-- 테이블을 나누지 않는 이유는 회전과 재사용 감지 규칙(D-015)이 양쪽 똑같기 때문이다.
-- 나누면 그 미묘한 규칙이 두 벌이 되고, 그중 한쪽만 고치는 날이 온다.
--
-- app_users 를 이 테이블보다 **먼저** 만드는 이유도 이것이다 (FK 대상이라).
-- ---------------------------------------------------------------------
CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 소유자. **둘 중 정확히 하나만** 채운다 (맨 아래 CHECK).
    -- NULL 이 허용되는 것은 '없어도 된다'가 아니라 '다른 쪽이 채워졌다'는 뜻이다.
    --
    -- 계정을 지우면 세션도 같이 사라져야 해서 ON DELETE CASCADE 다.
    -- (다만 위 status 주석대로, 운영에서는 지우기보다 suspended 로 막는다)
    admin_user_id UUID REFERENCES admin_users(id) ON DELETE CASCADE,
    app_user_id   UUID REFERENCES app_users(id)   ON DELETE CASCADE,

    -- 토큰 원문의 SHA-256 hex 64자. 원문은 저장하지 않는다.
    -- DB 가 통째로 새어도 그것만으로 유효한 토큰이 되지는 않게 하려는 것.
    -- 비밀번호와 달리 Argon2 가 아니라 SHA-256 인 이유: 토큰은 서버가 만든
    -- 고엔트로피 난수라 사전 공격 대상이 아니고, 재발급마다 조회해야 해서 빨라야 한다.
    token_hash CHAR(64) NOT NULL UNIQUE,

    -- 만료 시각. 지났으면 거부하고, 배치로 지운다 (남겨 둬도 무해하지만 쌓인다).
    expires_at TIMESTAMPTZ NOT NULL,

    -- 폐기 시각. NULL 이면 살아 있는 세션이다.
    -- 행을 지우지 않고 시각을 남기는 이유는 '재사용 감지' 때문이다 -
    -- 폐기된 토큰이 다시 들어오면 탈취를 의심하고 그 계정의 세션을 전부 끊을 수 있다.
    revoked_at TIMESTAMPTZ,

    -- 세션 목록 화면에서 "어디서 로그인했는지"를 보여 주기 위한 것.
    -- 인증 판단에는 쓰지 않는다 (둘 다 클라이언트가 바꿀 수 있는 값이다).
    user_agent TEXT,
    ip INET,

    -- created_at 이 곧 발급 시각이다 (issued_at 을 따로 두지 않는다).
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 소유자가 정확히 하나여야 한다. 둘 다 NULL 이면 주인 없는 세션이 남고,
    -- 둘 다 차 있으면 "이 세션은 누구 것이냐"의 답이 코드마다 달라진다.
    -- num_nonnulls() 는 NULL 이 아닌 인자의 개수를 센다 (PostgreSQL 내장).
    CONSTRAINT refresh_tokens_one_subject_check
        CHECK (num_nonnulls(admin_user_id, app_user_id) = 1)
);


-- ---------------------------------------------------------------------
-- 인덱스
--
-- documents 와 달리 여기 인덱스는 indexes.sql 이 아니라 이 파일에 둔다.
-- indexes.sql 을 나중에 실행하는 이유는 '대량 적재 중 인덱스 갱신 비용' 때문인데,
-- 인증 테이블은 대량 적재가 없고 첫 로그인부터 인덱스가 필요하다.
--
-- UNIQUE 제약(login_id, token_hash, kakao_id, email_hash)은 인덱스를 자동으로
-- 만들므로 여기 다시 적지 않는다.
-- ---------------------------------------------------------------------

-- "이 사람의 세션 전부" - 강제 로그아웃과 세션 목록 화면.
-- FK 는 인덱스를 자동으로 만들지 않으므로 직접 걸어야 한다.
--
-- 부분 인덱스(WHERE ... IS NOT NULL)인 이유는 각 행이 둘 중 한 컬럼만 채우기
-- 때문이다. 그냥 걸면 인덱스마다 '반대쪽 주체의 행 전부'가 NULL 로 들어간다.
-- 조회는 WHERE admin_user_id = ? 형태라 부분 인덱스로도 그대로 탄다.
CREATE INDEX idx_refresh_tokens_admin ON refresh_tokens (admin_user_id)
    WHERE admin_user_id IS NOT NULL;
CREATE INDEX idx_refresh_tokens_app ON refresh_tokens (app_user_id)
    WHERE app_user_id IS NOT NULL;

-- 만료 토큰 정리 배치용.
CREATE INDEX idx_refresh_tokens_expires ON refresh_tokens (expires_at);


-- ---------------------------------------------------------------------
-- updated_at 트리거 (함수는 02_trigger.sql 의 set_updated_at())
-- ---------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_admin_users_updated_at ON admin_users;
CREATE TRIGGER trg_admin_users_updated_at
    BEFORE UPDATE ON admin_users
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_refresh_tokens_updated_at ON refresh_tokens;
CREATE TRIGGER trg_refresh_tokens_updated_at
    BEFORE UPDATE ON refresh_tokens
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_app_users_updated_at ON app_users;
CREATE TRIGGER trg_app_users_updated_at
    BEFORE UPDATE ON app_users
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();


-- ---------------------------------------------------------------------
-- 컬럼 설명
-- ---------------------------------------------------------------------
COMMENT ON TABLE  admin_users                  IS '관리자 콘솔 계정 (id/pw 로그인)';

COMMENT ON COLUMN admin_users.id               IS '관리자 고유 ID';
COMMENT ON COLUMN admin_users.login_id         IS '로그인 아이디 (이메일 아님)';
COMMENT ON COLUMN admin_users.password_hash    IS 'Argon2id PHC 문자열 / 가역 암호화 금지';
COMMENT ON COLUMN admin_users.name             IS '관리자 이름 (사내 계정이라 평문)';
COMMENT ON COLUMN admin_users.role             IS
'권한 5단계 (D-014. 지금 발급하는 것은 ADMIN 뿐):
 ADMIN=전부 / OPERATOR=운영 CRUD+복호화 / CURATOR=지식베이스
 ANALYST=지표 조회 / VIEWER=조회(개인정보 마스킹)';
COMMENT ON COLUMN admin_users.status           IS '계정 상태 active/suspended (새 로그인 차단용)';
COMMENT ON COLUMN admin_users.last_login_at    IS '마지막 로그인 성공 시각';
COMMENT ON COLUMN admin_users.created_at       IS '생성 시각';
COMMENT ON COLUMN admin_users.updated_at       IS '수정 시각';

COMMENT ON TABLE  refresh_tokens               IS '재발급 토큰 (관리자 + 앱 회원) / 강제 로그아웃용';

COMMENT ON COLUMN refresh_tokens.id            IS '토큰 행 고유 ID';
COMMENT ON COLUMN refresh_tokens.admin_user_id IS '소유 관리자 (계정 삭제 시 CASCADE) / app_user_id 와 배타';
COMMENT ON COLUMN refresh_tokens.app_user_id   IS '소유 앱 회원 (계정 삭제 시 CASCADE) / admin_user_id 와 배타';
COMMENT ON COLUMN refresh_tokens.token_hash    IS '토큰 원문의 SHA-256 hex 64자 / 원문은 저장 안 함';
COMMENT ON COLUMN refresh_tokens.expires_at    IS '만료 시각';
COMMENT ON COLUMN refresh_tokens.revoked_at    IS '폐기 시각 / NULL 이면 살아 있는 세션. 재사용 감지에 씀';
COMMENT ON COLUMN refresh_tokens.user_agent    IS '발급 당시 User-Agent (표시용. 인증 판단에 쓰지 말 것)';
COMMENT ON COLUMN refresh_tokens.ip            IS '발급 당시 IP (표시용)';
COMMENT ON COLUMN refresh_tokens.created_at    IS '발급 시각';
COMMENT ON COLUMN refresh_tokens.updated_at    IS '수정 시각';

COMMENT ON TABLE  app_users                    IS '앱 회원 (카카오 소셜 로그인) / 개인정보는 앱단 AES-256-GCM';

COMMENT ON COLUMN app_users.id                 IS '회원 고유 ID';
COMMENT ON COLUMN app_users.kakao_id           IS '카카오 회원번호 / 평문 (로그인 조인 키)';
COMMENT ON COLUMN app_users.email_enc          IS '이메일 AES-256-GCM 암호문';
COMMENT ON COLUMN app_users.email_hash         IS '이메일 blind index / HMAC-SHA256 hex 64자. 검색은 이쪽으로';
COMMENT ON COLUMN app_users.phone_enc          IS '전화번호 AES-256-GCM 암호문 (검색 불가)';
COMMENT ON COLUMN app_users.name_enc           IS '이름 AES-256-GCM 암호문 (검색 불가)';
COMMENT ON COLUMN app_users.status             IS '회원 상태 active/suspended/withdrawn';
COMMENT ON COLUMN app_users.created_at         IS '가입 시각';
COMMENT ON COLUMN app_users.updated_at         IS '수정 시각';
