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
PETS_ONLY = (
    "CREATE TABLE pets("
    " id uuid PRIMARY KEY,"
    " app_user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,"
    " name varchar(40) NOT NULL);"
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
    return '\n'.join(keep) + '\n'


def sql_checks():
    # libpq settings are supplied only by the dedicated disposable CI service.
    assert os.environ.get('PGHOST') in ('127.0.0.1', 'localhost', '::1')
    total = 0
    # 항목은 (날짜, 이름, 픽스처, 테이블, 변조들[, 2회 적용할까]).
    # 마지막 칸은 거의 언제나 True 다 — **멱등은 이 저장소가 마이그레이션에 요구하는 성질**이라
    # (CLAUDE.md: 버전 테이블이 없으니 여러 번 돌려도 안전하게) 기본으로 두 번 적용해 본다.
    # False 로 두는 자리는 `documents_org_backfill` 하나뿐이고 이유는 그 항목에 적었다.
    for date, name, fixture, table, mutations, *rest in (
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
    ):
        migration = transactionless(
            (ROOT / f'db/migrations/{date}_{name}.sql').read_text(encoding='utf-8'))
        verifier = (ROOT / f'db/migrations/verify_{date}_{name}.sql').read_text(encoding='utf-8')
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
    parser.add_argument('mode', choices=['sql', 'windows'])
    args = parser.parse_args()
    {'sql': sql_checks, 'windows': windows_checks}[args.mode]()
