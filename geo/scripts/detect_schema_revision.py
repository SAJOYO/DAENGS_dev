"""이 DB 가 어디까지 적용됐는지 보고, 어떤 명령을 쳐야 하는지 알려준다.

    uv run python -m scripts.detect_schema_revision

alembic 도입 전부터 쓰던 DB 를 head 로 일괄 stamp 하면 안 되기 때문에 있다 —
근거는 app/core/schema_revision.py 를 참고.

종료코드: 0 = 안내대로 하면 됨, 1 = 자동 판별 불가(사람이 봐야 함).
"""

import sys

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.schema_revision import HEAD, Detection, LegacyMarker, detect

EXISTS_TABLE = text("SELECT to_regclass('public.' || :table) IS NOT NULL")
EXISTS_COLUMN = text(
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
    " WHERE table_schema = 'public' AND table_name = :table AND column_name = :column)"
)
EXISTS_CONSTRAINT = text(
    "SELECT EXISTS (SELECT 1 FROM information_schema.table_constraints"
    " WHERE table_schema = 'public' AND table_name = :table"
    " AND constraint_name = :constraint)"
)


def _report(detection: Detection) -> int:
    if detection.out_of_order:
        print("자동 판별 불가 — 빠진 것 뒤에 이미 존재하는 스키마가 있다:")
        for marker in detection.out_of_order:
            print(f"  {marker.revision} {marker.source}: {marker} 존재")
        print("\n손으로 적용했거나 revert 된 흔적이다. 무엇이 맞는지 정한 뒤 그 지점에 stamp 한다.")
        return 1

    if detection.stamp_at is None:
        print("빈 DB — stamp 하지 말고 그냥 만들면 된다:\n")
        print("  uv run alembic upgrade head")
        return 0

    if detection.up_to_date:
        print(f"스키마가 최신({HEAD})이다. 한 번만:\n")
        print(f"  uv run alembic stamp {HEAD}")
        return 0

    print(f"{detection.stamp_at} 까지 적용돼 있다. 아직 없는 것:")
    for marker in detection.missing:
        print(f"  {marker.revision} {marker.source}: {marker} 없음")
    print("\nstamp 한 뒤 나머지를 실제로 적용한다:\n")
    print(f"  uv run alembic stamp {detection.stamp_at}")
    print("  uv run alembic upgrade head")
    return 0


def _report_recorded(recorded: str | None, detection: Detection) -> int:
    """이미 alembic 이 관리하는 DB. 기록과 실제 스키마가 어긋나면 그게 제일 위험하다."""
    if not detection.safe:
        print(f"alembic_version = {recorded} 인데 스키마에 구멍이 있다:")
        for marker in detection.out_of_order:
            print(f"  {marker.revision} {marker.source}: {marker} 존재")
        print("\n손으로 봐야 한다.")
        return 1

    if recorded == detection.stamp_at:
        print(f"일치한다 (alembic_version = {recorded}).")
        if detection.missing:
            print("\n남은 것을 적용하려면:\n\n  uv run alembic upgrade head")
        return 0

    print(f"기록과 실제가 다르다 — alembic_version = {recorded}, 스키마는 {detection.stamp_at}.")
    print("기록이 뒤처져 있으면 upgrade 가 이미 적용된 마이그레이션을 다시 돌린다.")
    print("008 의 백필은 멱등이 아니라서 walk_facts.record_version 이 3에서 2로 내려간다.\n")
    print(f"실제 스키마에 맞춘다:\n\n  uv run alembic stamp {detection.stamp_at}")
    if detection.missing:
        print("  uv run alembic upgrade head")
    return 1


def main() -> int:
    engine = create_engine(settings.database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            def present(marker: LegacyMarker) -> bool:
                if marker.table is None:
                    raise AssertionError("데이터 전용 리비전은 detect 가 묻지 않는다")
                if marker.column is not None:
                    return bool(
                        connection.execute(
                            EXISTS_COLUMN, {"table": marker.table, "column": marker.column}
                        ).scalar()
                    )
                if marker.constraint is not None:
                    return bool(
                        connection.execute(
                            EXISTS_CONSTRAINT,
                            {"table": marker.table, "constraint": marker.constraint},
                        ).scalar()
                    )
                return bool(connection.execute(EXISTS_TABLE, {"table": marker.table}).scalar())

            detection = detect(present)

            if connection.execute(EXISTS_TABLE, {"table": "alembic_version"}).scalar():
                recorded = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                return _report_recorded(recorded, detection)

            return _report(detection)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
