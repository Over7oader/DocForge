"""Głosowanie kilku odpowiedzi LLM (kilka małych modeli albo kilka próbek jednego modelu).

Każda odpowiedź jest pełnym JSON-em klasy. Liść wybieramy większością głosów (wartości porównane
po złożeniu: małe litery, bez polskich znaków i znaków interpunkcyjnych), listę w całości jako
medoid: odpowiedź, której elementy najczęściej powtarzają się w innych odpowiedziach. Remis
rozstrzyga kolejność członków ensemble. Głosowanie nie tworzy nowych wartości, a wynik przechodzi
potem tę samą weryfikację ze źródłem co odpowiedź pojedynczego modelu.
"""

import dataclasses
import json
import logging
import re
from collections import Counter
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import JsonValue, ValidationError

from doxforge import settings
from doxforge.llm.client import LLMClient, LLMSettings, Sampling
from doxforge.text import fold

logger = logging.getLogger(__name__)


class JsonCompleter(Protocol):
    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, JsonValue],
        schema_name: str,
        sampling: Sampling | None = None,
    ) -> JsonValue: ...

    def close(self) -> None: ...


def _key(value: JsonValue) -> str:
    if isinstance(value, str):
        return "s:" + re.sub(r"[\W_]+", "", fold(value))
    return "j:" + json.dumps(value, sort_keys=True, ensure_ascii=False)


def vote(schema: JsonValue, answers: Sequence[JsonValue]) -> JsonValue:
    """Wynik głosowania odpowiedzi zgodnych ze schematem (bez $ref), w kolejności ważności."""
    if not answers:
        raise ValueError("Voting needs at least one answer")
    if not isinstance(schema, dict):
        return answers[0]
    properties = schema.get("properties")
    if isinstance(properties, dict):
        objects = [answer if isinstance(answer, dict) else {} for answer in answers]
        return {key: vote(child, [o.get(key) for o in objects]) for key, child in properties.items()}
    if schema.get("items") is not None:
        lists = [answer if isinstance(answer, list) else [] for answer in answers]
        keys = [Counter(_key(item) for item in items) for items in lists]
        support = [
            sum(sum((own & other).values()) for j, other in enumerate(keys) if j != i)
            for i, own in enumerate(keys)
        ]
        return lists[max(range(len(lists)), key=lambda i: (support[i], -i))]
    counts = Counter(_key(answer) for answer in answers)
    best = max(counts.values())
    return next(answer for answer in answers if counts[_key(answer)] == best)


class EnsembleClient:
    """Ten sam interfejs co `LLMClient`: pyta każdego członka (model, liczba próbek) i głosuje."""

    def __init__(self, members: Sequence[tuple[JsonCompleter, int]], temperature: float) -> None:
        self._members = members
        self._temperature = temperature

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, JsonValue],
        schema_name: str,
        sampling: Sampling | None = None,
    ) -> JsonValue:
        del sampling  # próbkowanie ustala ensemble, nie wywołujący
        answers: list[JsonValue] = []
        error: Exception | None = None
        for client, samples in self._members:
            for index in range(samples):
                try:
                    answers.append(
                        client.complete_json(
                            system,
                            user,
                            schema,
                            schema_name,
                            Sampling(self._temperature, index) if samples > 1 else None,
                        )
                    )
                except (httpx.HTTPError, ValidationError, json.JSONDecodeError) as failure:
                    # Brak jednego głosu nie przekreśla pozostałych.
                    logger.warning("Ensemble member failed: %s", type(failure).__name__)
                    error = failure
        if not answers:
            assert error is not None
            raise error
        return vote(schema, answers)

    def close(self) -> None:
        for client, _ in self._members:
            client.close()


def build_client(config: LLMSettings) -> JsonCompleter:
    """Pojedynczy klient albo ensemble, gdy konfiguracja ma kilka modeli lub kilka próbek."""
    if not config.extra_models and config.samples == 1:
        return LLMClient(config)
    models = [config.model, *config.extra_models]
    members: list[tuple[JsonCompleter, int]] = [
        (LLMClient(dataclasses.replace(config, model=model, extra_models=(), samples=1)), config.samples)
        for model in models
    ]
    logger.info("LLM ensemble: %s x %d sample(s)", models, config.samples)
    return EnsembleClient(members, settings.LLM_ENSEMBLE_TEMPERATURE)
