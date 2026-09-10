"""마이그레이션 검증 하네스(`tools/check_migration_verification.py`)를 pytest 에 연결한다.

이 파일이 생긴 이유: 그 하네스는 `.github/workflows/migration-verification-tests.yml`
(`ubuntu-latest`)이 부르는데, 이 팀은 GitHub 호스티드 러너를 안 쓰기로 했다 — 그 워크플로우는
**한 번도 안 돈다**. 이 팀에게 "CI" 는 `cd backend && uv run pytest` 뿐이라, 하네스가 실제로
검사하는지는 여기서 걸어야만 존재한다.

세 서브커맨드를 직접 실행하고(파이프 없이 `$?` 를 바로 본 결과, 2026-09-10 실측) 확인한 것:
  - `coverage` — 문제가 있으면 `coverage_checks()` 가 `raise SystemExit(1)` 로 **종료 코드**로
    알린다. DB 도 외부 바이너리도 안 쓴다. 여기서 미는 것이 이것이다.
  - `sql` — 마찬가지로 `assert` 가 실패하면(잡히지 않은 `AssertionError`) 종료 코드 1 이지만,
    실행 자체가 `psql` CLI 바이너리를 요구한다. 이 개발 환경 PATH 에는 `psql` 이 없다
    (`which psql` → not found, 2026-09-10). 로컬에서 항상 실패하므로 여기서는 안 돈다.
  - `windows` — 역시 `assert`/종료 코드 계약이고 DB 는 필요 없지만, git/cmd/powershell
    서브프로세스를 여러 벌 띄워 무겁고, 이 브랜치가 실제로 낸 부채(검증 **커버리지** 누락 —
    #273, #292)와는 무관한 영역(워크플로우 스크립트 리터럴)을 잰다. 여기서는 안 진다.

그래서 이 파일은 `coverage` 만 pytest 로 옮긴다 — "모든 `db/migrations/*.sql` 은
`verify_*.sql` 을 갖고, 그 verify 는 `RAISE EXCEPTION` 을 갖고, `CHECKS` 에 등록돼야 한다"는
규칙이 실제로 매 pytest 실행마다 확인되게 하는 것이 이 파일의 유일한 목적이다.
"""
import subprocess
import sys
from pathlib import Path

import pytest

# 이 파일은 backend/tests/ 에 있고 하네스는 저장소 루트의 tools/ 에 있다. pytest 는
# backend/ 에서 도는데(루트 CLAUDE.md), `../..` 같은 상대 홉은 실행 위치가 바뀌면 깨진다 —
# 그래서 이 파일 자신의 절대 경로에서 거슬러 올라간다: tests/ -> backend/ -> 저장소 루트.
REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "tools" / "check_migration_verification.py"

# 지금 존재하는 것 중 하나를 골라 "짝을 잃으면 잡히는가"를 증명한다. 목록에서 사라지면
# 이 테스트가 알려 주도록 존재 여부를 함께 단언한다.
FIXTURE_MIGRATION = "2026-09-09_pet_members"


def _run_coverage() -> subprocess.CompletedProcess:
    """하네스를 서브프로세스로 부른다. 셸을 거치지 않고(list argv, `shell=True` 아님) 절대
    경로로 스크립트를 지정하므로 pytest 가 어디서 돌든(backend/ 든 저장소 루트든) 안전하다.
    """
    return subprocess.run(
        [sys.executable, str(HARNESS), "coverage"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_coverage_passes_on_current_migrations():
    """`coverage` 는 구멍이 없으면 종료 코드 0 이다.

    이건 프린트를 파싱하는 게 아니라 **종료 코드 계약**이다 — `coverage_checks()` 안에서
    문제가 있을 때만 `raise SystemExit(1)` 이 나오고, 없으면 함수가 그냥 끝나 프로세스가
    0 으로 종료한다 (`tools/check_migration_verification.py` L889-916). 그래서 여기서는
    출력 문자열이 아니라 `returncode` 만 본다.
    """
    result = _run_coverage()
    assert result.returncode == 0, result.stdout + result.stderr


def test_coverage_fails_when_a_migration_loses_its_verifier():
    """하네스가 실제로 구멍을 잡는지 증명한다 — 못 잡으면 이 브랜치가 없애려던 것과 같은
    부채(#273, #292: 목록을 사람이 관리하다 빠뜨림)가 테스트 형태로 재발한 것이다.

    `verify_2026-09-09_pet_members.sql` 을 잠깐 지우고 같은 서브커맨드를 다시 불러
    종료 코드가 0 이 아닌지, 그리고 그 파일 이름이 출력에 나오는지 본다. `finally` 로
    원본 바이트를 반드시 복원한다 — 어서션이 실패해도 저장소에 지운 채로 남기지 않는다.
    """
    target = REPO_ROOT / "db" / "migrations" / f"verify_{FIXTURE_MIGRATION}.sql"
    assert target.exists(), "fixture migration verifier moved — update FIXTURE_MIGRATION"
    backup = target.read_bytes()
    target.unlink()
    try:
        result = _run_coverage()
        assert result.returncode != 0
        assert FIXTURE_MIGRATION in result.stdout
    finally:
        target.write_bytes(backup)


def _run_sql() -> subprocess.CompletedProcess:
    """하네스를 서브프로세스로 부른다. 같은 방식으로 PGHOST 등 libpq 환경 변수를 넘긴다.

    마이그레이션 검증 SQL 을 일회용 PostgreSQL DB 에 적용하고 의도적으로 망가뜨려
    verify 파일이 실제로 그 망가짐을 종료 코드로 잡는지를 본다.
    """
    import os
    import copy

    env = copy.deepcopy(os.environ)
    # sql_checks() 는 PGHOST 를 명시적으로 확인한다 (tools/check_migration_verification.py L834).
    # loopback DB 를 요구하고, psql 바이너리로 libpq 환경 변수를 읽는다.
    env.update({
        'PGHOST': '127.0.0.1',
        'PGPORT': '55432',  # 개발 PC 로컬 postgres:postgres@127.0.0.1:55432/vectordb
        'PGUSER': 'postgres',
        'PGPASSWORD': 'postgres',
        'PGDATABASE': 'vectordb',
    })
    return subprocess.run(
        [sys.executable, str(HARNESS), "sql"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def test_sql_mutation_testing_skips_when_psql_unavailable():
    """mutation-testing 하네스(`sql` 서브커맨드)는 `psql` CLI 바이너리를 요구한다.

    이 바이너리는 이 개발 환경 PATH 에 없다(`which psql` → not found, 2026-09-10).
    그러면 하네스는 실행 자체를 못 한다. 이 테스트는 **이 로컬 환경에서 스킵된다**.

    테스트가 실행되려면:
    - Windows: PostgreSQL 공식 배포판을 설치하거나(`C:\\Program Files\\PostgreSQL\\bin\\psql`)
      `psql` 을 PATH 에 놓아야 합니다.
    - Linux: `postgresql-client` 패키지를 설치하세요.
    - macOS: `brew install postgresql` 등.

    따라서 이 테스트는 **CI/CD 또는 로컬에 psql 이 설치된 환경에서만 실행됩니다.**
    로컬 테스트 흐름(`uv run pytest`)에서는 스킵하는 것이 정상입니다.
    """
    import shutil

    if shutil.which('psql') is None:
        pytest.skip("psql 바이너리가 PATH 에 없음 — mutation-testing 하네스 실행 불가")

    result = _run_sql()
    assert result.returncode == 0, result.stdout + result.stderr
