"""크롤 관리 API 의 응답 형태. 모델(models/crawl_run.py)과 섞지 않습니다."""

from datetime import datetime

from pydantic import BaseModel, Field


class CrawlRunOut(BaseModel):
    """실행 한 건.

    `changed_slugs` 는 건수만 내보냅니다 — 목록 전체는 조례 208건처럼 길어질 수 있고,
    화면이 필요로 하는 것은 "몇 건 바뀌었나"입니다. 무엇이 바뀌었는지는 C3(#66)가
    DB 에서 직접 읽습니다.
    """

    model_config = {"from_attributes": True}

    id: int
    run_id: str | None
    source_id: str
    trigger: str
    status: str
    docs_fetched: int
    docs_changed: int
    docs_failed: int
    docs_skipped: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class CrawlStatusOut(BaseModel):
    """화면의 기본 응답 — 소스별 마지막 실행 + 아직 안 끝난 수."""

    runs: list[CrawlRunOut]
    #: 0 이 아니면 **지금 돌고 있거나, 워커가 죽어서 남았거나** 둘 중 하나입니다.
    #: 둘을 가르는 것은 이 표가 아니라 워커 상태라, 화면은 `started_at` 을 같이 보여 줍니다.
    running: int


class CrawlTriggerRequest(BaseModel):
    """수동 트리거.

    비우면 due 판정을 태스크가 합니다 — **주기 실행과 같은 경로**입니다 (RAG-001 원칙 4).
    """

    source_ids: list[str] = Field(default_factory=list, max_length=50)


class CrawlTriggerAccepted(BaseModel):
    """202 응답. **결과가 아니라 접수증입니다** — 크롤은 분 단위라 기다리지 않습니다."""

    task_id: str
    source_ids: list[str]
