"""Disposable PostgreSQL and real Windows cmd checks; no application configuration."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


# Minimal stand-ins for what each migration expects to already exist.
# `db/init/01_schema.sql` cannot be used here: it needs the `vector` extension.
WALKS = 'CREATE TABLE walks(id uuid PRIMARY KEY);'
# Only the columns the migration and its verifier touch. The three-value CHECK is
# deliberate: the migration must be the thing that widens it. Rows make the UPDATE real.
DOCUMENTS = (
    "CREATE TABLE documents("
    " id bigserial PRIMARY KEY,"
    " category varchar(50) NOT NULL CHECK (category IN ('policy','travel','food')),"
    " subcategory varchar(50) NOT NULL,"
    " metadata jsonb NOT NULL DEFAULT '{}'::jsonb);"
    "INSERT INTO documents(category, subcategory)"
    " VALUES ('policy','insurance'), ('policy','insurance'), ('policy','ordinance');"
)
# `db/init/03_auth.sql` 의 app_users 중 이 마이그레이션들이 닿는 부분만.
# gen_random_uuid() 는 pgcrypto/ pg13+ 내장이라 쓰지 않고 값을 직접 넣는다 — 확장에 안 기댄다.
APP_USERS = (
    "CREATE TABLE app_users("
    " id uuid PRIMARY KEY,"
    " kakao_id bigint NOT NULL UNIQUE);"
    "INSERT INTO app_users(id, kakao_id)"
    " VALUES ('11111111-1111-1111-1111-111111111111', 1),"
    "        ('22222222-2222-2222-2222-222222222222', 2);"
)
# `db/init/05_pets.sql` 의 pets 중 닿는 부분만. **app_users 를 앞세워야 FK 가 선다** —
# 그래서 둘로 나눠 두고, 필요한 조합을 항목에서 더한다.
#
# `birth_date` 는 `pet_farewell`(2026-09-02) 때문에 있다 — 그 마이그레이션의
# `pets_farewell_after_birth` CHECK 이 이 칸을 읽으므로, 없으면 ALTER 가 죽고 **verifier 가
# 잡은 것이 아니라 적용이 실패한 것**이 된다. 다른 항목에는 nullable 한 칸이 하나 느는 것뿐이다.
PETS_ONLY = (
    "CREATE TABLE pets("
    " id uuid PRIMARY KEY,"
    " app_user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,"
    " name varchar(40) NOT NULL,"
    " birth_date date);"
    "INSERT INTO pets(id, app_user_id, name)"
    " VALUES ('33333333-3333-3333-3333-333333333333',"
    "         '11111111-1111-1111-1111-111111111111', 'x');"
)
PETS = APP_USERS + PETS_ONLY
# `db/init/02_trigger.sql` 의 함수. dog_cards·screening_records 가 트리거로 건다.
SET_UPDATED_AT = (
    'CREATE FUNCTION set_updated_at() RETURNS TRIGGER AS $t$'
    ' BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $t$ LANGUAGE plpgsql;'
)
# answer_reports 의 FK 셋. chat_turns 는 chat_sessions 를 참조하지만 그 사슬까지는 안 세운다 —
# 이 마이그레이션이 보는 것은 chat_turns(id) 하나다.
CHAT_AND_ADMINS = APP_USERS + (
    "CREATE TABLE admin_users(id uuid PRIMARY KEY);"
    "CREATE TABLE chat_turns(id uuid PRIMARY KEY);"
    "INSERT INTO admin_users(id) VALUES ('44444444-4444-4444-4444-444444444444');"
    "INSERT INTO chat_turns(id) VALUES ('55555555-5555-5555-5555-555555555555');"
)
# org 백필은 **값**을 검사하므로 픽스처에도 값이 있어야 한다. 실제 doc_id 두 개를
# 골라 넣는다 — 마이그레이션의 VALUES 목록에 있는 것이라야 UPDATE 가 실제로 돈다.
DOCUMENTS_WITH_ORG = (
    "CREATE TABLE documents("
    " id bigserial PRIMARY KEY,"
    " metadata jsonb NOT NULL DEFAULT '{}'::jsonb);"
    "INSERT INTO documents(metadata) VALUES"
    " ('{\"source_id\":\"ordinance-search\",\"doc_id\":\"ordinance-search-2182010__20260829\"}'),"
    " ('{\"source_id\":\"benefit24-services\","
    "\"doc_id\":\"benefit24-services-305000000130__20260829\"}'),"
    " ('{\"source_id\":\"law-drf-api\",\"doc_id\":\"law-drf-api-veterinarian-act__20260827\"}');"
)


# `db/init/03_auth.sql` 의 refresh_tokens 중 2026-08-26 이 닿는 부분만.
# **`admin_user_id` 가 NOT NULL 이고 인덱스가 전체(부분 아님)인 것이 옛 모양이고**, 그
# 마이그레이션이 고치러 오는 대상이다. 행 하나를 넣어 NOT NULL 변조가 실제로 돌게 한다.
REFRESH_TOKENS_OLD = APP_USERS + (
    "CREATE TABLE admin_users(id uuid PRIMARY KEY);"
    "INSERT INTO admin_users(id) VALUES ('44444444-4444-4444-4444-444444444444');"
    "CREATE TABLE refresh_tokens("
    " id uuid PRIMARY KEY,"
    " admin_user_id uuid NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,"
    " token_hash char(64) NOT NULL UNIQUE);"
    "CREATE INDEX idx_refresh_tokens_admin ON refresh_tokens (admin_user_id);"
    "INSERT INTO refresh_tokens(id, admin_user_id, token_hash)"
    " VALUES ('66666666-6666-6666-6666-666666666666',"
    "         '44444444-4444-4444-4444-444444444444', repeat('a', 64));"
)
# 2026-08-30 은 ALTER 만 하므로 **옛 두 값짜리 CHECK 를 가진 표**가 있어야 넓히는 일이 실제로
# 일어난다. 행은 'due' 라 옛 제약을 만족한다 — 그래야 변조에서 되돌릴 때 ALTER 가 안 죽는다.
CRAWL_RUNS_OLD = (
    "CREATE TABLE crawl_runs("
    " id bigserial PRIMARY KEY,"
    " run_id text,"
    " source_id text NOT NULL,"
    " trigger text NOT NULL"
    "   CONSTRAINT crawl_runs_trigger_check CHECK (trigger IN ('due','manual')),"
    " status text NOT NULL,"
    " started_at timestamptz NOT NULL DEFAULT now());"
    "INSERT INTO crawl_runs(source_id, trigger, status)"
    " VALUES ('ordinance-search','due','ok');"
)
# `training_rag_*` 는 pgvector 를 요구한다. CI 서비스가 `pgvector/pgvector:pg17` 인 이유다
# (`.github/workflows/migration-verification-tests.yml`). 일회용 스키마 안에 만들고 ROLLBACK
# 으로 같이 사라진다 — `format_type` 이 `vector(768)` 로 (스키마 없이) 보이려면 확장이
# search_path 안에 있어야 하므로 `WITH SCHEMA` 를 주지 않는다.
VECTOR_EXTENSION = 'CREATE EXTENSION IF NOT EXISTS vector;'
# HNSW 장(2026-09-09)은 **벡터 칸이 있어야** 마이그레이션 자체가 돈다. 위 `DOCUMENTS` 는
# 일부러 확장에 안 기대는 픽스처라 그 칸이 없어서, 이 한 장만 따로 세운다.
# 행은 안 넣는다 — 이 장이 만드는 것은 인덱스이고 빈 표에도 선다.
DOCUMENTS_WITH_EMBEDDING = VECTOR_EXTENSION + (
    "CREATE TABLE documents("
    " id bigserial PRIMARY KEY,"
    " embedding vector(1024));"
)

# 훈련 RAG 청크 표의 **옛** 모양 — `chunk_id` 가 PK 이던 시절이다. 2026-09-08 마이그레이션이
# 그 PK 를 복합키로 옮긴다. 픽스처가 옛 모양이어야 마이그레이션이 실제로 할 일이 생긴다.
TRAINING_RAG_OLD = VECTOR_EXTENSION + (
    "CREATE TABLE training_rag_documents("
    " document_id text PRIMARY KEY,"
    " source_id text NOT NULL,"
    " source_url text,"
    " content_sha256 text NOT NULL,"
    " metadata jsonb NOT NULL DEFAULT '{}'::jsonb,"
    " created_at timestamptz NOT NULL DEFAULT now());"
    "CREATE TABLE training_rag_chunks("
    " chunk_id text PRIMARY KEY,"
    " document_id text NOT NULL REFERENCES training_rag_documents(document_id) ON DELETE CASCADE,"
    " chunk_index integer NOT NULL,"
    " text text NOT NULL,"
    " token_count integer NOT NULL,"
    " metadata jsonb NOT NULL DEFAULT '{}'::jsonb,"
    " embedding_model text NOT NULL,"
    " embedding vector(768) NOT NULL,"
    " content_sha256 text NOT NULL,"
    " created_at timestamptz NOT NULL DEFAULT now(),"
    " UNIQUE(document_id, chunk_index, embedding_model));"
)


# 회원의 상태 칸까지 필요한 항목용. `activity_game` 의 트리거가 `AFTER UPDATE OF status
# ON app_users` 라 그 칸이 없으면 마이그레이션 자체가 안 붙는다.
APP_USERS_WITH_STATUS = (
    "CREATE TABLE app_users("
    " id uuid PRIMARY KEY,"
    " kakao_id bigint NOT NULL UNIQUE,"
    " status varchar(20) NOT NULL DEFAULT 'active');"
    "INSERT INTO app_users(id, kakao_id)"
    " VALUES ('11111111-1111-1111-1111-111111111111', 1),"
    "        ('22222222-2222-2222-2222-222222222222', 2);"
)


def prerequisites(*stems):
    """앞선 마이그레이션들을 **그대로** 픽스처로 쓴다.

    표가 열 개씩 얽힌 항목(`territory_claims` · `activity_game`)에서 스텁을 손으로 쓰면
    **스텁과 실물이 갈라지는 순간 검사가 거짓말을 한다** — 실물에 걸린 제약이 스텁에는
    없으니, verify 가 통과해도 그것이 무엇을 뜻하는지 알 수 없다. 앞 장의 SQL 을 그대로
    부으면 그 어긋남이 원천적으로 안 생긴다.

    스텁을 아예 안 쓰는 것은 아니다 — `app_users`·`pets` 처럼 `db/init/` 이 원본인 표는
    여전히 위의 상수들이 대신한다. 여기서 부르는 것은 `db/migrations/` 안의 것뿐이다.
    """
    return '\n'.join(
        transactionless((ROOT / f'db/migrations/{stem}.sql').read_text(encoding='utf-8'))
        for stem in stems)


def transactionless(sql):
    """Strip the migration's own BEGIN;/COMMIT; so the harness can supply the transaction.

    적용 경로(`db-migrate.yml`, `runbook.md`)는 `psql -X -v ON_ERROR_STOP=1` 이고
    **`--single-transaction` 이 없다.** 그래서 원자성이 필요한 마이그레이션은 자기 `BEGIN;`/
    `COMMIT;` 을 들고 있어야 한다 — `documents_org_backfill` 과 `documents_category_insurance`
    가 그렇다.

    그런데 이 하네스는 **일회용 스키마 + ROLLBACK** 으로 격리한다. 안쪽 `COMMIT;` 이 그
    바깥 트랜잭션을 커밋해 버리면 `SET LOCAL search_path` 가 날아가고, 그 뒤 문장들이
    `public` 을 보게 되어 **아무 문제가 없어도 verifier 가 'missing table' 을 낸다.**
    격리도 깨져 `verify_test_*` 스키마가 남는다.

    그래서 여기서만 벗긴다. 벗기는 것은 **단독 줄로 선 BEGIN;/COMMIT;** 뿐이라, 문장 안에
    그 낱말이 들어 있는 경우는 안 건드린다.
    """
    keep = [line for line in sql.splitlines()
            if line.strip().upper() not in ('BEGIN;', 'COMMIT;')]
    return unqualified('\n'.join(keep) + '\n')


def unqualified(sql):
    """`public.` 한정을 뗀다. **`transactionless()` 와 같은 종류의 손질**이다.

    두 파일이 표를 `public.walks` · `public.walk_points` 로 **스키마까지 적어** 가리킨다
    (`2026-09-02_walk_analyses` · `2026-09-02_walk_point_chunks`, 각 1건). 실제 적용 경로에서는
    그것이 맞는데 — 서버 DB 의 표는 `public` 에 있다 — **이 하네스는 일회용 스키마 안에서**
    돌므로 그 이름이 진짜 `public` 을 가리켜 "없는 표"가 된다.

    마이그레이션 SQL 을 고치는 대신 여기서 벗긴다 (#273 이 세운 원칙이고 `transactionless()`
    가 같은 이유로 있다). 두 파일 다 `public.` 이 그 한 자리에만 나온다.
    """
    return sql.replace('public.', '')


# pose_model 백필(2026-09-09, D-063)은 **값**을 검사하므로 픽스처에 행이 있어야 한다.
# 여섯 행이 백필 규칙의 가지 하나씩이다 — v4 관절 / legacy 관절 / 빈 객체 / 두 체계가 섞임 /
# 어느 집합에도 없는 키 / summary 자체가 NULL. 앞 넷은 DONE, 뒤 둘은 각각 DONE(unavailable)·FAILED.
# 마이그레이션 **전** 상태라 pose_model 컬럼이 없다 — 컬럼은 마이그레이션이 만든다.
GAIT_RECORDS_POSE_MODEL_ROWS = (
    "INSERT INTO gait_records(id, pet_id, status, quality_status, quality_tier, summary_for_ui) VALUES"
    " ('a0000000-0000-0000-0000-000000000001', '33333333-3333-3333-3333-333333333333',"
    "  'DONE', 'ok', 'good', '{\"L_Hip\": {\"x_range\": 0.5}, \"R_Knee\": {\"x_range\": 0.4}}'),"
    " ('a0000000-0000-0000-0000-000000000002', '33333333-3333-3333-3333-333333333333',"
    "  'DONE', 'ok', 'low', '{\"Iliac crest\": {\"x_range\": 0.5}, \"Femorotibial joint\": {\"x_range\": 0.4}}'),"
    " ('a0000000-0000-0000-0000-000000000003', '33333333-3333-3333-3333-333333333333',"
    "  'DONE', 'unavailable', NULL, '{}'),"
    " ('a0000000-0000-0000-0000-000000000004', '33333333-3333-3333-3333-333333333333',"
    "  'DONE', 'ok', 'good', '{\"L_Hip\": {\"x_range\": 0.5}, \"Iliac crest\": {\"x_range\": 0.4}}'),"
    " ('a0000000-0000-0000-0000-000000000005', '33333333-3333-3333-3333-333333333333',"
    "  'DONE', 'ok', 'good', '{\"L_Hip\": {\"x_range\": 0.5}, \"Tail_tip\": {\"x_range\": 0.4}}'),"
    " ('a0000000-0000-0000-0000-000000000006', '33333333-3333-3333-3333-333333333333',"
    "  'FAILED', NULL, NULL, NULL);"
)


# 항목은 (날짜, 이름, 픽스처, 테이블, 변조들[, 2회 적용할까]).
# 마지막 칸은 거의 언제나 True 다 — **멱등은 이 저장소가 마이그레이션에 요구하는 성질**이라
# (CLAUDE.md: 버전 테이블이 없으니 여러 번 돌려도 안전하게) 기본으로 두 번 적용해 본다.
# False 로 두는 자리는 `documents_org_backfill` 하나뿐이고 이유는 그 항목에 적었다.
#
# **모듈 수준에 둔다** — `coverage_checks()` 가 "등록됐나"를 이 목록에서 읽는다. 함수 안에
# 있으면 그 검사가 소스를 정규식으로 긁어야 하고, 그러면 목록을 고칠 때마다 정규식이 낡는다.
CHECKS = (
        ('2026-09-09', 'documents_hnsw', DOCUMENTS_WITH_EMBEDDING, 'documents', [
            # ⓐ 인덱스가 아예 없다 — 전수 스캔으로 돌아간다. **결과는 맞고 느리기만 하다.**
            'DROP INDEX idx_documents_embedding',
            # ⓑ 접근 방식을 바꾸는 변조. 이름도 같고 인덱스도 있는데 recall 특성이 다르다 —
            # `D16` 이 재려는 것이 바로 그 특성이라, 이름만 보는 검사로는 아무 의미가 없다.
            'DROP INDEX idx_documents_embedding;'
            ' CREATE INDEX idx_documents_embedding ON documents'
            ' USING ivfflat (embedding vector_cosine_ops)',
            # ⓒ 연산자 클래스를 바꾸는 변조. 검색은 `<=>`(코사인)로 묻는데 이 인덱스는
            # `<->` 용이라 **질의가 인덱스를 안 탄다.** 결과는 여전히 맞아서 아무도 안 알려준다 —
            # `db/indexes.sql` 이 2026-08 부터 같은 경고를 달고 있던 자리다.
            'DROP INDEX idx_documents_embedding;'
            ' CREATE INDEX idx_documents_embedding ON documents'
            ' USING hnsw (embedding vector_l2_ops)',
        ]),
        ('2026-09-09', 'walk_photo_manifests', WALKS, 'walk_photo_manifests', [
            'ALTER TABLE walk_photo_manifests DROP COLUMN publisher_id',
            'ALTER TABLE walk_photo_manifests DROP CONSTRAINT walk_photo_manifests_pkey',
            'ALTER TABLE walk_photo_manifests DROP CONSTRAINT walk_photo_manifests_walk_id_fkey',
            'ALTER TABLE walk_photo_manifests DROP CONSTRAINT walk_photo_revision_positive',
            'ALTER TABLE walk_photo_manifests DROP CONSTRAINT walk_photo_hash_valid',
            'ALTER TABLE walk_photo_manifests DROP CONSTRAINT walk_photo_records_bounded',
        ]),
        ('2026-09-09', 'walk_entry_pins',
         WALKS + (ROOT / 'db/init/19_walk_entries.sql').read_text(encoding='utf-8'),
         'walk_entry_pins', [
            'DROP TRIGGER walk_entry_pins_deleted ON walk_entries',
            'ALTER TABLE walk_entries DISABLE TRIGGER walk_entry_pins_deleted',
            'DROP TRIGGER walk_entry_pin_live ON walk_entry_pins',
            'DROP TRIGGER walk_entry_mutation_live ON walk_entry_mutations',
            'ALTER TABLE walk_entry_pins DROP COLUMN payload',
            'ALTER TABLE walk_entry_mutations DROP CONSTRAINT walk_entry_mutations_pkey',
            'ALTER TABLE walk_entry_pins DROP CONSTRAINT walk_entry_pins_walk_id_entry_id_fkey',
            'ALTER TABLE walk_entry_mutations DROP CONSTRAINT walk_entry_mutations_walk_id_entry_id_fkey',
         ]),
        ('2026-09-10', 'walk_context_recollection',
         WALKS + (ROOT / 'db/init/19_walk_entries.sql').read_text(encoding='utf-8')
         + prerequisites('2026-09-08_walk_entry_contexts'),
         'walk_entry_context_jobs', [
            'ALTER TABLE walk_entry_context_jobs DROP COLUMN collection_round CASCADE',
            'ALTER TABLE walk_entry_context_envelopes DROP COLUMN collection_round CASCADE',
            'ALTER TABLE walk_entry_context_jobs DROP COLUMN backfill_policy',
            'ALTER TABLE walk_entry_context_jobs ALTER COLUMN collection_round DROP DEFAULT',
            'ALTER TABLE walk_entry_context_envelopes ALTER COLUMN collection_round DROP DEFAULT',
            'ALTER TABLE walk_entry_context_envelopes ALTER COLUMN collection_round DROP NOT NULL',
            'ALTER TABLE walk_entry_context_jobs DROP CONSTRAINT walk_entry_context_jobs_collection_round_check',
            'ALTER TABLE walk_entry_context_envelopes DROP CONSTRAINT walk_entry_context_envelopes_round_attempt_key',
            'ALTER TABLE walk_entry_context_envelopes ADD UNIQUE (job_id, attempt)',
         ]),
        ('2026-09-09', 'walk_public_context_commerce',
         WALKS + (ROOT / 'db/init/19_walk_entries.sql').read_text(encoding='utf-8')
         + prerequisites('2026-09-08_walk_entry_contexts', '2026-09-09_walk_public_context'),
         'walk_entry_context_jobs', [
            'ALTER TABLE walk_entry_context_jobs DROP CONSTRAINT walk_entry_context_jobs_tag_check',
            ('ALTER TABLE walk_entry_context_jobs DROP CONSTRAINT walk_entry_context_jobs_tag_check;'
             " ALTER TABLE walk_entry_context_jobs ADD CONSTRAINT walk_entry_context_jobs_tag_check"
             " CHECK (tag IN ('space.facility','space.park','space.river','environment.weather','space.address'))"),
         ]),
        ('2026-09-09', 'walk_public_context',
         WALKS + (ROOT / 'db/init/19_walk_entries.sql').read_text(encoding='utf-8')
         + prerequisites('2026-09-08_walk_entry_contexts'),
         'walk_entry_context_jobs', [
            'ALTER TABLE walk_entry_context_jobs DROP CONSTRAINT walk_entry_context_jobs_tag_check',
            ('ALTER TABLE walk_entry_context_jobs DROP CONSTRAINT walk_entry_context_jobs_tag_check;'
             " ALTER TABLE walk_entry_context_jobs ADD CONSTRAINT walk_entry_context_jobs_tag_check"
             " CHECK (tag IN ('space.facility','space.park','space.river','environment.weather'))"),
         ]),
        ('2026-09-08', 'walk_entry_contexts',
         WALKS + (ROOT / 'db/init/19_walk_entries.sql').read_text(encoding='utf-8'),
         'walk_entry_context_jobs', [
            'DROP TRIGGER walk_entry_contexts_deleted ON walk_entries',
            'ALTER TABLE walk_entries DISABLE TRIGGER walk_entry_contexts_deleted',
            'ALTER TABLE walk_entry_context_jobs DROP CONSTRAINT walk_entry_context_jobs_walk_id_entry_id_fkey',
            'ALTER TABLE walk_entry_context_envelopes DROP CONSTRAINT walk_entry_context_envelopes_job_id_fkey',
         ]),
        # gait_records.quality_tier CHECK 를 good/low → good/ok/low 로. 픽스처는 **9/2 의 옛 표**
        # 그대로(prerequisites 로 그 마이그레이션 텍스트를 재사용) — 그래야 이 마이그레이션이
        # 실제로 하는 일(옛 제약을 떼고 새 제약을 거는 것)을 그대로 밟는다.
        ('2026-09-09', 'gait_quality_tier_ok',
         APP_USERS + PETS_ONLY + SET_UPDATED_AT + prerequisites('2026-09-02_gait_records'),
         'gait_records', [
            # 제약을 통째로 잃는 변조.
            'ALTER TABLE gait_records DROP CONSTRAINT gait_records_quality_tier_check',
            # **사고 이전으로 되돌리는 변조 — 이 항목의 이유다.** 옛 verify(9/2)는 good·low
            # "포함" 검사라 이걸 못 잡는다. 20~80 구간 영상이 다시 PROCESSING 좀비가 된다.
            'ALTER TABLE gait_records DROP CONSTRAINT gait_records_quality_tier_check;'
            " ALTER TABLE gait_records ADD CONSTRAINT gait_records_quality_tier_check"
            " CHECK (quality_tier IN ('good','low'))",
            # 이름만 같고 값이 하나 빠진 변조(low 를 잃음).
            'ALTER TABLE gait_records DROP CONSTRAINT gait_records_quality_tier_check;'
            " ALTER TABLE gait_records ADD CONSTRAINT gait_records_quality_tier_check"
            " CHECK (quality_tier IN ('good','ok'))",
            # 검증 안 된(NOT VALID) 제약은 "있어도 없는 것" — convalidated 를 본다.
            'ALTER TABLE gait_records DROP CONSTRAINT gait_records_quality_tier_check;'
            " ALTER TABLE gait_records ADD CONSTRAINT gait_records_quality_tier_check"
            " CHECK (quality_tier IN ('good','ok','low')) NOT VALID",
        ]),
        ('2026-09-08', 'certified_territory', APP_USERS + PETS_ONLY
         + prerequisites('2026-09-03_territory_visits', '2026-09-05_territory_claims'),
         'territory_challenges', [
            'ALTER TABLE territory_occupancies DROP COLUMN certified_at',
            'ALTER TABLE territory_challenges DROP COLUMN completed_at',
            'ALTER TABLE territory_challenges ALTER COLUMN expected_site_version TYPE integer',
            'ALTER TABLE territory_challenges DROP CONSTRAINT territory_challenges_photo_id_key',
            'ALTER TABLE territory_challenges DROP CONSTRAINT territory_challenges_claim_id_fkey',
            'ALTER TABLE territory_challenges DROP CONSTRAINT territory_challenges_photo_id_fkey',
            'DROP INDEX ix_territory_challenges_claim_id',
        ]),
        ('2026-09-05', 'walk_entries', WALKS, 'walk_entries', [
            'ALTER TABLE walk_entries DROP COLUMN payload',
            'ALTER TABLE walk_entries ALTER COLUMN revision TYPE bigint',
            'ALTER TABLE walk_entries ALTER COLUMN mutation_id DROP NOT NULL',
            'ALTER TABLE walk_entries DROP CONSTRAINT walk_entries_pkey',
            'ALTER TABLE walk_entries DROP CONSTRAINT walk_entries_walk_id_fkey',
            'ALTER TABLE walk_entries DROP CONSTRAINT walk_entries_revision_check',
            'ALTER TABLE walk_entries DROP CONSTRAINT walk_entries_walk_id_fkey; '
            'ALTER TABLE walk_entries ADD FOREIGN KEY(walk_id) REFERENCES walks(id)',
        ]),
        ('2026-09-05', 'walk_storyboards', WALKS, 'walk_storyboards', [
            'ALTER TABLE walk_storyboards DROP COLUMN bundle',
            'ALTER TABLE walk_storyboards ALTER COLUMN input_revision TYPE varchar(80)',
            'ALTER TABLE walk_storyboards ALTER COLUMN updated_at DROP NOT NULL',
            'ALTER TABLE walk_storyboards DROP CONSTRAINT walk_storyboards_pkey',
            'ALTER TABLE walk_storyboards DROP CONSTRAINT walk_storyboards_walk_id_fkey',
            'ALTER TABLE walk_storyboards DROP CONSTRAINT walk_storyboards_status_check',
            'ALTER TABLE walk_storyboards DROP CONSTRAINT walk_storyboards_generation_check',
        ]),
        # 2026-09-06 (RAG-067 / #271) — insurance 를 policy 에서 가른다.
        # **망가뜨리는 방식이 walk_* 와 다르다.** 저쪽은 모양(컬럼·제약)만 깨는데, 이 마이그레이션은
        # 하는 일의 절반이 UPDATE 라 **값**도 깨야 한다. 아래 넷 중 뒤의 둘이 그것이다.
        ('2026-09-06', 'documents_category_insurance', DOCUMENTS, 'documents', [
            'ALTER TABLE documents DROP CONSTRAINT documents_category_check',
            # 옛 세 값으로 되돌린다. **NOT VALID 여야 한다** — 이미 insurance 행이 있어서
            # 검증하려 들면 CHECK 위반으로 ALTER 자체가 죽고, 그러면 verifier 가 잡은 것이
            # 아니라 ALTER 가 실패한 것이 된다 (하네스는 그 둘을 stderr 낱말로 가른다).
            'ALTER TABLE documents DROP CONSTRAINT documents_category_check;'
            " ALTER TABLE documents ADD CONSTRAINT documents_category_check"
            " CHECK (category IN ('policy','travel','food')) NOT VALID",
            # 값을 옛것으로 되돌린다 — `rag load` 가 옛 청크로 덮어쓴 상태와 같은 모양이다.
            # RAG-066 ① 에서 `org` 이 실제로 이렇게 지워졌고 **아무 에러도 안 났다.**
            "UPDATE documents SET category = 'policy' WHERE subcategory = 'insurance'",
            # 엉뚱한 행을 옮긴다 — 조건을 잘못 쓰면 이쪽으로 샌다
            "UPDATE documents SET category = 'insurance' WHERE subcategory = 'ordinance'",
        ]),
        # 2026-09-06 (#273) — 밀린 verify 를 단언형으로 바꾸며 목록에 넣는다.
        # 이 여섯은 SELECT 나열이라 **틀려도 종료 코드 0 이었다**: db-migrate.yml 이
        # `psql … < verify_%MIG_FILE% || exit 1` 로 판정하는데 SELECT 는 실패하지 않는다.
        ('2026-09-05', 'app_user_nickname', APP_USERS, 'app_users', [
            'ALTER TABLE app_users DROP COLUMN nickname',
            'ALTER TABLE app_users ALTER COLUMN nickname TYPE varchar(80)',
            # ⚠ **값을 먼저 채워야 한다.** 그냥 SET NOT NULL 하면 기존 NULL 행 때문에
            #   ALTER 자체가 죽고, 그러면 **verifier 가 잡은 것이 아니라 ALTER 가 실패한 것**이
            #   된다 (하네스는 그 둘을 stderr 의 mismatch/missing table 낱말로 가른다).
            #   #271 의 `NOT VALID` 와 같은 함정이다. lower(nickname) 이 UNIQUE 라 값도 달라야 한다.
            "UPDATE app_users SET nickname = 'n' || kakao_id;"
            ' ALTER TABLE app_users ALTER COLUMN nickname SET NOT NULL',
            'DROP INDEX idx_app_users_nickname',
            # **표현식을 잃는 변조.** 이름은 같은데 lower() 가 없다 — 이러면
            # 'Neo' 와 'neo' 가 둘 다 생긴다. 이름만 보는 verify 는 이걸 못 잡는다.
            'DROP INDEX idx_app_users_nickname;'
            ' CREATE UNIQUE INDEX idx_app_users_nickname ON app_users (nickname)',
            # 유일성을 잃는 변조. 표현식은 맞는데 UNIQUE 가 아니다.
            'DROP INDEX idx_app_users_nickname;'
            ' CREATE INDEX idx_app_users_nickname ON app_users (lower(nickname))',
        ]),
        # 2026-09-07 (#288) — 동물등록 여부 한 칸. **변조 넷 중 마지막이 이 항목의 이유다.**
        # DEFAULT 를 거는 것은 타입도 널 허용도 안 건드리므로 컬럼 모양만 보는 verify 는
        # 통과시킨다. 그런데 그 순간 "안 물어봤다"가 전부 "안 했다"가 된다.
        # 2026-09-08 (#332) — 케어 이벤트(밥·약·간식) 새 표. **멱등키 UNIQUE 를 지우는 변조가
        # 이 항목의 추가 이유다** — 빠져도 아무 에러가 안 나고 앱의 재시도가 두 줄이 된다.
        # kind CHECK 를 지우는 변조도 같은 결이다 ('walk' 가 들어오면 walks 와 두 곳이 된다).
        ('2026-09-08', 'care_events', PETS, 'care_events', [
            'ALTER TABLE care_events DROP COLUMN client_event_id',
            'ALTER TABLE care_events ALTER COLUMN kind TYPE text',
            'ALTER TABLE care_events ALTER COLUMN occurred_at DROP NOT NULL',
            'ALTER TABLE care_events DROP CONSTRAINT care_events_client_event_unique',
            'ALTER TABLE care_events DROP CONSTRAINT care_events_kind_check',
            'ALTER TABLE care_events DROP CONSTRAINT care_events_note_not_blank',
            'ALTER TABLE care_events DROP CONSTRAINT care_events_pet_id_fkey',
            'ALTER TABLE care_events DROP CONSTRAINT care_events_pet_id_fkey; '
            'ALTER TABLE care_events ADD FOREIGN KEY(pet_id) REFERENCES pets(id)',
            'DROP INDEX idx_care_events_pet_occurred',
        ]),
        # 2026-09-09 (#353) — 진료비 기록 둘. **변조 목록의 마지막 하나가 이 항목의 이유다.**
        # 이 표에서 지켜야 하는 것은 칸의 모양이 아니라 **reason_code 가 닫힌 목록이라는 사실**
        # 이다. 목록을 통째로 permissive 한 CHECK 으로 갈아 끼우면 이름은 그대로라 ④ 는
        # 통과하는데, 그때부터 사유가 자유 텍스트가 되어 사유별 누계가 조용히 쪼개진다 —
        # 그것을 잡는 것이 verify ⑥ 이고, 그 그물이 실제로 걸리는지 여기서 잰다.
        #
        # 빈 표에 거는 변조라 CHECK 재검증이 죽지 않는다 (#271 의 NOT VALID 함정이 없다).
        ('2026-09-09', 'vet_visits', PETS, 'vet_visits', [
            'ALTER TABLE vet_visits DROP COLUMN raw_ocr_items',
            'ALTER TABLE vet_visits ALTER COLUMN reason_code TYPE text',
            # NOT NULL 을 잃는 변조. 유저가 확정 안 한 행이 vet_visits 에 앉을 수 있게 된다.
            'ALTER TABLE vet_visits ALTER COLUMN reason_code DROP NOT NULL',
            'ALTER TABLE vet_visits DROP CONSTRAINT vet_visits_client_event_unique',
            'ALTER TABLE vet_visits DROP CONSTRAINT vet_visits_reason_code_check',
            'ALTER TABLE vet_visits DROP CONSTRAINT vet_visits_total_krw_range',
            'ALTER TABLE vet_visits DROP CONSTRAINT vet_visits_hospital_phone_shape',
            'ALTER TABLE vet_visits DROP CONSTRAINT vet_visits_pet_id_fkey; '
            'ALTER TABLE vet_visits ADD FOREIGN KEY(pet_id) REFERENCES pets(id)',
            'DROP INDEX idx_vet_visits_pet_visited',
            'ALTER TABLE vet_visit_drafts DROP COLUMN extracted',
            'ALTER TABLE vet_visit_drafts ALTER COLUMN receipt_image_key DROP NOT NULL',
            'DROP INDEX idx_vet_visit_drafts_created_at',
            # **초안의 멱등키가 빠지는 변조.** 아무 에러도 안 나고 Gemini 요금만 는다.
            'ALTER TABLE vet_visit_drafts DROP CONSTRAINT'
            ' vet_visit_drafts_client_event_unique',
            'ALTER TABLE vet_visit_drafts DROP COLUMN receipt_sha256',
            'ALTER TABLE vet_visit_drafts DROP CONSTRAINT'
            ' vet_visit_drafts_receipt_sha256_shape',
            'DROP INDEX idx_vet_visit_drafts_user_sha',
            'ALTER TABLE vet_visits DROP COLUMN is_emergency',
            'ALTER TABLE vet_visits DROP COLUMN is_oncology',
            'ALTER TABLE vet_visits ALTER COLUMN is_emergency DROP NOT NULL',
            # 기본값이 뒤집히는 변조. 아무 에러 없이 **모든 방문이 종양 진료로** 쌓인다.
            'ALTER TABLE vet_visits ALTER COLUMN is_oncology SET DEFAULT true',
            # **닫힌 목록이 열리는 변조.** 이름은 살아 있으므로 ④ 로는 안 잡힌다.
            'ALTER TABLE vet_visits DROP CONSTRAINT vet_visits_reason_code_check;'
            ' ALTER TABLE vet_visits ADD CONSTRAINT vet_visits_reason_code_check'
            ' CHECK (length(reason_code) > 0)',
            # **축이 둘로 돌아가는 변조 — ⑦ 만 잡는다.** 목록은 여전히 닫혀 있고
            # 'cardiac'·'other' 도 살아 있어 ④ 도 ⑥ 도 통과한다. 그런데 'tumor' 가 돌아온
            # 순간 피부 종괴가 skin 이자 tumor 라, 지키려던 사유별 누계가 조용히 갈린다.
            'ALTER TABLE vet_visits DROP CONSTRAINT vet_visits_reason_code_check;'
            ' ALTER TABLE vet_visits ADD CONSTRAINT vet_visits_reason_code_check'
            " CHECK (reason_code IN ('skin','cardiac','other','tumor'))",
        ]),
        ('2026-09-07', 'pets_registered', PETS, 'pets', [
            'ALTER TABLE pets DROP COLUMN registered',
            'ALTER TABLE pets ALTER COLUMN registered TYPE text USING registered::text',
            # ⚠ 값을 먼저 채워야 ALTER 가 안 죽는다 — #271 의 NOT VALID · #273 의 nickname 과
            #   같은 함정이다. 하네스는 "verifier 가 잡았다"와 "ALTER 가 실패했다"를 stderr
            #   낱말로 가르므로, ALTER 가 죽으면 이 항목은 무엇도 증명하지 않는다.
            'UPDATE pets SET registered = true;'
            ' ALTER TABLE pets ALTER COLUMN registered SET NOT NULL',
            'ALTER TABLE pets ALTER COLUMN registered SET DEFAULT false',
        ]),
        # 2026-09-08 (#331) — 돌봄 칸 넷. 기본값 변조는 `pets_registered` 와 같은 이유이고,
        # **제약을 지우는 변조가 이 항목의 추가 이유다** — 제약이 빠져도 칸 모양은 그대로라
        # 칸만 보는 verify 는 통과하는데, 그 뒤로 자율급식에 시각이 붙은 행이 조용히 쌓인다.
        # ⚠ 타입 변조는 `feeding_style` 에 건다. `feeding_times` 를 text 로 바꾸면 그 위의
        #   `jsonb_typeof(feeding_times)` CHECK 가 재검증에서 죽어 **변조 자체가 실패**하고,
        #   하네스는 "verifier 가 잡았다"와 "ALTER 가 실패했다"를 stderr 낱말로 가르므로
        #   그 항목은 아무것도 증명하지 않는다 (2026-09-08 CI 실측).
        ('2026-09-08', 'pets_care', PETS, 'pets', [
            'ALTER TABLE pets DROP COLUMN feeding_times',
            'ALTER TABLE pets ALTER COLUMN feeding_style TYPE text',
            'ALTER TABLE pets ALTER COLUMN feeding_style SET DEFAULT \'free\'',
            'ALTER TABLE pets DROP CONSTRAINT pets_feeding_times_need_schedule',
        ]),
        # 2026-09-07 (#297) — 요청 메타데이터. **변조 목록의 마지막 둘이 이 항목의 이유다.**
        # 이 표에서 지켜야 하는 것은 "있어야 할 열이 있나" 만이 아니라 **"없어야 할 열이
        # 없나" 이다** (D-037 은 질문 원문을, D-054 는 회원 식별자를 금지했다). 열을 하나
        # 더하는 변조는 스키마를 안 깨고 테스트도 안 터뜨린다 — verify 가 이름으로 막는
        # 것이 유일한 그물이라, 그 그물이 실제로 걸리는지 여기서 잰다.
        #
        # 픽스처가 없다(''). 이 표는 아무것도 참조하지 않으므로 선행 테이블이 필요 없고,
        # 그 성질 자체를 verify ④ 가 단언한다.
        # 2026-09-09 (#353) — OCR 학습 이용 동의 두 칸. **마지막 변조가 이 항목의 이유다.**
        # 다른 변조는 스키마를 깨서 코드가 시끄럽게 죽지만, DEFAULT NOW() 한 줄은 아무것도
        # 안 깨뜨리면서 **아무도 누른 적 없는 동의를 전 회원에게 만든다.** 그 상태로 학습셋을
        # 뽑으면 근거 없이 모은 데이터가 되고, 그때는 되돌릴 수 없다.
        ('2026-09-09', 'ocr_consent', APP_USERS, 'app_users', [
            'ALTER TABLE app_users DROP COLUMN ocr_consent_at',
            'ALTER TABLE app_users DROP COLUMN ocr_consent_version',
            'ALTER TABLE app_users ALTER COLUMN ocr_consent_at TYPE text'
            ' USING ocr_consent_at::text',
            'ALTER TABLE app_users DROP CONSTRAINT app_users_ocr_consent_pair',
            # 미동의를 표현할 수 없게 되는 변조. 값을 먼저 채워야 ALTER 가 안 죽는데,
            # ⚠ **두 칸을 같이 채워야 한다** — 시각만 채우면 짝 CHECK 이 UPDATE 를
            #   죽이고, 하네스는 "verifier 가 잡았다"와 "변조가 죽었다"를 stderr 낱말로
            #   가르므로 그 항목은 아무것도 증명하지 않는다 (2026-09-09 CI 실측).
            "UPDATE app_users SET ocr_consent_at = NOW(), ocr_consent_version = 'v1';"
            ' ALTER TABLE app_users ALTER COLUMN ocr_consent_at SET NOT NULL',
            # **조용히 틀리는 변조 — ③ 만 잡는다.**
            'ALTER TABLE app_users ALTER COLUMN ocr_consent_at SET DEFAULT NOW()',
        ]),
        ('2026-09-07', 'request_metrics', '', 'request_metrics', [
            'ALTER TABLE request_metrics DROP COLUMN elapsed_ms',
            'ALTER TABLE request_metrics ALTER COLUMN elapsed_ms TYPE bigint',
            # NOT NULL 을 잃는 변조. "지연을 못 쟀다" 와 "0ms" 가 안 갈리게 된다.
            'ALTER TABLE request_metrics ALTER COLUMN elapsed_ms DROP NOT NULL',
            'ALTER TABLE request_metrics DROP CONSTRAINT request_metrics_elapsed_check',
            'ALTER TABLE request_metrics DROP CONSTRAINT'
            ' request_metrics_principal_kind_check',
            'ALTER TABLE request_metrics DROP CONSTRAINT request_metrics_router_kind_check',
            'DROP INDEX idx_request_metrics_created',
            'DROP INDEX idx_request_metrics_request_id',
            # **외래 키가 생기는 변조.** 편해 보여서 누군가 걸 수 있는데, 걸면 탈퇴 한 번에
            # 지난달 지연 통계가 바뀐다. 참조 대상을 같이 만들어야 ALTER 가 안 죽는다.
            'CREATE TABLE chat_turns(id uuid PRIMARY KEY);'
            ' ALTER TABLE request_metrics ADD COLUMN turn_id uuid'
            ' REFERENCES chat_turns(id) ON DELETE CASCADE',
            # **금지된 열이 생기는 변조 둘 — 이 항목의 핵심이다.**
            # 둘 다 "디버깅에 편하니까" 로 실제로 들어올 법한 모양이고, 스키마상으로는
            # 아무 문제가 없다. verify ⑤ 만이 이것을 잡는다.
            'ALTER TABLE request_metrics ADD COLUMN query text',
            'ALTER TABLE request_metrics ADD COLUMN app_user_id uuid',
        ]),
        ('2026-09-04', 'pet_photo', PETS, 'pets', [
            'ALTER TABLE pets DROP COLUMN photo_storage_key CASCADE',
            'ALTER TABLE pets DROP COLUMN photo_generation CASCADE',
            'ALTER TABLE pets ALTER COLUMN photo_size_bytes TYPE bigint',
            # NOT NULL 을 거는 변조. 사진 없는 강아지가 정상인데 그것을 막는다.
            "UPDATE pets SET photo_storage_key = 'k', photo_content_type = 'image/jpeg',"
            " photo_generation = 'g', photo_size_bytes = 1, photo_updated_at = now();"
            ' ALTER TABLE pets ALTER COLUMN photo_storage_key SET NOT NULL',
            'ALTER TABLE pets DROP CONSTRAINT pets_photo_set',
            'ALTER TABLE pets DROP CONSTRAINT pets_photo_pending_set',
            'ALTER TABLE pets DROP CONSTRAINT pets_photo_content_type',
            'ALTER TABLE pets DROP CONSTRAINT pets_photo_size',
            'DROP INDEX idx_pets_photo_storage_key',
            # **부분 조건을 잃는 변조.** WHERE 가 빠지면 NULL 이 유일해야 하는 값이 되어
            # 사진 없는 강아지가 둘 이상일 수 없게 된다.
            'DROP INDEX idx_pets_photo_pending_key;'
            ' CREATE UNIQUE INDEX idx_pets_photo_pending_key ON pets (photo_pending_key)',
        ]),
        ('2026-09-04', 'dog_cards', APP_USERS + PETS_ONLY + SET_UPDATED_AT, 'dog_cards', [
            'ALTER TABLE dog_cards DROP COLUMN user_framed',
            'ALTER TABLE dog_cards ALTER COLUMN dog_name TYPE varchar(80)',
            'ALTER TABLE dog_cards ALTER COLUMN dog_id SET NOT NULL',
            'ALTER TABLE dog_cards DROP CONSTRAINT dog_cards_core_rect',
            'ALTER TABLE dog_cards DROP CONSTRAINT dog_cards_face_set',
            # **FK 의 삭제 동작을 뒤바꾸는 변조.** SET NULL → CASCADE 면 강아지가 무지개다리를
            # 건널 때 뽑아 둔 카드가 같이 사라진다. 모양은 멀쩡해 보인다.
            'ALTER TABLE dog_cards DROP CONSTRAINT dog_cards_dog_id_fkey;'
            ' ALTER TABLE dog_cards ADD FOREIGN KEY(dog_id) REFERENCES pets(id) ON DELETE CASCADE',
            # 부분 조건을 잃는 변조 — 얼굴 없는 카드가 둘 이상일 수 없게 된다
            'DROP INDEX idx_dog_cards_face_key;'
            ' CREATE UNIQUE INDEX idx_dog_cards_face_key ON dog_cards (face_storage_key)',
            'DROP TRIGGER trg_dog_cards_updated_at ON dog_cards',
        ]),
        ('2026-09-04', 'screening_records',
         APP_USERS + PETS_ONLY + SET_UPDATED_AT, 'screening_records', [
            'ALTER TABLE screening_records DROP COLUMN result',
            'ALTER TABLE screening_records ALTER COLUMN result TYPE text',
            # **사진 칸의 NOT NULL 을 푸는 변조.** 사진 자체가 기록이라(D-052) 사진 없는
            # 기록은 성립하지 않는다.
            'ALTER TABLE screening_records ALTER COLUMN photo_storage_key DROP NOT NULL',
            'ALTER TABLE screening_records DROP CONSTRAINT screening_records_status_check',
            'ALTER TABLE screening_records DROP CONSTRAINT'
            ' screening_records_photo_content_type_check',
            'ALTER TABLE screening_records DROP CONSTRAINT screening_records_pet_id_fkey;'
            ' ALTER TABLE screening_records ADD FOREIGN KEY(pet_id)'
            ' REFERENCES pets(id) ON DELETE CASCADE',
            'DROP INDEX idx_screening_records_photo_key',
            'DROP TRIGGER trg_screening_records_updated_at ON screening_records',
        ]),
        ('2026-09-05', 'answer_reports', CHAT_AND_ADMINS, 'answer_reports', [
            'ALTER TABLE answer_reports DROP COLUMN reviewed_at',
            'ALTER TABLE answer_reports ALTER COLUMN reason TYPE varchar(200)',
            # **한 사람이 같은 답변을 두 번 신고할 수 있게 되는 변조.** 신고 수가 사람 수가
            # 아니라 클릭 수가 된다 — 화면은 멀쩡히 돈다.
            'ALTER TABLE answer_reports DROP CONSTRAINT answer_reports_turn_user_key',
            'ALTER TABLE answer_reports DROP CONSTRAINT answer_reports_status_check',
            'ALTER TABLE answer_reports DROP CONSTRAINT answer_reports_review_state_check',
            # **RESTRICT → CASCADE 변조.** 관리자를 지우면 그 사람이 검토한 신고가 사라진다.
            'ALTER TABLE answer_reports DROP CONSTRAINT answer_reports_reviewed_by_fkey;'
            ' ALTER TABLE answer_reports ADD FOREIGN KEY(reviewed_by)'
            ' REFERENCES admin_users(id) ON DELETE CASCADE',
            'DROP INDEX answer_reports_status_created_idx',
        ]),
        # ⚠ **이 한 줄만 2회 적용을 안 한다** (`repeat=False`).
        #    `CREATE TEMP TABLE _org_backfill … ON COMMIT DROP` 인데, 하네스는 `transactionless()`
        #    로 COMMIT 을 벗기므로 temp 가 안 사라지고 두 번째 CREATE 가 "already exists" 로 죽는다.
        #    **마이그레이션이 틀린 것이 아니다** — 실제 적용 경로에서는 자기 트랜잭션이 COMMIT 될 때
        #    temp 가 사라져 여러 번 돌려도 안전하다 (CLAUDE.md 의 요구를 만족한다).
        #    깨지는 것은 하네스의 격리 모델과 `ON COMMIT DROP` 이 겹치는 자리뿐이라,
        #    **마이그레이션 SQL 을 고치는 대신 여기서 예외로 둔다** (#273 은 SQL 을 안 고친다).
        ('2026-09-05', 'documents_org_backfill', DOCUMENTS_WITH_ORG, 'documents', [
            'DROP INDEX idx_documents_org',
            # **값을 지우는 변조 — RAG-066 ① 의 실제 사고와 같은 모양이다.**
            # #268 의 `rag load` 가 `org` 없는 청크로 덮어써 2,592행이 통째로 사라졌고
            # 적재는 성공하고 예외도 안 났다. 이제 verify 가 잡는다.
            "UPDATE documents SET metadata = metadata - 'org'",
            # 빈 문자열로 채우는 변조. 있는 것처럼 보이면서 지역 필터를 통과시킨다.
            "UPDATE documents SET metadata = metadata || '{\"org\":\"\"}'::jsonb"
            " WHERE metadata ? 'org'",
        ], False),
        # ── 2026-09-07 (#292) — verify 가 아예 없던 여덟 장. ────────────────────────
        # #273 이 "SELECT 나열이라 틀려도 녹색"인 여섯 장을 고쳤는데, **파일 자체가 없는
        # 것들은 그때 목록에 안 들어왔다.** `db-migrate.yml` 은 `verify=true` 가 기본이라
        # 이 여덟 장은 Actions 탭으로 **다시 돌릴 수도 없었다**(손으로 verify=false 를
        # 골라야 했다). 전부 2026-09-03(#273) 이전에 쓰인 것들이다.
        #
        # ⚠ 여기 넷(walks · crawl_runs · documents_lexical · refresh_tokens)은 **뒤 장이
        #   자기가 만든 것을 걷어 가거나 넓힌다.** verify 는 그 뒤 상태의 DB 에서도 돌아야
        #   하므로 걷힌 것은 조건부로, 넓혀진 것은 "들어 있는지만" 본다. 각 verify 머리말 참고.
        ('2026-08-26', 'refresh_token_subject', REFRESH_TOKENS_OLD, 'refresh_tokens', [
            'ALTER TABLE refresh_tokens DROP COLUMN app_user_id',
            # **NOT NULL 만 풀고 CHECK 를 안 거는 변조.** 마이그레이션 본문이 *"2번만 하고
            # 이걸 빠뜨리면 주인 없는 세션 행이 만들어질 수 있다"* 라고 경고한 바로 그 자리다.
            'ALTER TABLE refresh_tokens DROP CONSTRAINT refresh_tokens_one_subject_check',
            # 반대 방향 — NOT NULL 을 도로 걸면 앱 회원 세션을 넣을 수 없다.
            # 픽스처 행이 admin 쪽이라 ALTER 자체는 성공한다(#271 의 NOT VALID 함정 회피).
            'ALTER TABLE refresh_tokens ALTER COLUMN admin_user_id SET NOT NULL',
            # **부분 조건을 잃는 변조.** 이름이 같아서 "인덱스가 있다"는 확인은 통과하는데,
            # 마이그레이션이 옛 전체 인덱스를 DROP 하고 부분으로 다시 만드는 것이 그 이유다.
            'DROP INDEX idx_refresh_tokens_admin;'
            ' CREATE INDEX idx_refresh_tokens_admin ON refresh_tokens (admin_user_id)',
            'DROP INDEX idx_refresh_tokens_app',
            # FK 의 삭제 동작을 잃는 변조 — 회원이 탈퇴해도 세션이 남는다.
            'ALTER TABLE refresh_tokens DROP CONSTRAINT refresh_tokens_app_user_id_fkey;'
            ' ALTER TABLE refresh_tokens ADD FOREIGN KEY(app_user_id) REFERENCES app_users(id)',
        ]),
        ('2026-08-28', 'documents_lexical', DOCUMENTS, 'documents', [
            'ALTER TABLE documents DROP COLUMN content_tokens CASCADE',
            'ALTER TABLE documents ALTER COLUMN content_tokens DROP NOT NULL',
            # **2단계(DROP DEFAULT)를 빠뜨린 상태.** 토큰이 다 찬 뒤에도 기본값이 남아 있으면
            # 안 채운 INSERT 가 조용히 성공한다 — 그 행은 dense 로만 찾히는 반쪽 문서다.
            # verify 가 "토큰이 찼는가"로 판정하므로 **여기서 먼저 채워야** 그 가지에 닿는다.
            "UPDATE documents SET content_tokens = 'x'",
            # **생성식의 config 만 바꾸는 변조 — 이 항목의 이유다.** 타입도 생성 여부도
            # 그대로라 모양만 보는 검사는 통과하는데, 한국어에 영어 스테머가 걸려 토큰이
            # 망가진다. dense 가 계속 답을 주므로 예외도 로그도 안 난다.
            "UPDATE documents SET content_tokens = 'x';"
            ' ALTER TABLE documents DROP COLUMN content_tsv;'
            ' ALTER TABLE documents ADD COLUMN content_tsv tsvector'
            " GENERATED ALWAYS AS (to_tsvector('english', content_tokens)) STORED",
            # 생성 컬럼을 보통 컬럼으로 바꾸는 변조 — 토큰과 어긋날 수 있게 된다.
            "UPDATE documents SET content_tokens = 'x';"
            ' ALTER TABLE documents DROP COLUMN content_tsv;'
            ' ALTER TABLE documents ADD COLUMN content_tsv tsvector',
        ]),
        ('2026-08-29', 'crawl_runs', '', 'crawl_runs', [
            'ALTER TABLE crawl_runs DROP COLUMN changed_slugs',
            # **`run_id` 에 NOT NULL 을 도로 거는 변조.** 행은 수집이 시작될 때 먼저 들어가고
            # 이 값은 끝나야 나온다 — 막으면 죽은 실행을 아예 기록할 수 없다.
            'ALTER TABLE crawl_runs ALTER COLUMN run_id SET NOT NULL',
            # 기본값을 잃는 변조 — 개정 감지가 "바뀐 것 없음"과 "안 적음"을 못 가른다.
            'ALTER TABLE crawl_runs ALTER COLUMN changed_slugs DROP DEFAULT',
            'ALTER TABLE crawl_runs DROP CONSTRAINT crawl_runs_status_check',
            # **`unavailable` 을 잃는 변조.** 키 미설정·시드 사망이 `failed` 와 한 칸이 되면
            # 사람이 고쳐야 할 것과 재시도할 것이 화면에서 안 갈린다.
            'ALTER TABLE crawl_runs DROP CONSTRAINT crawl_runs_status_check;'
            " ALTER TABLE crawl_runs ADD CONSTRAINT crawl_runs_status_check"
            " CHECK (status IN ('running','ok','failed'))",
            'ALTER TABLE crawl_runs DROP CONSTRAINT crawl_runs_trigger_check',
            'DROP INDEX idx_crawl_runs_run_id',
            # **정렬 방향을 뒤집는 변조.** 이름이 같아 "인덱스가 있다"는 통과하는데
            # 관리자 화면의 "소스별 최신 실행"이 거꾸로 읽힌다.
            'DROP INDEX idx_crawl_runs_source_started;'
            ' CREATE INDEX idx_crawl_runs_source_started ON crawl_runs (source_id, started_at)',
        ]),
        ('2026-08-30', 'crawl_runs_trigger_revision', CRAWL_RUNS_OLD, 'crawl_runs', [
            'ALTER TABLE crawl_runs DROP CONSTRAINT crawl_runs_trigger_check',
            # **되돌리는 변조 — 이 한 줄짜리 마이그레이션의 전부다.** 안 걸면 Beat 가 개정
            # 판정으로 깨운 수집의 기록이 CHECK 에 막히는데, 기록 실패는 크롤을 안 죽이므로
            # (RAG-047 ②) 수집은 정상으로 끝나고 화면에만 그 실행이 안 보인다.
            'ALTER TABLE crawl_runs DROP CONSTRAINT crawl_runs_trigger_check;'
            " ALTER TABLE crawl_runs ADD CONSTRAINT crawl_runs_trigger_check"
            " CHECK (trigger IN ('due','manual'))",
        ]),
        ('2026-08-31', 'walks', APP_USERS + PETS_ONLY, 'walks', [
            'ALTER TABLE walks DROP COLUMN ended_at',
            # 열린 채 남은 세션이 올라올 수 있게 되는 변조.
            'ALTER TABLE walks ALTER COLUMN ended_at DROP NOT NULL',
            'ALTER TABLE walks DROP CONSTRAINT walks_time_order',
            # **재업로드 중복 방지를 잃는 변조.** 네트워크가 끊겨 다시 올리면 같은 산책이
            # 두 건이 된다 — 화면은 멀쩡히 돈다.
            'ALTER TABLE walks DROP CONSTRAINT walks_client_session_unique',
            'ALTER TABLE walks DROP CONSTRAINT walks_app_user_id_fkey;'
            ' ALTER TABLE walks ADD FOREIGN KEY(app_user_id) REFERENCES app_users(id)',
            # **SET NULL → CASCADE 로 뒤바꾸는 변조.** 무지개다리를 건넌 아이와의 산책이
            # 그 아이를 지웠다고 없던 일이 된다. 모양은 멀쩡해 보인다 (dog_cards 의 전례).
            'ALTER TABLE walks DROP CONSTRAINT walks_pet_id_fkey;'
            ' ALTER TABLE walks ADD FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE',
            'DROP INDEX walks_owner_started_idx;'
            ' CREATE INDEX walks_owner_started_idx ON walks (app_user_id, started_at)',
            # 옛 좌표 표 쪽. 아직 안 걷힌 DB 에서만 도는 가지를 여기서 잰다.
            'ALTER TABLE walk_points DROP COLUMN chain_index',
            'ALTER TABLE walk_points DROP CONSTRAINT walk_points_pkey',
            'ALTER TABLE walk_points DROP CONSTRAINT walk_points_walk_id_fkey;'
            ' ALTER TABLE walk_points ADD FOREIGN KEY(walk_id) REFERENCES walks(id)',
        ]),
        ('2026-09-01', 'room_name', APP_USERS, 'app_users', [
            'ALTER TABLE app_users DROP COLUMN room_name',
            'ALTER TABLE app_users ALTER COLUMN room_name TYPE varchar(40)',
            # 값을 먼저 채워야 ALTER 가 안 죽는다 (#271 NOT VALID · #273 nickname 과 같은 함정).
            "UPDATE app_users SET room_name = 'r';"
            ' ALTER TABLE app_users ALTER COLUMN room_name SET NOT NULL',
            # **기본값을 거는 변조 — 이 항목의 이유다.** 타입도 널 허용도 그대로라 컬럼 모양만
            # 보는 검사는 통과하는데, 그 순간 "아직 안 정했다"가 사라진다. 방 앞 이름표가
            # 앱에 박혀 있어 남의 강아지 이름이 걸려 있던 것이 이 마이그레이션의 계기였다.
            "ALTER TABLE app_users ALTER COLUMN room_name SET DEFAULT '네옹이네'",
        ]),
        ('2026-09-01', 'training_rag_into_vectordb', VECTOR_EXTENSION, 'training_rag_chunks', [
            'ALTER TABLE training_rag_chunks DROP COLUMN text',
            # **차원을 바꾸는 변조.** `vector` 로만 두거나 차원이 달라지면 검색이 런타임에
            # 죽는다 — 훈련 RAG 는 768 이고 Life 의 1024 와 다르다.
            'ALTER TABLE training_rag_chunks DROP COLUMN embedding;'
            ' ALTER TABLE training_rag_chunks ADD COLUMN embedding vector(1024) NOT NULL',
            'ALTER TABLE training_rag_chunks ALTER COLUMN embedding_model DROP NOT NULL',
            # 모델별 공존을 잃는 변조 — 모델을 바꿔 다시 적재할 수 없게 된다.
            'ALTER TABLE training_rag_chunks'
            ' DROP CONSTRAINT training_rag_chunks_document_id_chunk_index_embedding_model_key',
            # 문서를 지웠는데 청크가 남는 변조 — 출처를 못 대는 벡터가 검색에 계속 잡힌다.
            'ALTER TABLE training_rag_chunks DROP CONSTRAINT training_rag_chunks_document_id_fkey;'
            ' ALTER TABLE training_rag_chunks ADD FOREIGN KEY(document_id)'
            ' REFERENCES training_rag_documents(document_id)',
            'DROP INDEX training_rag_chunks_embedding_hnsw',
            # **연산자 클래스를 바꾸는 변조.** 이름은 같고 인덱스도 있는데 `<=>` 질의가
            # 그것을 안 탄다 — 결과는 맞고 느려지기만 해서 아무도 안 알려준다.
            'DROP INDEX training_rag_chunks_embedding_hnsw;'
            ' CREATE INDEX training_rag_chunks_embedding_hnsw ON training_rag_chunks'
            ' USING hnsw (embedding vector_l2_ops)',
        ]),
        ('2026-09-02', 'gait_records', APP_USERS + PETS_ONLY + SET_UPDATED_AT, 'gait_records', [
            # **비교 전용 칸을 없애는 변조 — 이 항목의 이유다.** 셋을 한 컬럼에 합치면
            # `internal_feature_vector` 가 API 응답으로 새어 나간다. 남은 칸의 타입은
            # 그대로(jsonb)라 타입만 보는 검사로는 안 잡힌다.
            'ALTER TABLE gait_records DROP COLUMN internal_feature_vector',
            'ALTER TABLE gait_records DROP COLUMN summary_for_ui',
            'ALTER TABLE gait_records ALTER COLUMN status TYPE text',
            # **`FAILED` 를 잃는 변조.** 워커가 죽었을 때 그 상태를 못 적는데, 두 축이 달라서
            # (status vs quality_status) 사용자 안내가 "재시도"인지 "재촬영"인지 갈린다.
            'ALTER TABLE gait_records DROP CONSTRAINT gait_records_status_check;'
            " ALTER TABLE gait_records ADD CONSTRAINT gait_records_status_check"
            " CHECK (status IN ('PENDING','UPLOADED','PROCESSING','DONE'))",
            'ALTER TABLE gait_records DROP CONSTRAINT gait_records_quality_status_check',
            'ALTER TABLE gait_records DROP CONSTRAINT gait_records_pet_id_fkey;'
            ' ALTER TABLE gait_records ADD FOREIGN KEY(pet_id) REFERENCES pets(id)',
            'DROP INDEX idx_gait_records_pet_created',
            'DROP TRIGGER trg_gait_records_updated_at ON gait_records',
        ]),
        # -- 2026-09-07 (#295) — "verify 는 있는데 아무것도 안 잡는" 여덟 장. --------------
        # #273 이 SELECT 나열이던 여섯 장을 단언형으로 바꿨는데 **그때 목록에 안 들어간
        # 여덟 장**이 남아 있었다. #292 가 그것을 다시 발견했고, 같은 일이 세 번째로 나지
        # 않도록 `coverage_checks()` 를 같이 세웠다.
        #
        # 표가 얽힌 항목은 스텁 대신 **앞 장의 SQL 을 그대로 붓는다** (`prerequisites`).
        ('2026-09-01', 'walk_pets', APP_USERS + PETS_ONLY
         + prerequisites('2026-08-31_walks'), 'walk_pets', [
            'ALTER TABLE walk_pets DROP CONSTRAINT walk_pets_pkey;'
            ' ALTER TABLE walk_pets ADD PRIMARY KEY (walk_id)',
            'ALTER TABLE walk_pets DROP CONSTRAINT walk_pets_pet_id_fkey;'
            ' ALTER TABLE walk_pets ADD FOREIGN KEY(pet_id) REFERENCES pets(id)',
            'DROP INDEX walk_pets_pet_idx',
            # **옛 칸을 되살리는 변조.** 값만 옮기고 DROP 을 빠뜨린 상태와 같은 모양이고,
            # 그러면 같은 사실이 두 곳에 있어 앱이 읽는 쪽에 따라 답이 달라진다.
            'ALTER TABLE walks ADD COLUMN pet_id uuid REFERENCES pets(id) ON DELETE SET NULL',
        ]),
        ('2026-09-02', 'pet_farewell', PETS, 'pets', [
            'ALTER TABLE pets DROP COLUMN farewell_on',
            'ALTER TABLE pets ALTER COLUMN farewell_on TYPE timestamptz',
            # 값을 먼저 채워야 ALTER 가 안 죽는다 (#271 · #273 · #288 과 같은 함정).
            'UPDATE pets SET farewell_on = CURRENT_DATE;'
            ' ALTER TABLE pets ALTER COLUMN farewell_on SET NOT NULL',
            # **기본값을 거는 변조** — 그 순간 전 강아지가 배웅된 것이 된다.
            'ALTER TABLE pets ALTER COLUMN farewell_on SET DEFAULT CURRENT_DATE',
            'ALTER TABLE pets DROP CONSTRAINT pets_farewell_not_future',
            'ALTER TABLE pets DROP CONSTRAINT pets_farewell_after_birth',
        ]),
        ('2026-09-01', 'chats', APP_USERS + PETS_ONLY, 'chat_turns', [
            'ALTER TABLE chat_turns DROP COLUMN public_response',
            'ALTER TABLE chat_turns ALTER COLUMN user_content TYPE varchar(100)',
            # **상태 기계를 푸는 변조.** 타입도 NOT NULL 도 그대로라 컬럼 검사는 다 통과하는데,
            # 그 순간 반쯤 채워진 행(답은 있는데 processing 인)이 들어온다.
            'ALTER TABLE chat_turns DROP CONSTRAINT chat_turns_state_check',
            'ALTER TABLE chat_turns DROP CONSTRAINT chat_turns_assistant_status_check;'
            " ALTER TABLE chat_turns ADD CONSTRAINT chat_turns_assistant_status_check"
            " CHECK (assistant_status IS NULL OR assistant_status IN ('ANSWERED','FAILED'))",
            # 재전송이 대화를 두 배로 만드는 변조.
            'ALTER TABLE chat_turns DROP CONSTRAINT chat_turns_session_client_key',
            'ALTER TABLE chat_summaries DROP CONSTRAINT chat_summaries_state_check',
            # **방을 지우면 요약이 같이 사라지는 변조.** SET NULL -> CASCADE 다.
            'ALTER TABLE chat_summaries DROP CONSTRAINT chat_summaries_source_session_id_fkey;'
            ' ALTER TABLE chat_summaries ADD FOREIGN KEY(source_session_id)'
            ' REFERENCES chat_sessions(id) ON DELETE CASCADE',
            # 빈 방이 무한히 쌓이는 변조 — UNIQUE 를 잃는다.
            'DROP INDEX chat_sessions_one_draft_idx;'
            ' CREATE INDEX chat_sessions_one_draft_idx ON chat_sessions (app_user_id, pet_id)'
            ' WHERE last_message_at IS NULL',
            # 부분 조건을 잃는 변조.
            'DROP INDEX chat_sessions_one_draft_idx;'
            ' CREATE UNIQUE INDEX chat_sessions_one_draft_idx'
            ' ON chat_sessions (app_user_id, pet_id, id)',
        ]),
        ('2026-09-02', 'walk_analyses', APP_USERS + PETS_ONLY
         + prerequisites('2026-08-31_walks'), 'walk_analyses', [
            'ALTER TABLE walk_analyses DROP COLUMN measurement_receipt',
            # **신원 UNIQUE 를 줄이는 변조.** 규칙을 고친 순간 옛 계산을 덮어쓰게 된다.
            'ALTER TABLE walk_analyses DROP CONSTRAINT walk_analyses_identity_unique;'
            ' ALTER TABLE walk_analyses ADD CONSTRAINT walk_analyses_identity_unique'
            ' UNIQUE (walk_id, input_fingerprint)',
            # jsonb 의 **모양**을 잃는 변조 — 타입은 그대로라 컬럼 검사는 통과한다.
            'ALTER TABLE walk_analyses DROP CONSTRAINT walk_analyses_events_array',
            'ALTER TABLE walk_analyses DROP CONSTRAINT walk_analyses_facts_object',
            'ALTER TABLE walk_analyses DROP CONSTRAINT walk_analyses_terminal_sequence_check',
            'ALTER TABLE walk_analyses DROP CONSTRAINT walk_analyses_input_fingerprint_check',
            'DROP INDEX walk_analyses_walk_derived_idx;'
            ' CREATE INDEX walk_analyses_walk_derived_idx ON walk_analyses (walk_id, derived_at)',
            # `walks` 쪽 칸. 기본값을 잃으면 이미 쌓인 산책의 상태가 안 정해진다.
            'ALTER TABLE walks ALTER COLUMN analysis_state DROP DEFAULT',
            'ALTER TABLE walks DROP CONSTRAINT walks_analysis_state_check',
        ]),
        ('2026-09-02', 'walk_point_chunks', APP_USERS + PETS_ONLY
         + prerequisites('2026-08-31_walks'), 'walk_point_chunks', [
            'ALTER TABLE walk_point_chunks DROP COLUMN payload',
            'ALTER TABLE walk_point_chunks ALTER COLUMN point_count TYPE bigint',
            'ALTER TABLE walk_point_chunks DROP CONSTRAINT walk_point_chunks_pkey;'
            ' ALTER TABLE walk_point_chunks ADD PRIMARY KEY (walk_id, seq_to)',
            'ALTER TABLE walk_point_chunks DROP CONSTRAINT walk_point_chunks_seq_order',
            'ALTER TABLE walk_point_chunks DROP CONSTRAINT walk_point_chunks_point_count_check',
            'ALTER TABLE walk_point_chunks DROP CONSTRAINT walk_point_chunks_walk_id_fkey;'
            ' ALTER TABLE walk_point_chunks ADD FOREIGN KEY(walk_id) REFERENCES walks(id)',
            # **옛 표를 되살리는 변조 — 이 항목의 이유다.** 옮기기만 하고 DROP 을 빠뜨리면
            # 같은 좌표가 두 곳에 남는데, 옛 verify 는 그것을 SELECT 로 찍기만 했다.
            'CREATE TABLE walk_points(walk_id uuid, client_seq integer)',
        ]),
        ('2026-09-03', 'territory_visits', APP_USERS + PETS_ONLY,
         'territory_verified_visits', [
            'ALTER TABLE territory_verified_visits DROP COLUMN evidence_version',
            # **사진 한 장으로 여러 번 인정받게 되는 변조.** PK 가 따로 있어 스키마는
            # 멀쩡해 보인다.
            'ALTER TABLE territory_verified_visits'
            ' DROP CONSTRAINT territory_verified_visits_attempt_id_key',
            'ALTER TABLE territory_verified_visits'
            ' DROP CONSTRAINT territory_verified_visits_attempt_id_fkey;'
            ' ALTER TABLE territory_verified_visits ADD FOREIGN KEY(attempt_id)'
            ' REFERENCES territory_attempts(id)',
            # 가상 위치가 통과하게 되는 변조.
            'ALTER TABLE territory_attempts DROP CONSTRAINT territory_attempts_not_mock',
            # **오차를 안 보게 되는 변조.** `distance_m` 만 남으면 오차 50m 측정이
            # "1m 앞"으로 통과한다.
            'ALTER TABLE territory_attempts DROP CONSTRAINT territory_attempts_location_evidence',
            'ALTER TABLE territory_attempts'
            ' DROP CONSTRAINT territory_attempts_confirmed_photo_identity',
            'ALTER TABLE territory_attempts'
            ' DROP CONSTRAINT territory_attempts_final_vision_metadata',
            'ALTER TABLE territory_attempts DROP CONSTRAINT territory_attempts_status_check;'
            " ALTER TABLE territory_attempts ADD CONSTRAINT territory_attempts_status_check"
            " CHECK (status IN ('PENDING_UPLOAD','VERIFIED'))",
            'ALTER TABLE territory_attempts'
            ' DROP CONSTRAINT territory_attempts_photo_storage_key_key',
        ]),
        ('2026-09-03', 'walk_capsules', APP_USERS + PETS_ONLY
         + prerequisites('2026-08-31_walks', '2026-09-02_walk_analyses'), 'walk_capsules', [
            'ALTER TABLE walk_capsules DROP COLUMN trail_context',
            'ALTER TABLE walk_capsules ALTER COLUMN sealed_at DROP NOT NULL',
            'ALTER TABLE walk_capsules DROP CONSTRAINT walk_capsules_context_object',
            # **빈 배열을 허용하는 변조.** `array` 라는 낱말은 남아서 모양만 보면 안 잡힌다.
            'ALTER TABLE walk_capsules DROP CONSTRAINT walk_capsules_capabilities_array;'
            " ALTER TABLE walk_capsules ADD CONSTRAINT walk_capsules_capabilities_array"
            " CHECK (jsonb_typeof(capabilities) = 'array')",
            'ALTER TABLE walk_capsules DROP CONSTRAINT walk_capsules_analysis_id_fkey;'
            ' ALTER TABLE walk_capsules ADD FOREIGN KEY(analysis_id)'
            ' REFERENCES walk_analyses(id)',
            # **PK 를 줄이는 변조** — 다른 물감으로 칠한 장이 서로를 덮어쓴다.
            'ALTER TABLE walk_cellophane_sheets DROP CONSTRAINT walk_cellophane_sheets_pkey;'
            ' ALTER TABLE walk_cellophane_sheets ADD PRIMARY KEY (analysis_id)',
            'ALTER TABLE walk_cellophane_sheets DROP CONSTRAINT walk_cellophane_identity_nonempty',
            'ALTER TABLE walk_cellophane_sheets DROP CONSTRAINT walk_cellophane_fingerprint_check',
            'DROP INDEX walk_cellophane_paint_fp_idx',
        ]),
        ('2026-09-04', 'admin_audit_log', CHAT_AND_ADMINS, 'admin_audit_log', [
            'ALTER TABLE admin_audit_log DROP COLUMN detail',
            # **로그인 실패를 기록할 수 없게 되는 변조.** 없는 아이디로 두드린 시도는
            # 가리킬 admin_users 행이 아예 없다.
            'ALTER TABLE admin_audit_log ALTER COLUMN admin_user_id SET NOT NULL',
            # **RESTRICT -> CASCADE 변조 — 이 항목의 이유다.** 관리자를 지우는 순간
            # 그 사람이 한 일이 통째로 사라진다. 모양은 멀쩡해 보인다.
            'ALTER TABLE admin_audit_log DROP CONSTRAINT admin_audit_log_admin_user_id_fkey;'
            ' ALTER TABLE admin_audit_log ADD FOREIGN KEY(admin_user_id)'
            ' REFERENCES admin_users(id) ON DELETE CASCADE',
            'ALTER TABLE admin_audit_log DROP CONSTRAINT admin_audit_log_detail_object_check',
            'DROP INDEX idx_admin_audit_log_created',
            # 부분 조건을 잃는 변조 둘.
            'DROP INDEX idx_admin_audit_log_admin;'
            ' CREATE INDEX idx_admin_audit_log_admin'
            ' ON admin_audit_log (admin_user_id, created_at DESC)',
            'DROP INDEX idx_admin_audit_log_target;'
            ' CREATE INDEX idx_admin_audit_log_target ON admin_audit_log (target_type, target_id)',
            # **append-only 를 깨는 변조.** 다른 표에 다 있는 것이 여기만 없는 것이 의도다.
            'ALTER TABLE admin_audit_log ADD COLUMN updated_at timestamptz',
        ]),
        # -- 단언형인데 등록만 없던 둘 (#295). -------------------------------------------
        ('2026-09-05', 'territory_claims', APP_USERS + PETS_ONLY
         + prerequisites('2026-09-03_territory_visits'), 'territory_claims', [
            'ALTER TABLE territory_claims DROP COLUMN pet_id',
            'ALTER TABLE territory_occupancies DROP CONSTRAINT territory_occupancies_pkey',
            'ALTER TABLE territory_claim_photos'
            ' DROP CONSTRAINT territory_claim_photos_claim_id_fkey;'
            ' ALTER TABLE territory_claim_photos ADD FOREIGN KEY(claim_id)'
            ' REFERENCES territory_claims(id)',
            'DROP INDEX ix_territory_claim_photos_claim_id',
            'DROP INDEX territory_claims_pet_idx',
        ]),
        ('2026-09-10', 'activity_monthly', APP_USERS_WITH_STATUS + PETS_ONLY + SET_UPDATED_AT
         + prerequisites('2026-08-31_walks', '2026-09-02_walk_analyses',
                         '2026-09-03_territory_visits', '2026-09-05_territory_claims',
                         '2026-09-06_activity_game'),
         'activity_monthly_seasons', [
            'ALTER TABLE activity_accounts DROP COLUMN final_rank',
            'ALTER TABLE activity_accounts DROP CONSTRAINT activity_final_rank_positive',
            'ALTER TABLE activity_monthly_seasons DROP CONSTRAINT activity_monthly_seasons_pkey',
            'ALTER TABLE activity_monthly_seasons DROP CONSTRAINT activity_monthly_seasons_previous_season_id_key',
            'ALTER TABLE activity_monthly_seasons DROP CONSTRAINT activity_monthly_seasons_previous_season_id_fkey',
            'ALTER TABLE activity_monthly_seasons DROP CONSTRAINT activity_monthly_seasons_season_id_fkey',
            'ALTER TABLE activity_monthly_seasons DROP CONSTRAINT activity_monthly_seasons_check',
        ]),
        ('2026-09-10', 'territory_expiry', PETS + SET_UPDATED_AT
         + prerequisites('2026-09-03_territory_visits', '2026-09-05_territory_claims'),
         'territory_renewals', [
            'ALTER TABLE territory_occupancies DROP COLUMN expires_at',
            'ALTER TABLE territory_renewals DROP CONSTRAINT territory_renewals_pkey',
            'ALTER TABLE territory_renewals DROP CONSTRAINT territory_renewals_claim_id_fkey',
            'ALTER TABLE territory_renewals DROP CONSTRAINT territory_renewals_check',
            'ALTER TABLE territory_renewals ALTER COLUMN contact DROP NOT NULL',
            'DROP INDEX territory_occupancies_expiry_idx',
            'DROP INDEX territory_renewals_claim_idx',
        ]),
        ('2026-09-10', 'activity_rewards', APP_USERS_WITH_STATUS + PETS_ONLY + SET_UPDATED_AT
         + prerequisites('2026-08-31_walks', '2026-09-02_walk_analyses',
                         '2026-09-03_territory_visits', '2026-09-05_territory_claims',
                         '2026-09-06_activity_game'),
         'activity_base_rewards', [
            'ALTER TABLE activity_base_rewards DROP CONSTRAINT activity_base_rewards_pkey CASCADE',
            'ALTER TABLE activity_base_rewards DROP CONSTRAINT activity_base_rewards_paid_check',
            'ALTER TABLE activity_reward_details DROP CONSTRAINT activity_reward_details_pkey',
            'ALTER TABLE activity_reward_details DROP COLUMN takeover_points',
            'DROP TRIGGER activity_reward_owner_cleanup ON app_users',
            'DROP INDEX activity_base_rewards_member',
        ]),
        ('2026-09-06', 'activity_game', APP_USERS_WITH_STATUS + PETS_ONLY + SET_UPDATED_AT
         + prerequisites('2026-08-31_walks', '2026-09-02_walk_analyses',
                         '2026-09-03_territory_visits', '2026-09-05_territory_claims'),
         'activity_holding_periods', [
            'ALTER TABLE activity_holding_periods DROP COLUMN site_id',
            # 트리거 셋은 **탈퇴·삭제 때 게임 데이터를 치우는** 자리다. 하나만 빠져도
            # 지운 강아지가 점령판에 계속 서 있는다.
            'DROP TRIGGER activity_pet_cleanup ON pets',
            'DROP TRIGGER activity_owner_cleanup ON app_users',
            'DROP TRIGGER activity_ownership_guard ON territory_occupancies',
            'DROP INDEX activity_one_active_season',
            'DROP INDEX activity_one_open_holding',
        ]),
        # 2026-09-08 (#329) — 한 청킹의 여러 임베딩이 공존하게. **첫 변조가 핵심이다** —
        # PK 가 chunk_id 로 되돌아가면 다른 모델 적재가 옛 벡터를 조용히 덮어쓴다.
        ('2026-09-08', 'training_rag_embedding_key', TRAINING_RAG_OLD,
         'training_rag_chunks', [
            'ALTER TABLE training_rag_chunks DROP CONSTRAINT training_rag_chunks_pkey; '
            'ALTER TABLE training_rag_chunks ADD PRIMARY KEY (chunk_id)',
            'ALTER TABLE training_rag_chunks DROP CONSTRAINT training_rag_chunks_chunk_id_model_key',
            'ALTER TABLE training_rag_chunks ALTER COLUMN chunk_id DROP NOT NULL',
            'ALTER TABLE training_rag_chunks DROP CONSTRAINT training_rag_chunks_document_id_fkey',
            'ALTER TABLE training_rag_chunks ADD UNIQUE (document_id, chunk_index, embedding_model)',
        ]),
        # 2026-09-09 (D-063) — pose_model 컬럼 + 관절 키로 판별되는 행만 백필. 픽스처는 09-02 의
        # 표 + 09-09 tier CHECK 확장 위에 여섯 행(GAIT_RECORDS_POSE_MODEL_ROWS). 변조는 규칙의
        # 양쪽을 다 민다 — "명확한 행에 틀린 값/NULL" 과 "허용되지 않은 ID". **빈 객체 행에
        # 허용된 ID 를 넣는 것은 변조가 아니다**(새 분석의 unavailable 기록이 그 모양) — 그
        # 통과 조건은 backend/tests/test_gait_pose_model.py 가 verify SQL 의 조건을 읽어 지킨다.
        ('2026-09-09', 'gait_records_pose_model',
         APP_USERS + PETS_ONLY + SET_UPDATED_AT
         + prerequisites('2026-09-02_gait_records', '2026-09-09_gait_quality_tier_ok')
         + GAIT_RECORDS_POSE_MODEL_ROWS,
         'gait_records', [
            'ALTER TABLE gait_records DROP COLUMN pose_model',
            # 값이 들어갈 만큼 넓게 잡는다 — 좁히면 ALTER 자체가 죽어 verify 가 아니라 변조가
            # 실패하고, 하네스는 그것을 "못 잡음" 으로 읽는다 (#271 NOT VALID 과 같은 함정).
            'ALTER TABLE gait_records ALTER COLUMN pose_model TYPE varchar(40)',
            # AP-10K 관절 행에 legacy 값 — 관절 정의가 다른 기록끼리 비교되게 하는 변조.
            "UPDATE gait_records SET pose_model = 'yolov8_12kp_best'"
            " WHERE summary_for_ui ? 'L_Hip' AND NOT summary_for_ui ? 'Iliac crest'",
            # legacy 관절 행을 NULL 로 — 백필이 안 돈 상태와 같은 모양.
            "UPDATE gait_records SET pose_model = NULL"
            " WHERE summary_for_ui ? 'Iliac crest' AND NOT summary_for_ui ? 'L_Hip'",
            # 레지스트리에 없는 ID. v4 compare 가 키 없을 때 쓰던 옛 기본값이 그대로 들어오는 사고.
            "UPDATE gait_records SET pose_model = 'best_pt' WHERE summary_for_ui = '{}'::jsonb",
        ]),
)


def sql_checks():
    # libpq settings are supplied only by the dedicated disposable CI service.
    assert os.environ.get('PGHOST') in ('127.0.0.1', 'localhost', '::1')
    total = 0
    for date, name, fixture, table, mutations, *rest in CHECKS:
        migration = transactionless(
            (ROOT / f'db/migrations/{date}_{name}.sql').read_text(encoding='utf-8'))
        verifier = unqualified(
            (ROOT / f'db/migrations/verify_{date}_{name}.sql').read_text(encoding='utf-8'))
        applied = migration * (2 if (rest[0] if rest else True) else 1)
        for mutation in ['', f'DROP TABLE {table} CASCADE', *mutations]:
            schema = 'verify_test_' + uuid.uuid4().hex
            sql = (f'BEGIN; CREATE SCHEMA {schema}; SET LOCAL search_path TO {schema}; '
                   + fixture + '\n' + applied
                   + mutation + ';\n' + verifier + '\nROLLBACK;')
            result = subprocess.run(['psql', '-X', '-v', 'ON_ERROR_STOP=1'],
                                    input=sql, text=True, capture_output=True)
            if mutation:
                assert result.returncode != 0 and (
                    'mismatch' in result.stderr or 'missing table' in result.stderr
                ), result.stderr
            else:
                assert result.returncode == 0, result.stderr
            total += 1
    print(f'PostgreSQL: {total} checks passed (repeat apply, valid schema, schema damage).')


# `verify_*.sql` 이 **행동 테스트**라 하네스에 안 넣는 것. 이름 하나에 이유 하나다.
#
# 하네스는 "일회용 스키마에 픽스처 + 마이그레이션을 붓고, 일부러 망가뜨린 뒤, verify 가
# 종료 코드로 잡는가"를 본다. 아래 파일은 그 모양이 아니다 — **자기 스키마를 직접 만들고**
# (`CREATE SCHEMA daengs_pets_check`) `\i db/init/*.sql` 로 원본 스키마를 부르고 INSERT 로
# FK 의 실제 동작을 본다. 안쪽의 `DROP SCHEMA` 가 하네스의 격리(바깥 트랜잭션 + ROLLBACK)와
# 겹치고, `\i` 의 상대 경로는 psql 의 작업 디렉터리를 요구한다.
#
# **못 넣는 것이지 부실한 것이 아니다.** 이 파일은 카탈로그가 아니라 동작을 재고, 세 단언이
# 다 `RAISE EXCEPTION` 이다. 여기 이름을 더할 때는 **왜 하네스 모델과 안 맞는지**를 적는다 —
# "나중에 하자" 는 이유가 아니다.
BEHAVIOURAL = {
    '2026-08-31_pets': r'자기 스키마를 만들고 db/init 을 \i 로 부르는 행동 테스트',
}


def coverage_checks():
    """**모든 마이그레이션이 ⓐ 짝 ⓑ 단언 ⓒ 등록을 갖는지.** DB 도 psql 도 안 쓴다.

    이 검사가 있는 이유는 같은 부채가 **두 번** 났기 때문이다 — #273 이 "SELECT 나열이라
    틀려도 녹색"인 여섯 장을 고쳤는데 여덟 장이 그때 목록에 안 들어갔고, #292 가 그것을
    다시 발견했다. **목록을 사람이 관리하는 한 세 번째가 온다.**

    세 가지를 본다:
      ⓐ `verify_<파일>` 이 있는가            — 없으면 `db-migrate.yml` 이 적용 자체를 막는다
      ⓑ `RAISE EXCEPTION` 이 하나라도 있는가 — 없으면 스키마가 어떻든 종료 코드 0 이다
      ⓒ `CHECKS` 에 등록됐는가              — 안 하면 그 단언이 실제로 무엇을 잡는지 아무도 안 잰다

    ⓒ 의 예외는 `BEHAVIOURAL` 하나뿐이고 이유가 그 옆에 적혀 있다.
    """
    migrations = sorted(p.stem for p in (ROOT / 'db/migrations').glob('*.sql')
                        if not p.stem.startswith('verify_'))
    registered = {f'{date}_{name}' for date, name, *_ in CHECKS}
    problems = []
    for stem in migrations:
        verifier = ROOT / f'db/migrations/verify_{stem}.sql'
        if not verifier.exists():
            problems.append(f'{stem}: verify_{stem}.sql 이 없다.'
                            ' db-migrate.yml 이 verify=true 로 적용을 막는다')
            continue
        text = verifier.read_text(encoding='utf-8')
        if 'RAISE EXCEPTION' not in text:
            problems.append(f'{stem}: verify 에 RAISE EXCEPTION 이 없다.'
                            ' SELECT 만 있으면 스키마가 어떻든 종료 코드 0 이라 틀려도 녹색이다')
        if stem not in registered and stem not in BEHAVIOURAL:
            problems.append(f'{stem}: CHECKS 에 등록되지 않았다.'
                            ' 단언이 실제로 변조를 잡는지 아무도 재지 않는다'
                            ' (하네스 모델과 안 맞으면 BEHAVIOURAL 에 이유와 함께 적을 것)')
    for stem in sorted(BEHAVIOURAL):
        if not (ROOT / f'db/migrations/{stem}.sql').exists():
            problems.append(f'{stem}: BEHAVIOURAL 에 있는데 마이그레이션이 없다. 목록이 낡았다')
    if problems:
        print('마이그레이션 검증 커버리지에 구멍이 있습니다:')
        for problem in problems:
            print(f'  - {problem}')
        raise SystemExit(1)
    print(f'Coverage: {len(migrations)} migrations, 짝 · 단언 · 등록 모두 확인'
          f' (행동 테스트 예외 {len(BEHAVIOURAL)}).')


def workflow_script(step):
    source = (ROOT / '.github/workflows/db-migrate.yml').read_text(encoding='utf-8')
    block = source.split(f'      - name: {step}\n', 1)[1].split('\n      - name:', 1)[0]
    body = block.split('        run: |\n', 1)[1]
    lines = []
    for line in body.splitlines():
        if line and not line.startswith('          '):
            break
        lines.append(line[10:])
    script = '\n'.join(lines)
    assert script.isascii(), 'PowerShell 5.1/cmd run blocks must be ASCII'
    return script


def windows_checks():
    assert os.name == 'nt'
    fetch = workflow_script('Fetch migration SQL')
    validate = workflow_script('Validate input')
    with tempfile.TemporaryDirectory() as folder:
        origin = Path(folder) / 'origin'
        origin.mkdir()
        def git(*args):
            subprocess.run(['git', '-C', str(origin), *args], check=True, capture_output=True)
        git('init', '-b', 'fixture')
        git('config', 'user.name', 'CI')
        git('config', 'user.email', 'ci@example.invalid')
        files = origin / 'db/migrations'
        files.mkdir(parents=True)
        payload = '-- 한글 바이트 보존\nSELECT 1;\n'.encode('utf-8')
        for name in ('ok', 'missing', 'empty'):
            (files / f'{name}.sql').write_bytes(payload)
        (files / 'ok.sql').write_bytes(payload)
        (files / 'verify_ok.sql').write_bytes(payload)
        (files / 'verify_empty.sql').write_bytes(b'')
        (files / 'blank.sql').write_bytes(b'')
        git('add', '.')
        git('commit', '-m', 'fixture')
        cases = [('ok', 'true', True), ('missing', 'true', False),
                 ('empty', 'true', False), ('missing', 'false', True),
                 ('absent', 'false', False), ('blank', 'false', False)]
        for i, (name, verify, success) in enumerate(cases):
            work = Path(folder) / f'work{i}'
            subprocess.run(['git', 'clone', str(origin), str(work)], check=True, capture_output=True)
            stale = work / '_migrate'
            stale.mkdir()
            (stale / f'verify_{name}.sql').write_bytes(payload)
            script = work / 'fetch.cmd'
            script.write_text('@echo off\n' + fetch, encoding='ascii')
            env = dict(os.environ, MIG_REF='fixture', MIG_FILE=f'{name}.sql', MIG_VERIFY=verify)
            result = subprocess.run(['cmd', '/d', '/c', str(script)], cwd=work, env=env,
                                    capture_output=True)
            assert (result.returncode == 0) == success, (name, verify, result.returncode, success, result.stdout, result.stderr, fetch)
            if success:
                assert (stale / f'{name}.sql').read_bytes() == payload
                if verify == 'true':
                    assert (stale / f'verify_{name}.sql').read_bytes() == payload
                else:
                    assert not (stale / f'verify_{name}.sql').exists()
        script = Path(folder) / 'validate.ps1'
        script.write_text(validate, encoding='ascii')
        for ref, success in [('fixture', True), ('feat/test', True), ('bad&echo', False)]:
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(script)],
                                    env=dict(os.environ, MIG_REF=ref, MIG_FILE='ok.sql'),
                                    capture_output=True)
            assert (result.returncode == 0) == success, result.stderr
    print('Windows: 9 checks passed (actual git/cmd bytes, missing/empty/stale, opt-out, ref).')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['sql', 'windows', 'coverage'])
    args = parser.parse_args()
    {'sql': sql_checks, 'windows': windows_checks,
     'coverage': coverage_checks}[args.mode]()
