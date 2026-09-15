"""One budget and one pacing boundary for space, action, review and title calls."""

import asyncio
import time


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
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ):
        if minimum_interval_s < 0 or max_calls < 0:
            raise ValueError("invalid call policy")
        self.send = send
        self.minimum_interval_s = minimum_interval_s
        self.max_calls = max_calls
        self.clock, self.sleep = clock, sleep
        self.calls = 0
        self.stopped = False
        self.last_completed = None
        self.lock = asyncio.Lock()
        self.trace = []

    async def __call__(self, stage, payload, schema):
        async with self.lock:
            if self.stopped:
                raise CallsStopped("provider rate limit stopped this run")
            if self.calls >= self.max_calls:
                raise CallBudgetExceeded("model call budget exhausted")
            if self.last_completed is not None:
                delay = self.minimum_interval_s - (self.clock() - self.last_completed)
                if delay > 0:
                    await self.sleep(delay)
            self.calls += 1
            entry = {"stage": stage, "started_s": self.clock(), "status": "started"}
            self.trace.append(entry)
            try:
                result = await self.send(stage, payload, schema)
                entry["status"] = "returned"
                return result
            except ProviderFailure as exc:
                entry.update(status="failed", http_status=exc.code)
                if exc.code == 429:
                    self.stopped = True
                raise
            except Exception as exc:
                entry.update(status="failed", error_type=type(exc).__name__)
                raise
            finally:
                self.last_completed = self.clock()
                entry["completed_s"] = self.last_completed
