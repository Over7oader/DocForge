import httpx
import pytest

from doxforge.llm.client import LLMClient, Sampling, load_llm_settings
from doxforge.llm.ensemble import EnsembleClient, build_client, vote

SCHEMA = {
    "type": "object",
    "properties": {
        "nr": {"type": ["string", "null"]},
        "kwota": {"type": ["number", "null"]},
        "osoby": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "string"}}}},
    },
}


def test_leaf_majority_ignores_case_and_punctuation_and_keeps_first_spelling():
    answers = [{"nr": "ZP.1.2026"}, {"nr": "zp 1 2026"}, {"nr": "ZP.7.2026"}]
    assert vote(SCHEMA, answers)["nr"] == "ZP.1.2026"


def test_null_majority_wins_and_tie_goes_to_first_member():
    assert vote(SCHEMA, [{"kwota": None}, {"kwota": None}, {"kwota": 5.0}])["kwota"] is None
    assert vote(SCHEMA, [{"kwota": 7.0}, {"kwota": 5.0}])["kwota"] == 7.0


def test_list_is_taken_whole_from_the_most_supported_answer():
    a = [{"n": "Jan Gaj"}]
    b = [{"n": "Jan Gaj"}, {"n": "Anna Lis"}]
    c = [{"n": "Jan Gaj"}, {"n": "Anna Lis"}]
    assert vote(SCHEMA, [{"osoby": a}, {"osoby": b}, {"osoby": c}])["osoby"] == b


class Fake:
    def __init__(self, answer, fail=False):
        self.answer, self.fail, self.calls, self.closed = answer, fail, [], False

    def complete_json(self, system, user, schema, schema_name, sampling=None):
        self.calls.append(sampling)
        if self.fail:
            raise httpx.ConnectError("down")
        return self.answer

    def close(self):
        self.closed = True


def test_ensemble_samples_votes_and_survives_a_failed_member():
    good, other, broken = Fake({"nr": "A"}), Fake({"nr": "B"}), Fake(None, fail=True)
    client = EnsembleClient([(good, 2), (other, 1), (broken, 1)], temperature=0.5)
    assert client.complete_json("s", "u", SCHEMA, "x")["nr"] == "A"
    assert good.calls == [Sampling(0.5, 0), Sampling(0.5, 1)]
    assert other.calls == [None]  # jedna próbka: deterministycznie
    client.close()
    assert good.closed and broken.closed


def test_ensemble_raises_when_every_member_fails():
    client = EnsembleClient([(Fake(None, fail=True), 1)], temperature=0.5)
    with pytest.raises(httpx.ConnectError):
        client.complete_json("s", "u", SCHEMA, "x")


def test_openrouter_models_list_and_samples_build_an_ensemble():
    env = {
        "DOXFORGE_LLM": "openrouter",
        "OPENROUTER_API_KEY": "k",
        "OPENROUTER_MODEL": "mistralai/ministral-8b-2512, mistralai/ministral-3b-2512",
        "OPENROUTER_SAMPLES": "2",
    }
    config = load_llm_settings(env)
    assert config.model == "mistralai/ministral-8b-2512"
    assert config.extra_models == ("mistralai/ministral-3b-2512",)
    client = build_client(config)
    assert isinstance(client, EnsembleClient)
    client.close()
    single = build_client(load_llm_settings({**env, "OPENROUTER_MODEL": "m", "OPENROUTER_SAMPLES": "1"}))
    assert isinstance(single, LLMClient)
    single.close()
