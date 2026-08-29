from app.core.config import settings
from app.usage.composition import route_capability_problems, route_provider
from app.usage.registry import usage_gate


def clear_composition() -> None:
    route_provider.cache_clear()
    usage_gate.cache_clear()


def test_tmap_configuration_fails_closed_when_key_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "walk_route_provider", "tmap")
    monkeypatch.setattr(settings, "tmap_app_key", "")
    clear_composition()

    try:
        assert route_capability_problems() == ["walk: 'tmap' provider key is missing"]
    finally:
        clear_composition()


def test_fake_and_none_need_no_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "walk_route_provider", "fake")
    monkeypatch.setattr(settings, "car_route_provider", "none")
    monkeypatch.setattr(settings, "transit_route_provider", "fake")
    clear_composition()

    try:
        assert route_capability_problems() == []
    finally:
        clear_composition()
