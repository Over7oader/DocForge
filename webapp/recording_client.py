"""An ensemble client that keeps every member's answer instead of discarding it.

`EnsembleClient.complete_json` votes and returns only the winner. `RecordingClient` behaves
identically (same members, same voting, same tolerance for a failed member) but also exposes the
per-member answers of the most recent call, which `webapp.confidence` turns into per-field
agreement. It builds the same members `ensemble.build_client` would, so even a single configured
model yields one answer (and a "no signal" confidence).
"""

import dataclasses
import json
import logging
from collections.abc import Sequence

import httpx
from pydantic import JsonValue, ValidationError

from doxforge import settings
from doxforge.llm.client import LLMClient, LLMSettings, Sampling
from doxforge.llm.ensemble import JsonCompleter, vote

logger = logging.getLogger(__name__)


class RecordingClient:
    """Same interface as `LLMClient`/`EnsembleClient`; `.answers` holds the last call's votes."""

    def __init__(self, members: Sequence[tuple[JsonCompleter, int]], temperature: float) -> None:
        self._members = members
        self._temperature = temperature
        self.answers: list[JsonValue] = []

    @property
    def member_count(self) -> int:
        return len(self.answers)

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, JsonValue],
        schema_name: str,
        sampling: Sampling | None = None,
    ) -> JsonValue:
        del sampling  # the ensemble decides sampling, not the caller
        self.answers = []
        error: Exception | None = None
        for client, samples in self._members:
            for index in range(samples):
                try:
                    self.answers.append(
                        client.complete_json(
                            system,
                            user,
                            schema,
                            schema_name,
                            Sampling(self._temperature, index) if samples > 1 else None,
                        )
                    )
                except (httpx.HTTPError, ValidationError, json.JSONDecodeError) as failure:
                    logger.warning("Ensemble member failed: %s", type(failure).__name__)
                    error = failure
        if not self.answers:
            assert error is not None
            raise error
        return vote(schema, self.answers)

    def close(self) -> None:
        for client, _ in self._members:
            client.close()


def from_settings(config: LLMSettings) -> RecordingClient:
    """Build a recording ensemble of the configured model(s), one `LLMClient` per model."""
    models = [config.model, *config.extra_models]
    members: list[tuple[JsonCompleter, int]] = [
        (LLMClient(dataclasses.replace(config, model=model, extra_models=(), samples=1)), config.samples)
        for model in models
    ]
    return RecordingClient(members, settings.LLM_ENSEMBLE_TEMPERATURE)
