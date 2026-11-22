"""Klient czatu z ustrukturyzowaną odpowiedzią (JSON Schema) dla API zgodnego z OpenAI.

Działa z Mistral (`https://api.eu.mistral.ai/v1/`) i innymi dostawcami tego formatu
(OpenRouter, Scaleway, OVHcloud AI Endpoints, vLLM). Wybór dostawcy to tylko konfiguracja w `.env`.
"""

import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path

import httpx
from pydantic import BaseModel, JsonValue

from doxforge import settings
from doxforge.exceptions import ImproperlyConfiguredError

logger = logging.getLogger(__name__)

# Statusy, przy których ponowienie ma sens (limit zapytań, chwilowa awaria dostawcy).
_RETRY_STATUSES = {
    HTTPStatus.TOO_MANY_REQUESTS,
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
}


# Nagłówek z limitem zapytań na minutę (Mistral); inni dostawcy mogą go nie wysyłać.
_REQUESTS_PER_MINUTE_HEADER = "x-ratelimit-limit-req-minute"


class LLMMode(StrEnum):
    NONE = "none"  # tylko reguły
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENROUTER = "openrouter"


@dataclass(frozen=True)
class LLMSettings:
    base_url: str  # zakończony "/"
    model: str
    api_key: str
    provider: dict[str, JsonValue] | None = None
    max_tokens: int | None = None
    min_interval_s: float = settings.LLM_MIN_REQUEST_INTERVAL_S
    # Ensemble: further models voting with `model`, and samples per model (1 = deterministic).
    extra_models: tuple[str, ...] = ()
    samples: int = 1


@dataclass(frozen=True)
class Sampling:
    """Non-deterministic sampling of one request; `index` separates repeated samples in the cache."""

    temperature: float
    index: int


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    if not value:
        raise ImproperlyConfiguredError(f"Missing required environment variable {name}")
    return value


def _openrouter_settings(environ: Mapping[str, str]) -> LLMSettings:
    key = _required(environ, "OPENROUTER_API_KEY")
    base_url = environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/").strip()
    # One model, or several comma-separated models voting together (first breaks ties).
    models = [
        name.strip()
        for name in environ.get("OPENROUTER_MODEL", "mistralai/ministral-8b-2512").split(",")
        if name.strip()
    ]
    model = models[0] if models else ""
    only = [
        name.strip() for name in environ.get("OPENROUTER_PROVIDERS", "mistral/eu").split(",") if name.strip()
    ]
    if not base_url or not model or not only:
        raise ImproperlyConfiguredError("OpenRouter requires a base URL, model and provider allowlist")
    try:
        max_tokens = int(environ.get("OPENROUTER_MAX_TOKENS", "4096"))
        interval = float(environ.get("OPENROUTER_MIN_INTERVAL_S", "1.2"))
        samples = int(environ.get("OPENROUTER_SAMPLES", "1"))
    except ValueError:
        raise ImproperlyConfiguredError(
            "Invalid OpenRouter max tokens, request interval or samples"
        ) from None
    if samples < 1:
        raise ImproperlyConfiguredError("OPENROUTER_SAMPLES must be a positive integer")
    if max_tokens <= 0 or not math.isfinite(interval) or interval < 0:
        raise ImproperlyConfiguredError(
            "OpenRouter max tokens must be positive and interval finite/nonnegative"
        )
    return LLMSettings(
        base_url=base_url.rstrip("/") + "/",
        model=model,
        api_key=key,
        provider={
            "only": [str(name) for name in only],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
        },
        max_tokens=max_tokens,
        min_interval_s=interval,
        extra_models=tuple(models[1:]),
        samples=samples,
    )


def load_llm_settings(environ: Mapping[str, str] = os.environ) -> LLMSettings | None:
    """Ustawienia LLM z env; `None` dla `DOXFORGE_LLM=none`. Braki przerywają start aplikacji."""
    raw_mode = _required(environ, "DOXFORGE_LLM")
    try:
        mode = LLMMode(raw_mode)
    except ValueError:
        raise ImproperlyConfiguredError(
            f"DOXFORGE_LLM must be one of {[m.value for m in LLMMode]}, got {raw_mode!r}"
        ) from None
    if mode is LLMMode.NONE:
        return None
    if mode is LLMMode.OPENROUTER:
        return _openrouter_settings(environ)
    if mode is LLMMode.OPENAI_COMPATIBLE:
        base_url = _required(environ, "LLM_BASE_URL")
        return LLMSettings(
            base_url=base_url if base_url.endswith("/") else f"{base_url}/",
            model=_required(environ, "LLM_MODEL"),
            api_key=_required(environ, "LLM_API_KEY"),
        )
    raise ImproperlyConfiguredError(f"Unhandled LLM mode {mode!r}")


class _Message(BaseModel):
    content: str


class _Choice(BaseModel):
    message: _Message


class _Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None


class _ChatResponse(BaseModel):
    choices: list[_Choice]
    usage: _Usage | None = None
    provider: str | None = None
    id: str | None = None


class LLMClient:
    def __init__(self, llm_settings: LLMSettings, cache_dir: Path = settings.LLM_CACHE_DIR) -> None:
        self.model = llm_settings.model
        self._settings = llm_settings
        self._cache_dir = cache_dir
        self._http = httpx.Client(
            base_url=llm_settings.base_url,
            timeout=settings.LLM_TIMEOUT_S,
            headers={"Authorization": f"Bearer {llm_settings.api_key}"},
        )
        # Wspólny dla wątków odstęp między zapytaniami: dostawcy limitują liczbę zapytań na sekundę.
        self._throttle_lock = threading.Lock()
        self._next_request_at = 0.0
        self._last_request_at = 0.0
        self._interval_s = llm_settings.min_interval_s

    def _throttle(self) -> None:
        # Reserve at dispatch time. Pre-reserving slots outside the lock lets sleeping
        # workers send at the OLD interval after a response lowers the rate limit.
        while True:
            with self._throttle_lock:
                now = time.monotonic()
                wait = self._next_request_at - now
                if wait <= 0:
                    self._last_request_at = now
                    self._next_request_at = now + self._interval_s
                    return
            # Responses must be able to update limits/cooldown while workers sleep.
            # Recheck on wake: another worker or a new limit may have moved the slot.
            time.sleep(wait)

    def _adapt_interval(self, headers: httpx.Headers) -> None:
        """Dopasowuje odstęp do limitu zapytań na minutę, jeśli dostawca go podaje (np. Mistral)."""
        limit = headers.get(_REQUESTS_PER_MINUTE_HEADER, "")
        if limit.isdigit() and int(limit) > 0:
            interval = max(self._settings.min_interval_s, 60 / int(limit) * 1.05)
            with self._throttle_lock:
                if interval != self._interval_s:
                    logger.info(
                        "Provider allows %s requests/min, spacing requests by %.1f s", limit, interval
                    )
                    self._interval_s = interval
                    self._next_request_at = max(self._next_request_at, self._last_request_at + interval)

    def close(self) -> None:
        self._http.close()

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, JsonValue],
        schema_name: str,
        sampling: Sampling | None = None,
    ) -> JsonValue:
        """Odpowiedź modelu jako JSON zgodny ze schematem (z pamięcią podręczną na dysku).

        Raises:
            httpx.HTTPError: błąd sieci albo status HTTP po wyczerpaniu ponowień.
            pydantic.ValidationError: odpowiedź API w nieoczekiwanym kształcie.
            json.JSONDecodeError: treść odpowiedzi nie jest JSON-em.
        """
        body: dict[str, JsonValue] = {
            "model": self.model,
            "temperature": sampling.temperature if sampling else 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            },
        }
        if self._settings.provider is not None:
            body["provider"] = self._settings.provider
        if self._settings.max_tokens is not None:
            body["max_tokens"] = self._settings.max_tokens
        cache_identity: dict[str, JsonValue] = body
        # Preserve known direct-Mistral cache. Gateways and their regions have separate identities.
        if self._settings.base_url != "https://api.eu.mistral.ai/v1/":
            cache_identity = {"endpoint": self._settings.base_url, "request": body}
        if sampling and sampling.index:
            # Repeated sampling of one request (ensemble): each sample is a separate cached answer.
            cache_identity = {**cache_identity, "sample": sampling.index}
        cache_file = (
            self._cache_dir
            / f"{hashlib.sha256(json.dumps(cache_identity, sort_keys=True).encode()).hexdigest()}.json"
        )
        if cache_file.is_file():
            content = cache_file.read_text(encoding="utf-8")
        else:
            response = _ChatResponse.model_validate(self._post(body))
            if response.usage is not None:
                logger.info(
                    "LLM usage: %s",
                    json.dumps(
                        {
                            "model": self.model,
                            "provider": response.provider,
                            "request_id": response.id,
                            **response.usage.model_dump(),
                        }
                    ),
                )
            content = response.choices[0].message.content
            # Never persist an invalid response: a transient bad JSON must not poison
            # all subsequent runs for this document.
            json.loads(content)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self._cache_dir, suffix=".tmp", encoding="utf-8", delete=False
            ) as staged:
                staged.write(content)
            Path(staged.name).replace(cache_file)
        result: JsonValue = json.loads(content)
        return result

    def _post(self, body: dict[str, JsonValue]) -> JsonValue:
        for attempt in range(settings.LLM_MAX_RETRIES + 1):
            last_attempt = attempt == settings.LLM_MAX_RETRIES
            self._throttle()
            try:
                response = self._http.post("chat/completions", json=body)
            except httpx.TransportError:  # timeout, zerwane połączenie
                if last_attempt:
                    raise
                logger.warning("LLM request failed (attempt %d), retrying", attempt + 1, exc_info=True)
            else:
                self._adapt_interval(response.headers)
                if response.status_code not in _RETRY_STATUSES or last_attempt:
                    response.raise_for_status()
                    result: JsonValue = response.json()
                    return result
                logger.warning("LLM returned %d (attempt %d), retrying", response.status_code, attempt + 1)
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    # Retry-After is a server minimum, not capped exponential backoff.
                    # Share the cooldown so other workers do not spend the same quota.
                    with self._throttle_lock:
                        self._next_request_at = max(
                            self._next_request_at, time.monotonic() + int(retry_after)
                        )
                    continue
            time.sleep(min(settings.LLM_RETRY_BASE_DELAY_S * 2**attempt, settings.LLM_MAX_RETRY_DELAY_S))
        raise AssertionError("unreachable: the last attempt returns or raises")
