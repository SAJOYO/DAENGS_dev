"""Exercise the evaluator's evidence boundary, accounting, resume and review ownership."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from daengs_evals.place_conversation.judge import (
    Calls,
    JudgeSettings,
    ProviderResult,
    check_anchors,
    create_run,
    judge_directory,
    key_from_file,
    locked,
    openai_provider,
    read_judgments,
    score,
    verify_run,
)
from daengs_evals.place_conversation.judge_anchors import DEFAULT_ANCHORS, load_anchors
from daengs_evals.place_conversation.judge_contract import (
    Evidence,
    Verdict,
    append_jsonl,
    read_jsonl,
    validate_evidence,
    write_json,
)
from daengs_evals.place_conversation.judge_rubric import build_inputs, prompt
from daengs_evals.place_conversation.report import judge_report, summarize


def observation(**updates):
    return {
        "case_id": "search",
        "variant": "baseline",
        "repetition": 1,
        "turn": 1,
        "query": "주차 가능한 카페만 찾아줘",
        "status": "review_required",
        "before": {"filters": {"candidate_kinds": ["cafe"]}, "selected": None},
        "prepared": {
            "state": {"filters": {"candidate_kinds": ["cafe"]}},
            "receipt": {"execution": "searched", "returned_count": 2},
        },
        "plans": [{"kind": "facility_action", "goal": "show"}],
        "served_answer": {"text": "카페 2곳 찾았어요."},
        "checks": [{"criterion": "preserve", "status": "pass"}],
        **updates,
    }


@pytest.fixture
def run(tmp_path):
    write_json(tmp_path / "metadata.json", {"model": "fake-subject", "boundary": "synthetic"})
    write_json(tmp_path / "cases.json", [{"expect": "ORACLE_LABEL"}])
    append_jsonl(tmp_path / "observations.jsonl", observation())
    return tmp_path


def fake_generate(item, model):
    expected = {a.id: a.expected for a in load_anchors(DEFAULT_ANCHORS)}
    status = expected.get(item.key.case_id, "pass")
    return ProviderResult(
        verdict=Verdict(
            evidence=[Evidence(path="/query", observation="fixture observation")],
            rationale="Deterministic test provider, not a semantic experiment.",
            status=status,
        ),
        usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    )


def ready(run, cap=40):
    directory = create_run(run, "test", "fake-judge", DEFAULT_ANCHORS)
    calls = Calls(directory, fake_generate, "fake-judge", cap, retries=0, retry_delay=0)
    assert check_anchors(directory, DEFAULT_ANCHORS, calls)["passed"]
    return directory, calls


def test_inputs_hide_oracles_provider_credentials_and_other_repetitions():
    first = observation(expect="ORACLE", review="HUMAN", provider_calls=[{"key": "SECRET"}])
    first["before"]["history"] = [{"query": "must not use fabricated history"}]
    second = observation(turn=2, query="음식점은 빼줘")
    other = observation(repetition=2, query="UNRELATED")
    rows = build_inputs([first, other, second])
    current = next(i for i in rows if i.key.turn == 2 and i.axis == "intent_alignment")
    raw = json.dumps(current.payload)
    for token in ("ORACLE", "HUMAN", "SECRET", "UNRELATED", "fabricated", "checks"):
        assert token not in raw
    assert current.payload["previous_turns"][0]["query"] == first["query"]
    faithfulness = next(i for i in rows if i.axis == "result_faithfulness")
    assert "plans" not in faithfulness.payload
    assert first["before"]["history"]  # projection never edits source


@pytest.mark.parametrize("updates", [{"status": "blocked"}, {"turn": 2}, {"status": "not_run"}])
def test_missing_execution_or_context_is_unmeasured_without_calls(run, updates):
    items = build_inputs([observation(**updates)])
    calls = Calls(run, lambda *_: pytest.fail("must not call"), "fake", 1)
    assert all(calls.judge(item, "score").status == "unmeasured" for item in items)
    assert calls.count() == 0


def test_payload_overflow_is_not_silently_truncated():
    item = build_inputs([observation(query="x" * 61_000)])[0]
    assert "limit" in item.unavailable_reason


def test_evidence_paths_and_nonempty_verdict_are_enforced():
    with pytest.raises(ValueError, match="requires observed evidence"):
        Verdict(evidence=[], rationale="looks fine", status="pass")
    verdict = Verdict(
        evidence=[Evidence(path="/prepared/member_saved", observation="yes")],
        rationale="done",
        status="pass",
    )
    with pytest.raises(ValueError, match="outside"):
        validate_evidence(verdict, {"prepared": {"receipt": {}}})


def test_duplicates_and_directory_escape_are_rejected(run):
    with pytest.raises(ValueError, match="duplicate"):
        build_inputs([observation(), observation()])
    for name in ("../other", "a/b", "C:\\temp", ".."):
        with pytest.raises(ValueError):
            judge_directory(run, name)


def test_failed_anchor_blocks_scoring(run):
    directory = create_run(run, "failed", "fake", DEFAULT_ANCHORS)

    def all_pass(item, model):
        result = fake_generate(item, model)
        result.verdict.status = "pass"
        return result

    calls = Calls(directory, all_pass, "fake", 30, retries=0)
    assert not check_anchors(directory, DEFAULT_ANCHORS, calls)["passed"]
    with pytest.raises(ValueError, match="anchor pass"):
        score(directory, calls)
    report = judge_report(run, "failed")
    assert report["anchor_passed"] is False
    assert report["rows"][0]["judge_axes"]["scope_fit"]["status"] == "not_run"


def test_budget_counts_anchors_retries_and_resume_without_recalling_completed(run):
    anchor_count = len(load_anchors(DEFAULT_ANCHORS))
    directory, calls = ready(run, cap=anchor_count + 1)
    score(directory, calls)
    assert calls.count() == anchor_count + 1
    statuses = [r.status for r in read_judgments(directory).values()]
    assert statuses == ["judged", "budget_exhausted"]
    expanded = Calls(directory, fake_generate, "fake-judge", 20, retries=0)
    score(directory, expanded, resume=True)
    assert expanded.count() == anchor_count + 3
    assert all(r.status == "judged" for r in read_judgments(directory).values())
    score(directory, expanded, resume=True)
    assert expanded.count() == anchor_count + 3
    assert any(r["status"] == "budget_exhausted" for r in read_jsonl(directory / "judgments.jsonl"))


def test_provider_errors_remain_visible_and_secrets_are_not_logged(run):
    item = build_inputs([observation()])[0]
    attempts = 0

    def flaky(current, model):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Authorization: SECRET-KEY")
        return fake_generate(current, model)

    calls = Calls(run, flaky, "fake", 2, retries=1, retry_delay=0)
    row = calls.judge(item, "score")
    assert row.status == "judged" and row.attempts == 2
    text = (run / "calls.jsonl").read_text(encoding="utf-8")
    assert "RuntimeError" in text and "SECRET-KEY" not in text
    assert calls.count() == 2


def test_unfinished_attempt_consumes_budget(run):
    append_jsonl(run / "calls.jsonl", {"event": "started", "call_id": "interrupted"})
    calls = Calls(run, lambda *_: pytest.fail("budget exceeded"), "fake", 1)
    assert calls.judge(build_inputs([observation()])[0], "score").status == "budget_exhausted"


def test_authentication_error_stops_retries_and_remaining_axis_calls(run):
    class AuthenticationError(Exception):
        status_code = 401

    def unauthenticated(*_):
        raise AuthenticationError("secret key in provider message")

    calls = Calls(run, unauthenticated, "fake", 30, retries=2, retry_delay=0)
    rows = [calls.judge(item, "score") for item in build_inputs([observation()])]
    assert [row.attempts for row in rows] == [1, 0, 0]
    assert all(row.status == "judge_error" for row in rows)
    assert calls.count() == 1
    assert "secret key" not in (run / "calls.jsonl").read_text(encoding="utf-8")


def test_key_file_accepts_old_field_without_copying_or_printing(tmp_path, capsys):
    (tmp_path / ".env").write_text(
        'DAENGS_OPENAI_API_KEY="test-only"\ngemini: unrelated', encoding="utf-8"
    )
    assert key_from_file(tmp_path).get_secret_value() == "test-only"
    assert capsys.readouterr().out == ""
    assert len(list(tmp_path.iterdir())) == 1


def test_malformed_source_and_invalid_array_pointer_are_not_evidence():
    row = observation()
    row["prepared"]["receipt"] = {}
    assert all(item.unavailable_reason for item in build_inputs([row]))
    verdict = Verdict(
        evidence=[Evidence(path="/items/-1", observation="last")], rationale="x", status="pass"
    )
    with pytest.raises(ValueError, match="outside"):
        validate_evidence(verdict, {"items": ["only"]})


def test_report_does_not_mark_missing_code_checks_as_pass(run):
    (run / "observations.jsonl").write_text(
        json.dumps(observation(checks=[])) + "\n", encoding="utf-8"
    )
    directory, calls = ready(run)
    score(directory, calls)
    row = judge_report(run, "test")["rows"][0]
    assert row["code_status"] == "unmeasured" and row["final_status"] == "review_required"


@pytest.mark.parametrize("target", ["observations.jsonl", "cases.json", "metadata.json"])
def test_resume_rejects_changed_source(run, target):
    directory, _ = ready(run)
    with (run / target).open("a", encoding="utf-8") as output:
        output.write("\n")
    with pytest.raises(ValueError, match="source changed"):
        verify_run(run, directory, "fake-judge", DEFAULT_ANCHORS)


def test_resume_rejects_model_or_judge_input_change(run):
    directory, _ = ready(run)
    with pytest.raises(ValueError, match="settings"):
        verify_run(run, directory, "other", DEFAULT_ANCHORS)
    with (directory / "inputs.jsonl").open("a", encoding="utf-8") as output:
        output.write("\n")
    with pytest.raises(ValueError, match="inputs changed"):
        verify_run(run, directory, "fake-judge", DEFAULT_ANCHORS)


def test_completed_judgments_cannot_be_overwritten_or_attached_to_other_input(run):
    directory, calls = ready(run)
    score(directory, calls)
    with pytest.raises(ValueError, match="explicitly resume"):
        score(directory, calls)
    row = read_jsonl(directory / "judgments.jsonl")[0]
    append_jsonl(directory / "judgments.jsonl", row)
    with pytest.raises(ValueError, match="duplicate completed"):
        read_judgments(directory)


def test_lock_excludes_concurrent_judges_and_releases_after_exception(run):
    with pytest.raises(RuntimeError), locked(run):
        with pytest.raises(FileExistsError), locked(run):
            pass
        raise RuntimeError("interrupted")
    assert not (run / ".lock").exists()


def test_report_never_promotes_model_opinion_or_hides_code_failures(run):
    row = observation(status="fail", checks=[{"criterion": "wrong_filter", "status": "fail"}])
    (run / "observations.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    review = {
        **{k: row[k] for k in ("case_id", "variant", "repetition", "turn")},
        "reviewer": "Codex review",
        "faithfulness": {"status": "pass"},
        "task_completion": {"status": "pass"},
    }
    append_jsonl(run / "reviews.jsonl", review)
    originals = {
        name: (run / name).read_bytes() for name in ("reviews.jsonl", "observations.jsonl")
    }
    directory, calls = ready(run)
    score(directory, calls)
    summarize(run, judge_id="test")
    report = judge_report(run, "test")
    assert report["rows"][0]["final_status"] == "fail"
    assert report["rows"][0]["judge_axes"]["intent_alignment"]["status"] == "pass"
    assert report["usage"]["total_tokens"] == (len(load_anchors(DEFAULT_ANCHORS)) + 3) * 5
    for name, content in originals.items():
        assert (run / name).read_bytes() == content


def test_judge_pass_without_explicit_review_stays_review_required(run):
    directory, calls = ready(run)
    score(directory, calls)
    assert judge_report(run, "test")["rows"][0]["final_status"] == "review_required"
    assert not (run / "reviews.jsonl").exists()


def test_provider_uses_structured_output_without_sdk_hidden_retries(monkeypatch):
    import openai

    captured = {}
    verdict = fake_generate(build_inputs([observation()])[0], "fake").verdict

    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="completed", output_parsed=verdict, usage=None)

    def client(**kwargs):
        captured["client_options"] = kwargs
        return SimpleNamespace(responses=SimpleNamespace(parse=parse))

    monkeypatch.setattr(openai, "OpenAI", client)
    generate = openai_provider(JudgeSettings(api_key="test", _env_file=None))
    item = build_inputs([observation()])[0]
    assert generate(item, "fake").verdict == verdict
    assert captured["client_options"]["max_retries"] == 0
    assert captured["text_format"] is Verdict and captured["store"] is False
    assert captured["input"][1]["role"] == "user"
    assert "ORACLE" not in captured["input"][0]["content"]


def test_refusal_or_missing_structure_is_an_error(monkeypatch):
    import openai

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **_: SimpleNamespace(
            responses=SimpleNamespace(
                parse=lambda **_: SimpleNamespace(status="completed", output_parsed=None)
            )
        ),
    )
    generate = openai_provider(JudgeSettings(api_key="test", _env_file=None))
    with pytest.raises(ValueError, match="refusal"):
        generate(build_inputs([observation()])[0], "fake")


def test_settings_match_existing_env_names_without_backend_import(monkeypatch):
    monkeypatch.setenv("OPENAI_JUDGE_MODEL", "test-pinned-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = JudgeSettings(_env_file=None)
    assert settings.model == "test-pinned-model"
    assert settings.api_key.get_secret_value() == "test-key"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import daengs_evals.place_conversation.judge; "
                "assert 'daengs_backend.config' not in sys.modules"
            ),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_anchor_labels_are_never_input_and_cover_all_axes():
    for anchor in load_anchors(DEFAULT_ANCHORS):
        assert "expected" not in anchor.judge_input().model_dump()
        assert "expected" not in anchor.judge_input().payload
        assert anchor.id not in prompt(anchor.axis)


def test_versioned_cases_load_in_existing_runner():
    from daengs_evals.place_conversation.runner import DATA, read_cases

    dev = read_cases(DATA / "judge/cases.dev.v1.jsonl")
    holdout = read_cases(DATA / "judge/cases.holdout.v1.jsonl")
    assert {c["id"] for c in dev}.isdisjoint(c["id"] for c in holdout)
    assert all(c["group"] == "holdout" for c in holdout)
    assert all(step["expect"] for c in dev + holdout for step in c["steps"])
