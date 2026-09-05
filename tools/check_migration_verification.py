"""Disposable PostgreSQL and real Windows cmd checks; no application configuration."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def sql_checks():
    # libpq settings are supplied only by the dedicated disposable CI service.
    assert os.environ.get('PGHOST') in ('127.0.0.1', 'localhost', '::1')
    total = 0
    for name, table, mutations in (
        ('walk_entries', 'walk_entries', [
            'ALTER TABLE walk_entries DROP COLUMN payload',
            'ALTER TABLE walk_entries ALTER COLUMN revision TYPE bigint',
            'ALTER TABLE walk_entries ALTER COLUMN mutation_id DROP NOT NULL',
            'ALTER TABLE walk_entries DROP CONSTRAINT walk_entries_pkey',
            'ALTER TABLE walk_entries DROP CONSTRAINT walk_entries_walk_id_fkey',
            'ALTER TABLE walk_entries DROP CONSTRAINT walk_entries_revision_check',
            'ALTER TABLE walk_entries DROP CONSTRAINT walk_entries_walk_id_fkey; '
            'ALTER TABLE walk_entries ADD FOREIGN KEY(walk_id) REFERENCES walks(id)',
        ]),
        ('walk_storyboards', 'walk_storyboards', [
            'ALTER TABLE walk_storyboards DROP COLUMN bundle',
            'ALTER TABLE walk_storyboards ALTER COLUMN input_revision TYPE varchar(80)',
            'ALTER TABLE walk_storyboards ALTER COLUMN updated_at DROP NOT NULL',
            'ALTER TABLE walk_storyboards DROP CONSTRAINT walk_storyboards_pkey',
            'ALTER TABLE walk_storyboards DROP CONSTRAINT walk_storyboards_walk_id_fkey',
            'ALTER TABLE walk_storyboards DROP CONSTRAINT walk_storyboards_status_check',
            'ALTER TABLE walk_storyboards DROP CONSTRAINT walk_storyboards_generation_check',
        ]),
    ):
        migration = (ROOT / f'db/migrations/2026-09-05_{name}.sql').read_text(encoding='utf-8')
        verifier = (ROOT / f'db/migrations/verify_2026-09-05_{name}.sql').read_text(encoding='utf-8')
        for mutation in ['', f'DROP TABLE {table}', *mutations]:
            schema = 'verify_test_' + uuid.uuid4().hex
            sql = (f'BEGIN; CREATE SCHEMA {schema}; SET LOCAL search_path TO {schema}; '
                   'CREATE TABLE walks(id uuid PRIMARY KEY);\n' + migration + migration
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
            assert (result.returncode == 0) == success, (name, result.stdout, result.stderr)
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
