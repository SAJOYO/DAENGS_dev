"""alembic 리비전이 품고 있는 옛 `.sql` 본문을 실행할 때 쓰는 헬퍼.

앱 런타임은 import 하지 않는다. `alembic/` 은 패키지가 아니라 리비전 파일끼리 공용 모듈을
import 할 수 없어서, 이미 sys.path 에 있고 컨테이너에도 들어가는 여기에 둔다.
"""

from alembic import context, op


def run_legacy_sql(name: str, sql: str) -> None:
    """리비전이 들고 있는 `sql` 을 그대로 실행한다.

    `name` 은 **출처 라벨**이다 — 파일을 읽지 않는다. 옛 `migrations/*.sql` 은 리비전 안에
    통째로 들어 있어서 두 벌이 되므로 지웠고, 원문은 git history 에 있다
    (migrations/README.md). 실패 메시지와 `--sql` 출력에서 어느 변경인지 가리키는 데만 쓴다.

    online 은 드라이버 커서에 직접 넣는다. `op.execute` / `exec_driver_sql` 은 빈 파라미터를
    함께 넘기고, 그러면 psycopg 가 SQL 안의 `%` 를 자리표시자로 읽어 `011_anchor` 주석의
    `3.5%` 에서 죽는다. 파일 하나에 든 여러 문장을 한 번에 보내는 것도 이 경로에서만 된다.

    offline(`--sql`)은 실행할 연결이 없으니 스크립트에 그대로 찍는다.
    """
    if context.is_offline_mode():
        op.get_context().impl.static_output(f"-- migrations/{name}{chr(10)}{sql}")
        return

    raw = op.get_bind().connection.driver_connection
    try:
        with raw.cursor() as cursor:
            cursor.execute(sql)
    except Exception as exc:
        raise RuntimeError(f"legacy SQL 실패: {name}") from exc
