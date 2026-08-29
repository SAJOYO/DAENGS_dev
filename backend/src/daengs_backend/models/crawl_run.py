"""크롤 실행 이력. 원본 스키마는 `db/init/04_crawl_runs.sql` 입니다.

쓰는 쪽은 이 앱이 아니라 Celery 워커(`daengs_life.tasks.crawl_runs`)입니다.
여기서는 **읽기만** 합니다 — 관리자 화면이 "소스별 마지막 실행"을 보여 주는 자리입니다.
"""

from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, CheckConstraint, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

#: CHECK 제약과 같은 값입니다. SQL 쪽을 고치면 여기도 고쳐야 합니다 (RAG-047).
CRAWL_TRIGGERS = ("due", "manual")

#: `unavailable` 이 `failed` 와 따로 있는 것이 이 표의 요점입니다 — 키 미설정·시드 URL
#: 사망은 실패가 아니라 **사람이 고쳐야 하는 것**이라 화면에서 달라야 합니다.
#: `running` 은 워커가 중간에 죽으면 남는 상태이고, 그 자체가 정보입니다.
CRAWL_STATUSES = ("running", "ok", "failed", "unavailable")


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    __table_args__ = (
        CheckConstraint("trigger IN ('due','manual')", name="crawl_runs_trigger_check"),
        CheckConstraint("status IN ('running','ok','failed','unavailable')",
                        name="crawl_runs_status_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    #: crawler 가 붙인 실행 id. `data/manifests/crawl_log.jsonl` 과 맞물리는 다리입니다.
    #: 수집이 끝나야 나오므로 `running` 인 동안에는 비어 있습니다.
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_id: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)

    docs_fetched: Mapped[int] = mapped_column(Integer)
    docs_changed: Mapped[int] = mapped_column(Integer)
    docs_failed: Mapped[int] = mapped_column(Integer)
    docs_skipped: Mapped[int] = mapped_column(Integer)

    #: 바뀐 문서 slug. C3(개정 감지)의 입력입니다 — 화면에서는 건수만 보여도 됩니다.
    changed_slugs: Mapped[list[str]] = mapped_column(ARRAY(Text))

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
