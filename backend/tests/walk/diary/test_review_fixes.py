"""Regression coverage for historical title validation and failure reporting."""

import pytest

from daengs_walk.diary.relational.title_context import TITLE_CONTRACT, validate_title_publication
from daengs_walk.diary.relational.title_writer_view import TITLE_WRITER_POLICIES
from tools.diary_call_status import call_status


@pytest.mark.parametrize("version", [TITLE_CONTRACT, *sorted(TITLE_WRITER_POLICIES)])
def test_missing_marker_cannot_bypass_title_validation(version):
    receipt = {"title": {"request": {"version": version}, "text": "altered"}}
    with pytest.raises(ValueError, match="missing title contract marker"):
        validate_title_publication(receipt)


@pytest.mark.parametrize("error_type", ["JSONDecodeError", "ValidationError", "ValueError"])
def test_response_failure_preserves_writer_diagnostics(error_type):
    result = {"stage": "space", "task_id": "s1", "status": "failed", "failure_phase": "references",
                  "error_type": error_type, "raw_text": "candidate"}
    status = call_status(result, {"status": "returned"})
    assert status["failure_stage"] == "references"
    assert status["error_type"] == error_type
    assert status["raw"] == "candidate"
    assert status["http_status"] is None


def test_provider_failure_preserves_http_code_from_stored_result():
    result = {"stage": "space", "task_id": "s1", "status": "failed", "failure_phase": "request",
                  "error_type": "ProviderFailure", "http_status": 503}
    status = call_status(result, {})
    assert status["http_status"] == 503
    assert status["error_type"] == "ProviderFailure"
