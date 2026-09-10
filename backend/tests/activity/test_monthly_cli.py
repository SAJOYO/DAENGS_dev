"""Exercise explicit monthly activation arguments without opening a real database."""

import argparse
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from daengs_backend.cli import activity as cli
from daengs_backend.core import database
from daengs_backend.services import activity_game
from daengs_backend.services.activity_core import first_season_policy as first
from tests.activity.test_monthly_calendar import stamp


async def test_cli_uses_actual_start_and_kst_month_end(tmp_path, monkeypatch, clock, capsys):
    clock[0] = stamp("2026-09-11T12:34:56+09:00")
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(asdict(first.Rules())), "utf-8")
    token = object()

    @asynccontextmanager
    async def factory():
        yield token

    async def create(db, season_id, starts_ms, ends_ms, rules, *, monthly):
        assert db is token and monthly is True
        assert season_id == "territory-2026-09"
        assert starts_ms == clock[0]
        assert ends_ms == stamp("2026-10-01T00:00:00+09:00")
        assert rules == first.Rules()
        return SimpleNamespace(id=season_id, coverage_start_ms=starts_ms)

    monkeypatch.setattr(database, "worker_session", factory)
    monkeypatch.setattr(activity_game, "create_season", create)
    await cli.run(argparse.Namespace(command="start-monthly", rules=str(rules_path)))
    assert json.loads(capsys.readouterr().out)["season_id"] == "territory-2026-09"


def test_cli_requires_explicit_rules_and_keeps_manual_command(monkeypatch):
    monkeypatch.setattr("sys.argv", ["activity", "start-monthly"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    seen = []

    async def run(args):
        seen.append(args)

    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "activity",
            "start-season",
            "manual",
            "--starts-ms",
            "1",
            "--ends-ms",
            "2",
            "--rules",
            "rules.json",
        ],
    )
    cli.main()
    assert seen[0].command == "start-season" and seen[0].season_id == "manual"
