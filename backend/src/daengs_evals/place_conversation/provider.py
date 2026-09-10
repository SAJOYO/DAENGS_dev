"""Observe the production provider without recording authentication or changing its prompt."""

import asyncio
from time import perf_counter

from daengs_place.place.providers.conversation_gemini import GeminiConversation


class ObservedGemini(GeminiConversation):
    def __init__(self, *args, interval=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []
        self.plans = []
        self.drafts = []
        self.interval = interval
        self.last_started = 0

    async def _call(self, payload):
        await asyncio.sleep(max(0, self.interval - (perf_counter() - self.last_started)))
        self.last_started = perf_counter()
        record = {"request": payload}
        self.calls.append(record)
        started = perf_counter()
        try:
            result = await super()._call(payload)
            record["response"] = result
            return result
        except Exception as error:
            # Exception strings/chains may contain headers or provider details.
            record["error_type"] = type(error).__name__
            response = getattr(error.__cause__, "response", None)
            if response is not None:
                record["http_status"] = response.status_code
                try:
                    record["provider_status"] = response.json().get("error", {}).get("status")
                except ValueError:
                    pass
            raise
        finally:
            record["latency_ms"] = round((perf_counter() - started) * 1000)

    async def plan(self, request):
        plan = await super().plan(request)
        self.plans.append(plan.model_dump(mode="json"))
        return plan

    async def decide_pending(self, request):
        decision = await super().decide_pending(request)
        # Raw classifier requests/responses are retained in calls; it is not a new filter plan.
        return decision
