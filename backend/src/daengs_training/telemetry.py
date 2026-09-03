"""Training stage timings — logs only, never payload (D-037).

One Training capability run gets one random, opaque ``trace_id``.  Every timing record for
that run carries it, so a single request can be reassembled from the log stream.  The id is
not derived from the user, the pet, or the question, and it is not returned in any response.

What a record may contain: ``event`` · ``trace_id`` · a duration in milliseconds ·
``runtime_state`` · ``outcome`` · ``result_status`` · a canonical reason ``code`` · an
exception *class name*.  What it may never contain: the question, the prompt, the answer,
retrieved chunks, identifiers of people or pets, or provider payloads.  ``_SAFE_FIELDS``
enforces that shape — an unknown field name is a programming error, not a log line.

The trace travels in a ``ContextVar``.  ``asyncio.to_thread`` copies the current context
into the worker thread, so a trace started in the adapter coroutine is visible inside the
blocking ``RAGService.answer`` call without threading it through every signature.  Code
that runs with no active trace (a unit test of the retriever, a CLI probe) gets a no-op
trace and emits nothing.

Why this module installs its own stream handler: the backend runs under uvicorn's default
logging config, which gives the root logger no handler and leaves it at WARNING.  An INFO
record from any ``daengs_*`` logger is therefore dropped before it reaches ``docker logs``
— that is why the historical ``training_rag completed`` line could not separate stages.
The handler is attached lazily, to this logger only, and only when nothing in the logger
hierarchy would otherwise handle the record.  This is the smallest change that makes the
timings observable without touching the application entrypoint.
"""

from __future__ import annotations

import logging
import secrets
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("daengs_training.telemetry")
LOGGER.setLevel(logging.INFO)

#: Every stage name this module emits, in request order.
EVENT_ADAPTER_TOTAL = "training_adapter_total"
EVENT_RUNTIME = "training_runtime"
EVENT_LOCK_WAIT = "training_lock_wait"
EVENT_EMBEDDING = "training_embedding"
EVENT_PGVECTOR = "training_pgvector"
EVENT_GENERATION = "training_generation"
EVENT_FINAL = "training_final"

#: The only keys a timing record may carry (besides ``event`` and ``trace_id``).
_SAFE_FIELDS = frozenset(
    {
        "duration_ms",
        "wait_ms",
        "connect_ms",
        "query_ms",
        "runtime_state",
        "outcome",
        "error_type",
        "result_status",
        "code",
        "elapsed_ms",
    }
)


def _ensure_handler() -> None:
    """Attach a stderr handler if no handler anywhere in the hierarchy would take the record."""
    if LOGGER.hasHandlers():
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    LOGGER.addHandler(handler)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)


@dataclass
class TrainingTrace:
    """One capability run.  ``runtime_created`` is set by the runtime factory body itself."""

    trace_id: str = field(default_factory=lambda: secrets.token_hex(8))
    runtime_created: bool = False
    enabled: bool = True

    def emit(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        unknown = set(fields) - _SAFE_FIELDS
        if unknown:
            raise ValueError(f"training telemetry field not allowed: {sorted(unknown)}")
        _ensure_handler()
        parts = [f"event={event}", f"trace_id={self.trace_id}"]
        parts.extend(f"{key}={value}" for key, value in fields.items())
        LOGGER.info(
            " ".join(parts),
            extra={"training_event": {"event": event, "trace_id": self.trace_id, **fields}},
        )

    @contextmanager
    def stage(self, event: str, **fields: Any) -> Iterator[dict[str, Any]]:
        """Time a block and emit one record on exit.

        The yielded dict lets the block add fields (``runtime_state``, ``connect_ms``) before
        the record goes out.  An exception inside the block still emits the record, with
        ``outcome=error`` and the exception class name, then propagates unchanged.
        """
        extra: dict[str, Any] = dict(fields)
        started = time.perf_counter()
        try:
            yield extra
        except BaseException as exc:
            extra.setdefault("outcome", "error")
            extra.setdefault("error_type", type(exc).__name__)
            raise
        finally:
            self.emit(event, duration_ms=_elapsed_ms(started), **extra)


_NULL_TRACE = TrainingTrace(trace_id="", enabled=False)
_CURRENT: ContextVar[TrainingTrace | None] = ContextVar("daengs_training_trace", default=None)


def current_trace() -> TrainingTrace:
    """The active trace, or a no-op one when nothing started a run."""
    return _CURRENT.get() or _NULL_TRACE


@contextmanager
def training_trace() -> Iterator[TrainingTrace]:
    """Start a run.  Nested use joins the outer run instead of minting a second id."""
    existing = _CURRENT.get()
    if existing is not None:
        yield existing
        return
    trace = TrainingTrace()
    token = _CURRENT.set(trace)
    try:
        yield trace
    finally:
        _CURRENT.reset(token)


__all__ = [
    "EVENT_ADAPTER_TOTAL",
    "EVENT_EMBEDDING",
    "EVENT_FINAL",
    "EVENT_GENERATION",
    "EVENT_LOCK_WAIT",
    "EVENT_PGVECTOR",
    "EVENT_RUNTIME",
    "LOGGER",
    "TrainingTrace",
    "current_trace",
    "training_trace",
]
