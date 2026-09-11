-- =====================================================================
-- 07_gait_records.sql
-- 보행 분석 기록 — backend 가 소유하는 record/job 원장 (D-043)
-- 실행 순서: 01_schema -> 02_trigger -> 03_auth -> 04_crawl_runs -> 05_pets
--            -> 06_walks -> 07_gait_records
-- =====================================================================
--
-- pets 를 FK 로 참조하므로 05_pets 뒤에 온다.
--
-- 지금까지 기록은 gait-analysis 컨테이너의 JSON 파일이었다 (gait-data 볼륨).
-- D-043 으로 원장이 여기로 온다 — 소유권 검증(pet_id → pets.app_user_id)과
-- 비동기 job 상태를 backend 가 관리해야 하기 때문이다. **기존 JSON 은 이관하지
-- 않는다** — 전부 테스트 데이터이고 dog_id="1" 같은 값은 이 FK 를 만족하지 못한다.
--
-- **영상 바이트는 DB 에 절대 넣지 않는다.** storage_key 는 클라우드 저장소(#78)의
-- 불투명 식별자다. provider 가 정해지지 않아 형식을 강제하지 않는다(text).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gait_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 강아지가 지워지면 기록도 같이 지운다. 소유권은 pet_id -> pets.app_user_id 로
    -- 유도한다 — owner_user_id 를 중복 저장하면 반려견 양도 같은 경우에 어긋난다.
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    -- ⚠ 소유권이 아니라 **업로드한 사람**이다 (Task 19, docs/co-care.md §2). 확정
    -- (confirm_upload)을 "업로더 본인 또는 대표"로 열기 위해 있다 — 대표만으로 좁히면
    -- start_analysis 를 구성원(대표∪돌보미)에게 연 것과 어긋나 돌보미가 티켓 발급·업로드는
    -- 되는데 confirm 에서만 404 를 받는 "반쯤 열린" 상태가 된다. **소유권 칸으로 오해해
    -- 단순화하지 말 것** — 소유는 여전히 pet_id -> pets.app_user_id 하나뿐이고, 이 칸은
    -- "그때 누가 올렸는지"만 남긴다. NULL 은 이 칸이 생기기 전의 옛 기록.
    actor_app_user_id UUID REFERENCES app_users(id) ON DELETE SET NULL,

    -- 파이프라인이 어디까지 갔나. quality_status 와 **다른 축**이다 —
    -- FAILED(워커가 죽음 → 재시도)와 unavailable(영상이 분석 부적합 → 재촬영)은
    -- 사용자 안내가 완전히 다르다 (D-033 이 ABSTAINED≠REFUSED 를 가른 것과 같은 이유).
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','UPLOADED','PROCESSING','DONE','FAILED')),

    -- 분석해 보니 쓸 만한가. DONE 일 때만 채워진다.
    quality_status VARCHAR(20)
        CHECK (quality_status IN ('ok','unavailable')),
    -- ⚠ 엔진이 내는 어휘와 **같아야 한다** — legacy(daengs_gait/quality_gate.py)·v4(gait_v4/quality.py)
    --   둘 다 good(>80) / ok(20~80) / low(<20) 세 단계다. 처음(D-043)에 good/low 둘만 적어서
    --   20~80 구간 영상의 DONE 커밋이 CheckViolation 으로 죽고 행이 PROCESSING 으로 남았다
    --   (2026-09-09). tests/test_gait_quality_tier_contract.py 가 두 엔진 소스와 이 CHECK 를 대조한다.
    quality_tier VARCHAR(10)
        CHECK (quality_tier IN ('good','ok','low')),

    -- 클라우드 저장소 키 (#78). 원본은 presign 발급 때, overlay 는 분석 뒤에 채워진다.
    original_storage_key TEXT,
    overlay_storage_key TEXT,

    -- 반정형 값. documents.metadata 처럼 표준 키는 주석·Pydantic 으로 관리하고
    -- DB 는 강제하지 않는다. 셋을 나누는 이유:
    --   quality                 — 판정 통계 (daengs_gait quality_gate 출력 그대로)
    --   summary_for_ui          — 화면에 내도 되는 요약
    --   internal_feature_vector — **비교 전용. API 응답이 절대 내보내면 안 된다.**
    --                             한 컬럼에 섞으면 실수로 나간다.
    quality JSONB,
    summary_for_ui JSONB,
    internal_feature_vector JSONB,

    -- 판정 로직 버전. 버전이 다른 두 기록을 비교하면 경고를 붙인다.
    gait_filter_version TEXT,
    -- 어떤 pose model / 관절 정의로 만든 기록인가 (D-063). **실행 엔진 선택이 아니라
    -- 메타데이터다** — 그 모델이 더는 안 돌아도 옛 기록이 그 모델로 만들어진 사실은 남는다.
    --   rtmpose_ap10k_ssd  v4 (AP-10K 17 관절)
    --   yolov8_12kp_best   legacy (12 관절)
    -- NULL = 판별 불가한 옛 기록, 또는 엔진 결과 없이 FAILED 로 끝난 기록. 값의 정본은
    -- daengs_gait/contract.py 의 POSE_MODELS 이고, CHECK 로 못 박지 않는다 — 모델이 바뀔
    -- 때마다 마이그레이션이 필요해지는 것을 피한다. 관절 이름이 다른 기록끼리는 비교하지
    -- 않는다 (2단계, services/gait.py).
    pose_model TEXT,

    -- 사용자가 준 촬영일. 없을 수 있다 (analyzed 시각과 다르다).
    captured_at DATE,
    video_meta JSONB,
    -- 사용자가 올린 원본 파일명 (표시용). 저장 키와 별개다.
    source_file TEXT,
    note TEXT,
    -- 워커 실패 사유 (status='FAILED' 일 때). 사용자 안내가 아니라 운영 진단용이다.
    failure_reason TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- soft delete. 파일 삭제(스토리지)가 비동기라, 행을 먼저 지우면 키를 잃어
    -- 파일이 영영 고아가 된다 — 지우기로 표시하고 정리는 따로 돈다.
    deleted_at TIMESTAMPTZ
);

-- 목록 조회가 "이 강아지의 기록, 시간순" 하나뿐이다 (설계문서 §4).
CREATE INDEX IF NOT EXISTS idx_gait_records_pet_created
    ON gait_records (pet_id, created_at);

DROP TRIGGER IF EXISTS trg_gait_records_updated_at ON gait_records;
CREATE TRIGGER trg_gait_records_updated_at
    BEFORE UPDATE ON gait_records
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
