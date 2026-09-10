"""Activation refuses missing prerequisites without logging credentials or writing records."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from daengs_backend.cli import walk_runtime_check as runtime
from daengs_backend.config import settings


@pytest.fixture
def ready(monkeypatch):
    for key in runtime.FLAGS:
        monkeypatch.setattr(settings, key, True)
    for key in runtime.KEYS:
        monkeypatch.setattr(settings, key, SecretStr("private-test-credential"))
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    broker = AsyncMock()
    broker.__aenter__.return_value = broker
    broker.ping.return_value = True
    monkeypatch.setattr(runtime.Redis, "from_url", lambda *_args, **_kwargs: broker)
    monkeypatch.setattr(runtime, "verify_schema", AsyncMock(return_value=list(runtime.VERIFIERS)))
    monkeypatch.setattr(
        runtime, "catalogs", lambda _point: {"park": {}, "commerce": {}, "river": {}}
    )
    return SimpleNamespace(
        startup=False,
        allow_disabled=False,
        probe_address=False,
        point={"lat": 37.4878, "lng": 127.052},
        schema_dir=Path("unused"),
    )


async def test_ready_checks_every_dependency_without_leaking_keys(ready):
    report = await runtime.check(ready)
    assert report["ready"]
    assert set(report["checks"]) == {
        "flags",
        "keys_present",
        "catalogs",
        "schema",
        "broker",
        "catalog_refresh_enabled",
    }
    assert "private-test-credential" not in json.dumps(report)


@pytest.mark.parametrize("flag", runtime.FLAGS)
async def test_disabled_feature_cannot_activate(ready, monkeypatch, flag):
    monkeypatch.setattr(settings, flag, False)
    report = await runtime.check(ready)
    assert not report["ready"]
    assert report["errors"]["flags"] == "required_flags_disabled"
    ready.allow_disabled = True
    assert (await runtime.check(ready))["ready"]


async def test_disabled_preparation_still_checks_cache_and_redacts_failures(ready, monkeypatch):
    def corrupt(_point):
        raise ValueError("bad file including private-test-credential")

    ready.allow_disabled = True
    monkeypatch.setattr(runtime, "catalogs", corrupt)
    report = await runtime.check(ready)
    assert not report["ready"]
    assert report["errors"]["catalogs"] == "ValueError"
    assert "private-test-credential" not in json.dumps(report)


async def test_restart_can_drain_jobs_after_catalog_expiry(ready, monkeypatch):
    def expired(_point):
        raise ValueError("expired cache")

    monkeypatch.setattr(runtime, "catalogs", expired)
    ready.startup = True
    report = await runtime.check(ready)
    assert report["ready"]
    assert "catalogs" not in report["checks"]


@pytest.mark.parametrize("dependency", ["schema", "broker", "sgis"])
async def test_dependency_failure_blocks_activation(ready, monkeypatch, dependency):
    failure = RuntimeError("private-test-credential")
    if dependency == "schema":
        runtime.verify_schema.side_effect = failure
    elif dependency == "broker":
        monkeypatch.setattr(
            runtime.Redis, "from_url", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure)
        )
    else:
        ready.probe_address = True
        monkeypatch.setattr(runtime.sgis, "address", AsyncMock(side_effect=failure))
    report = await runtime.check(ready)
    assert not report["ready"]
    assert report["errors"][dependency] == "RuntimeError"
    assert "private-test-credential" not in json.dumps(report)
