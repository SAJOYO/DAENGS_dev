import pytest

from daengs_backend.services.cardimage import judge


def test_parse_valid_json():
    r = judge.parse_judge_json('{"likeness": 4, "text_ok": true, "avatar_ok": false, "note": "ears differ"}')
    assert r == judge.JudgeResult(likeness=4, text_ok=True, avatar_ok=False, note="ears differ")


def test_parse_clamps_nothing_and_rejects_out_of_range():
    with pytest.raises(judge.JudgeError):
        judge.parse_judge_json('{"likeness": 9, "text_ok": true, "avatar_ok": true, "note": ""}')


def test_parse_rejects_missing_field():
    with pytest.raises(judge.JudgeError):
        judge.parse_judge_json('{"likeness": 3}')


def test_parse_rejects_non_json():
    with pytest.raises(judge.JudgeError):
        judge.parse_judge_json("I think it looks similar")


def test_prompt_asks_for_json_only():
    assert '"likeness"' in judge.PROMPT and "JSON" in judge.PROMPT
