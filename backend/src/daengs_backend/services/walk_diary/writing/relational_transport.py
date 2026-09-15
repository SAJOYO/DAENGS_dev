"""One budget and one pacing boundary for space, action, review and title calls."""

import asyncio
import time
from math import isfinite


class ProviderFailure(RuntimeError):
    def __init__(self, code=None):
        super().__init__("diary provider request failed")
        self.code = code


class CallBudgetExceeded(RuntimeError):
    pass


class CallsStopped(RuntimeError):
    pass


class CallCoordinator:
    """429 stops this run; no hidden retry and no automatic model substitution."""

    def __init__(
        self,
        send,
        *,
        minimum_interval_s=0.0,
        max_calls=64,
        call_timeout_s=None,
        total_timeout_s=None,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ):
        if (
            not isfinite(minimum_interval_s)
            or minimum_interval_s < 0
            or type(max_calls) is not int
            or max_calls < 0
        ):
            raise ValueError("invalid call policy")
        for timeout in (call_timeout_s, total_timeout_s):
            if timeout is not None and (not isfinite(timeout) or timeout <= 0):
                raise ValueError("invalid call timeout")
        self.send = send
        self.minimum_interval_s = minimum_interval_s
        self.max_calls = max_calls
        self.clock, self.sleep = clock, sleep
        self.calls = 0
        self.stopped = False
        self.last_completed = None
        self.lock = asyncio.Lock()
        self.trace = []
        self.call_timeout_s = call_timeout_s
        self.deadline = clock() + total_timeout_s if total_timeout_s is not None else None
        self.deadline_reached = False

    def remaining(self):
        if self.deadline_reached:
            raise CallsStopped("diary execution deadline reached")
        remaining = None if self.deadline is None else self.deadline - self.clock()
        if remaining is not None and remaining <= 0:
            self.deadline_reached = True
            raise CallsStopped("diary execution deadline reached")
        return remaining

    async def __call__(self, stage, payload, schema):
        async with self.lock:
            if self.stopped:
                raise CallsStopped("provider rate limit stopped this run")
            if self.calls >= self.max_calls:
                raise CallBudgetExceeded("model call budget exhausted")
            remaining = self.remaining()
            if self.last_completed is not None:
                delay = self.minimum_interval_s - (self.clock() - self.last_completed)
                if delay > 0:
                    if remaining is not None and delay >= remaining:
                        self.deadline_reached = True
                        raise CallsStopped("next call cannot start before diary deadline")
                    await self.sleep(delay)
            remaining = self.remaining()
            self.calls += 1
            entry = {"stage": stage, "started_s": self.clock(), "status": "started"}
            self.trace.append(entry)
            try:
                limits = [n for n in (remaining, self.call_timeout_s) if n is not None]
                timeout = asyncio.timeout(min(limits) if limits else None)
                total_is_limit = remaining is not None and (
                    self.call_timeout_s is None or remaining <= self.call_timeout_s
                )
                try:
                    async with timeout:
                        result = await self.send(stage, payload, schema)
                finally:
                    if timeout.expired() and total_is_limit:
                        self.deadline_reached = True
                if timeout.expired():
                    raise TimeoutError("provider suppressed timeout cancellation")
                # A provider may suppress timeout cancellation and return a late candidate.
                self.remaining()
                if (
                    self.call_timeout_s is not None
                    and self.clock() - entry["started_s"] >= self.call_timeout_s
                ):
                    raise TimeoutError("provider returned after the call deadline")
                entry["status"] = "returned"
                return result
            except asyncio.CancelledError:
                entry["status"] = "cancelled"
                raise
            except ProviderFailure as exc:
                entry.update(status="failed", http_status=exc.code)
                if exc.code == 429:
                    self.stopped = True
                raise
            except Exception as exc:
                entry.update(status="failed", error_type=type(exc).__name__)
                if self.deadline is not None and self.clock() >= self.deadline:
                    self.deadline_reached = True
                raise
            finally:
                self.last_completed = self.clock()
                entry["completed_s"] = self.last_completed
