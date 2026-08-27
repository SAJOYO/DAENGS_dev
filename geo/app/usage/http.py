"""Usage Gate 거부를 명시적인 HTTP 계약으로 번역한다."""

from fastapi import HTTPException

from app.usage.models import UsageDenied


def usage_http_exception(exc: UsageDenied) -> HTTPException:
    if exc.code == "policy_denied":
        status = 403
    elif exc.code in ("request_limit", "usage_limit"):
        status = 429
    else:
        status = 503
    headers = {"Retry-After": str(exc.retry_after_s)} if exc.retry_after_s else None
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": exc.reason},
        headers=headers,
    )
