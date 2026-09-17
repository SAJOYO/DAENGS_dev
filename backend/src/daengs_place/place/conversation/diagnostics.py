"""Bounded validation metadata; never log queries, argument values or exception prose."""

from pydantic import ValidationError


def validation_issues(error):
    if isinstance(error, ValidationError):
        return [
            {
                "path": "<extra>"
                if item["type"] == "extra_forbidden"
                else ".".join(str(part) for part in item["loc"])[:120],
                "code": item["type"],
            }
            for item in error.errors(include_input=False, include_context=False, include_url=False)[
                :8
            ]
        ]
    return [{"path": "", "code": type(error).__name__}]


def error_origin(error):
    trace = error.__traceback__
    while trace and trace.tb_next:
        trace = trace.tb_next
    return f"{trace.tb_frame.f_code.co_name}:{trace.tb_lineno}" if trace else "unknown"
