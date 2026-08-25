"""`uv run seed-admin` — 관리자 계정의 INSERT SQL 을 만들어 줍니다.

**DB 를 건드리지 않습니다.** 화면에 SQL 을 찍어 줄 뿐이라, 여러 번 돌려도
부작용이 없습니다 (같은 비밀번호라도 salt 가 달라 매번 다른 해시가 나옵니다).

    uv run seed-admin --login-id daengs --name "댕스 관리자"
    # 비밀번호를 두 번 물어본 뒤 INSERT 문을 찍습니다. 그걸 psql 에 붙여넣으세요.

**컨테이너에서 돌릴 때는 `-it` 가 필요합니다.** getpass 는 파이프가 아니라 콘솔에서
직접 읽으므로, tty 가 없으면 입력을 기다리며 멈춥니다.

    docker compose exec -it backend uv run seed-admin --login-id daengs --name "..."

**왜 db/init/*.sql 에 넣지 않나**: Postgres 로는 Argon2id 해시를 만들 수 없습니다.
pgcrypto 의 crypt() 는 bcrypt / md5 / des 만 지원하고, 지금 켜는 확장은 vector
하나뿐입니다. SQL 파일에 넣으려면 해시를 git 에 커밋해야 하는데, 그러면 나중에
비밀번호를 바꿔도 저장소 히스토리에 옛 해시가 그대로 남습니다.

**주의**: psql 에 붙여넣으면 그 SQL 이 `~/.psql_history` 에 남습니다. 해시라 평문은
아니지만, 신경 쓰이면 출력을 파일로 받아 `psql -f seed.sql` 로 실행하고 지우세요.
"""

import argparse
import getpass
import sys

from daengs_backend.core.password import hash_password
from daengs_backend.models import ADMIN_ROLES

# 짧은 비밀번호를 막습니다. 관리자 계정이라 뚫리면 개인정보 복호화까지 열립니다.
MIN_LENGTH = 12


def _read_password() -> str:
    """비밀번호를 두 번 받아 대조합니다.

    getpass 는 입력을 화면에 찍지 않고, 인자로 받지 않으므로 셸 히스토리에도
    남지 않습니다. **--password 옵션을 만들지 마세요** — 그 순간 히스토리와
    프로세스 목록에 평문이 남습니다.
    """
    password = getpass.getpass("비밀번호: ")
    if len(password) < MIN_LENGTH:
        sys.exit(f"비밀번호는 {MIN_LENGTH}자 이상이어야 합니다.")

    if password != getpass.getpass("비밀번호 확인: "):
        # 한 번만 받으면 오타를 친 채로 계정이 만들어지고, 그걸 알아채는 시점은
        # 로그인이 안 될 때입니다. 그때는 이미 SQL 을 실행한 뒤입니다.
        sys.exit("두 번 입력한 값이 다릅니다.")
    return password


def _quote(value: str) -> str:
    """SQL 문자열 리터럴로 감쌉니다.

    이름에 작은따옴표가 들어갈 수 있습니다 (예: O'Brien). 이스케이프하지 않으면
    SQL 이 깨지고, 최악의 경우 붙여넣는 사람이 의도하지 않은 문장을 실행합니다.
    """
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="seed-admin",
        description="관리자 계정 INSERT SQL 을 만듭니다 (DB 는 건드리지 않습니다).",
    )
    parser.add_argument("--login-id", required=True, help="로그인 아이디 (이메일 아님)")
    parser.add_argument("--name", required=True, help="화면에 찍을 이름")
    parser.add_argument(
        "--role",
        default="ADMIN",
        choices=ADMIN_ROLES,
        help="권한. 기본값 ADMIN (지금 실제로 쓰는 것은 이것뿐입니다)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="새로 만들지 않고 기존 계정의 비밀번호만 바꾸는 UPDATE 문을 냅니다",
    )
    args = parser.parse_args()

    if len(args.login_id) > 50:
        sys.exit("로그인 아이디는 50자 이하여야 합니다 (login_id VARCHAR(50)).")

    digest = hash_password(_read_password())

    if args.reset:
        # INSERT 와 갈라 둔 이유는, 실수로 덮어쓰는 일이 없게 하기 위해서입니다.
        # 비밀번호 재설정은 의도가 있어야만 나오는 경로여야 합니다.
        statement = (
            "UPDATE admin_users\n"
            f"   SET password_hash = {_quote(digest)}\n"
            f" WHERE login_id = {_quote(args.login_id)};"
        )
        note = "-- 기존 계정의 비밀번호만 바꿉니다. 계정이 없으면 UPDATE 0 이 나옵니다."
    else:
        # ON CONFLICT DO NOTHING 을 붙이지 않습니다. 붙이면 두 번 붙여넣었을 때
        # 조용히 넘어가서 "비밀번호가 왜 안 바뀌지" 로 헤매게 됩니다.
        # login_id 의 UNIQUE 제약이 시끄럽게 막아 주는 편이 낫습니다.
        statement = (
            "INSERT INTO admin_users (login_id, password_hash, name, role)\n"
            f"VALUES ({_quote(args.login_id)}, {_quote(digest)}, "
            f"{_quote(args.name)}, {_quote(args.role)});"
        )
        note = "-- 같은 login_id 를 두 번 넣으면 UNIQUE 제약이 막습니다 (의도한 동작)."

    print()
    print("-- ─────────────────────────────────────────────────────────────")
    print("-- 아래 SQL 을 psql 에 붙여넣으세요.")
    print("--   docker compose exec -it pgvector psql -U postgres -d vectordb")
    print("--")
    print("-- 소유자를 맞춰야 하므로 daengs 가 아니라 postgres 로 실행합니다.")
    print("-- (ALTER DEFAULT PRIVILEGES 덕에 daengs 도 보게 됩니다)")
    print(f"-- {note[3:]}")
    print("-- ─────────────────────────────────────────────────────────────")
    print(statement)
    print()


if __name__ == "__main__":
    main()
