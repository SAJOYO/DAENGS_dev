"""Runtime router/resolver and offline judging share the same client construction."""

from types import SimpleNamespace

from pydantic import SecretStr

from daengs_backend.core import gemini


def test_minimal_settings_and_client_use_existing_key_and_timeout(monkeypatch):
    from google import genai

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("GEMINI_TIMEOUT_MS", "4567")
    configured = gemini.GeminiClientSettings(_env_file=None)
    captured = {}

    def construct(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(genai, "Client", construct)
    gemini.create_client(
        api_key=configured.api_key.get_secret_value(), timeout_ms=configured.timeout_ms
    )
    assert captured["api_key"] == "test-gemini"
    assert captured["http_options"].timeout == 4567


def test_router_and_resolver_pass_existing_settings_to_shared_factory(monkeypatch):
    from daengs_backend.orchestration import resolver, semantic

    seen = []

    def construct(**kwargs):
        seen.append(kwargs)
        return object()

    monkeypatch.setattr(gemini, "create_client", construct)
    for module in (semantic, resolver):
        monkeypatch.setattr(
            module,
            "settings",
            SimpleNamespace(
                gemini_api_key=SecretStr("test-gemini"),
                gemini_timeout_ms=4567,
            ),
        )
        module._gemini_client.cache_clear()
        try:
            assert module._gemini_client() is module._gemini_client()
        finally:
            module._gemini_client.cache_clear()
    assert seen == [{"api_key": "test-gemini", "timeout_ms": 4567}] * 2
