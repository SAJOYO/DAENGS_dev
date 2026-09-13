"""Shared adapter execution for assistant routes and diary writing graphs.

Owns bounded concurrency and invocation outcomes, never publication or domain payloads.
Deadlines include queue time. Late provider values have no adoption authority.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

_late_tasks: set[asyncio.Task] = set()


def discard(task: asyncio.Task) -> None:
    def consume(done):
        _late_tasks.discard(done)
        if not done.cancelled():
            done.exception()

    if task.done():
        consume(task)
    else:
        _late_tasks.add(task)
        task.add_done_callback(consume)
        task.cancel()


@dataclass(frozen=True)
class ExecutionResult[T]:
    job_id: str
    status: Literal["ok", "timeout", "error"]
    value: T | None = None
    error_kind: str | None = None
    elapsed_ms: int = 0


class JobExecutor:
    def __init__(self, concurrency: int = 1):
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self._semaphore = asyncio.Semaphore(concurrency)

    async def run[T](
        self,
        job_id: str,
        invoke: Callable[[], Awaitable[T]],
        *,
        deadline: float | None = None,
        timeout_ms: int | None = None,
    ) -> ExecutionResult[T]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        if timeout_ms is not None:
            cutoff = started + timeout_ms / 1000
            deadline = min(deadline, cutoff) if deadline is not None else cutoff

        async def call():
            async with self._semaphore:
                if deadline is not None and loop.time() >= deadline:
                    raise TimeoutError
                return await invoke()

        task = None
        try:
            if deadline is not None and started >= deadline:
                raise TimeoutError
            task = asyncio.create_task(call(), name=job_id)
            if deadline is None:
                value = await task
            else:
                done, _ = await asyncio.wait({task}, timeout=max(0, deadline - loop.time()))
                if not done or loop.time() >= deadline:
                    raise TimeoutError
                value = task.result()
            return ExecutionResult(
                job_id, "ok", value, elapsed_ms=int((loop.time() - started) * 1000)
            )
        except TimeoutError:
            return ExecutionResult(
                job_id, "timeout", elapsed_ms=int((loop.time() - started) * 1000)
            )
        except Exception as exc:  # noqa: BLE001 - adapter failures are isolated; cancellation propagates
            return ExecutionResult(
                job_id,
                "error",
                error_kind=type(exc).__name__,
                elapsed_ms=int((loop.time() - started) * 1000),
            )
        finally:
            if task is not None:
                discard(task)
