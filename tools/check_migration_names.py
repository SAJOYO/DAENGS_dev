"""`db/migrations/` 의 **이름과 짝**을 본다. 내용은 안 본다.

`tools/check_migration_verification.py` 와 겹치지 않는다. 저쪽은 walk 두 파일의 verify SQL 이
**실제로 스키마 훼손을 잡아내는지**(컬럼 drop · 타입 변경 · FK 제거를 일부러 해 보고 터지는지)를
본다. 여기는 그 **앞단**이다 — 파일이 규칙대로 이름 붙었고 짝이 있는지.

**왜 기계가 봐야 하나.** `.github/workflows/db-migrate.yml` 은 `verify_<파일명 그대로>` 를 찾는다.
그래서 제일 위험한 것은 verify 파일이 **없는** 경우가 아니라 **있는데 이름이 짝이 아닌** 경우다 —
폴더에 파일이 보이니 사람 눈에는 멀쩡하고, 워크플로만 못 찾는다. 실제로 2026-09-06 에
`20260905_documents_org_backfill.sql` 이 그 상태였다(짝이 `verify_2026-09-05_documents_org.sql`).
검증이 기본값 `true` 가 된 뒤로는 그런 파일이 **적용 전에 실패**하므로, 머지 전에 잡는 편이 싸다.

**이제 폴더 전체를 본다** (2026-09-07, #295). 처음에는 *"폴더 전체에 걸면 verify 짝이 없는
옛 파일 여덟 개가 첫날부터 빨간불이 된다"* 는 이유로 **PR 이 건드린 파일만** 봤는데,
**#292 가 그 여덟을 다 채워 전제가 사라졌다.** 예외 목록을 두지 않겠다는 판단은 그대로다 —
목록을 두면 사람들이 목록에 추가하는 습관이 생기고 목록 자체가 또 낡는다.

    python tools/check_migration_names.py            # 폴더 전체
    python tools/check_migration_names.py <경로...>   # 그 파일들만 (옛 사용법도 그대로 된다)

인자를 주면 그것만 본다. 안 주면 `db/migrations/*.sql` 전부를 본다.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db/migrations"

#: `db/migrations/README.md` 의 규칙. 날짜는 파일 이름으로만 쓰이고 DB 는 이것을 모른다
#: (버전 테이블이 없다 — CLAUDE.md). 그래도 순서를 사람이 읽는 유일한 단서라 형식을 지킨다.
#:
#: **하이픈이 핵심이다.** 없으면 바이트 순 정렬에서 하이픈 있는 이름 **전부의 뒤**로 가서,
#: 9월 5일 파일이 10월 1일 파일보다 뒤에 온다.
NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_[a-z0-9_]+\.sql$")


def check(paths: list[str]) -> list[str]:
    """어긋난 것을 사람이 읽을 문장으로 돌려준다. 빈 목록이면 통과다."""
    problems: list[str] = []

    for raw in paths:
        path = Path(raw)
        if path.parent.as_posix() != "db/migrations" or path.suffix != ".sql":
            continue
        # 지워진 파일은 검사하지 않는다. rename 은 지운 쪽과 만든 쪽이 같이 오므로
        # 만든 쪽만 보면 된다.
        if not (ROOT / path).exists():
            continue

        name = path.name
        base = name[len("verify_"):] if name.startswith("verify_") else name

        if not NAME.match(base):
            problems.append(
                f"{name}: 이름이 `YYYY-MM-DD_이름.sql` 이 아닙니다. "
                f"하이픈이 빠지면 날짜순 정렬이 깨집니다 (db/migrations/README.md)"
            )
            # 이름이 규칙 밖이면 짝 검사는 뜻이 없다 — 무엇과 짝지어야 할지가 정해지지 않는다.
            continue

        if name.startswith("verify_"):
            if not (MIGRATIONS / base).exists():
                problems.append(f"{name}: 짝이 될 `{base}` 가 없습니다")
            if (MIGRATIONS / name).stat().st_size == 0:
                problems.append(f"{name}: 비어 있습니다. db-migrate.yml 이 적용 전에 실패시킵니다")
            continue

        verifier = MIGRATIONS / f"verify_{name}"
        if not verifier.exists():
            problems.append(
                f"{name}: `verify_{name}` 이 없습니다. "
                f"db-migrate.yml 은 **파일명이 정확히 일치하는** verify 를 찾습니다 — "
                f"이름이 다른 verify 가 폴더에 있어도 못 찾습니다"
            )
        elif verifier.stat().st_size == 0:
            problems.append(f"verify_{name}: 비어 있습니다")

    return problems


def all_migration_files() -> list[str]:
    """폴더 전체. 인자를 안 줬을 때 이것을 본다 (#295 전에는 아무것도 안 봤다)."""
    return sorted(f"db/migrations/{path.name}" for path in MIGRATIONS.glob("*.sql"))


def main() -> int:
    problems = check(sys.argv[1:] or all_migration_files())
    if not problems:
        print("db/migrations 이름·짝 검사 통과")
        return 0
    print("db/migrations 이름·짝 검사 실패:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    print(
        "\n고치는 법은 db/migrations/README.md 를 보세요. "
        "이 검사는 **이 PR 이 건드린 파일만** 봅니다 — 옛 파일의 부채는 여기서 안 잡습니다.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
