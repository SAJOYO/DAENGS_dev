"""Render stored writer failures without losing response-validation diagnostics."""


def call_status(result, call):
    failed = result["status"] == "failed"
    return {
        "stage": result["stage"],
        "task_id": result["task_id"],
        "status": result["status"],
        "failure_stage": (
            result.get("failure_phase")
            or ("provider" if call.get("status") == "failed" else "response")
        ) if failed else None,
        "error_type": result.get("error_type") or call.get("error_type"),
        "http_status": result.get("http_status") or call.get("code"),
        "raw": result.get("raw_text"),
        "reused": call.get("cached", False),
    }
