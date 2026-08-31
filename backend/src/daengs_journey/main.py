from fastapi import FastAPI

from daengs_journey.api import router
from daengs_journey.core.config import settings
from daengs_journey.usage.composition import route_capability_problems
from daengs_journey.usage.gate import usage_request_scope

problems = route_capability_problems()
if problems:
    raise RuntimeError("route provider configuration: " + " / ".join(problems))

app = FastAPI(title="DAENGS Journey", version="0.1.0")
app.include_router(router)


@app.middleware("http")
async def bind_usage_request_scope(request, call_next):
    async with usage_request_scope():
        return await call_next(request)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "walk_route_provider": settings.walk_route_provider,
        "usage_policy": settings.usage_policy,
    }
