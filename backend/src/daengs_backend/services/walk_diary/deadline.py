"""One request deadline, inherited by independent writing tasks."""

from contextvars import ContextVar
from datetime import datetime

publication_deadline: ContextVar[datetime | None] = ContextVar("diary_deadline", default=None)
