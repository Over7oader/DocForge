import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from doxforge.exceptions import ImproperlyConfiguredError
from doxforge.llm.client import LLMClient, load_llm_settings


def config(**extra):
    result = load_llm_settings({"DOXFORGE_LLM": "openrouter", "OPENROUTER_API_KEY": "router-secret", **extra})
    assert result is not None
    return result


def test_openrouter_has_dedicated_key_model_and_routing():
    settings = config(LLM_API_KEY="old-mistral-secret")
    assert settings.api_key == "router-secret"
    assert settings.base_url == "https://openrouter.ai/api/v1/"
    assert settings.model == "mistralai/ministral-8b-2512"
    assert settings.provider == {
        "only": ["mistral/eu"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
    }


def test_openrouter_never_uses_the_mistral_key():
    with pytest.raises(ImproperlyConfiguredError, match="OPENROUTER_API_KEY"):
        load_llm_settings({"DOXFORGE_LLM": "openrouter", "LLM_API_KEY": "wrong-provider-secret"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"OPENROUTER_PROVIDERS": ",  "},
        {"OPENROUTER_BASE_URL": ""},
        {"OPENROUTER_MODEL": ""},
        {"OPENROUTER_MAX_TOKENS": "0"},
        {"OPENROUTER_MAX_TOKENS": "bad"},
        {"OPENROUTER_MIN_INTERVAL_S": "-1"},
        {"OPENROUTER_MIN_INTERVAL_S": "nan"},
        {"OPENROUTER_MIN_INTERVAL_S": "inf"},
    ],
)
def test_openrouter_invalid_settings_fail_before_processing(overrides):
    with pytest.raises(ImproperlyConfiguredError):
        config(**overrides)


def test_regional_endpoint_provider_override_and_rate():
    settings = config(
        OPENROUTER_BASE_URL="https://eu.openrouter.ai/api/v1",
        OPENROUTER_PROVIDERS="mistral/eu, another/eu",
        OPENROUTER_MAX_TOKENS="2048",
        OPENROUTER_MIN_INTERVAL_S="6",
    )
    assert settings.base_url == "https://eu.openrouter.ai/api/v1/"
    assert settings.provider["only"] == ["mistral/eu", "another/eu"]
    assert settings.max_tokens == 2048
    assert settings.min_interval_s == 6


def test_wire_format_cache_and_regions_are_separate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"field": "value"}'}}]})

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    settings = config(OPENROUTER_MIN_INTERVAL_S="0")
    configs = [
        settings,
        replace(settings, base_url="https://eu.openrouter.ai/api/v1/"),
        replace(settings, provider={**settings.provider, "only": ["different/eu"]}),
    ]
    for cfg in configs:
        client = LLMClient(cfg, cache_dir=tmp_path)
        try:
            assert client.complete_json("system", "user", {"type": "object"}, "example") == {"field": "value"}
            assert client.complete_json("system", "user", {"type": "object"}, "example") == {"field": "value"}
        finally:
            client.close()
    assert len(requests) == 3
    body = json.loads(requests[0].content)
    assert requests[0].url == "https://openrouter.ai/api/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer router-secret"
    assert body["model"] == settings.model
    assert body["provider"] == settings.provider
    assert body["max_tokens"] == 4096
    assert body["response_format"]["json_schema"]["strict"] is True
    assert len(list(tmp_path.glob("*.json"))) == 3
