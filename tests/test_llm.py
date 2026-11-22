import json
from pathlib import Path

import httpx
import pytest

from doxforge import pipeline, settings
from doxforge.doc_types import load_doc_types
from doxforge.exceptions import ImproperlyConfiguredError
from doxforge.extraction.engine import apply_vocabularies
from doxforge.ingest import Document, Page, TextSource
from doxforge.llm import client as client_module
from doxforge.llm.client import LLMClient, LLMSettings, load_llm_settings
from doxforge.llm.extract import (
    LLMConfig,
    _completes,
    _spelling_key,
    is_grounded,
    llm_schema,
    merge_results,
    squash,
)

DOCUMENT = """Gdańsk, dnia 1 sierpnia 2026 roku
Odwotujacy: Opalit Serwis sp. k., NIP: 9992251714, REGON: 999950475
reprezentowana przez: adw. Patrycja Bartoszewicz
udziela pełnomocnictwa Panu Janowi Kowalskiemu
Zamawiający: Opalit Wyposażenie S.A.
pn. „Dostawa oprogramowania”"""


def test_settings_none_mode_and_missing_variables():
    assert load_llm_settings({"DOXFORGE_LLM": "none"}) is None
    with pytest.raises(ImproperlyConfiguredError, match="DOXFORGE_LLM"):
        load_llm_settings({})
    with pytest.raises(ImproperlyConfiguredError, match="must be one of"):
        load_llm_settings({"DOXFORGE_LLM": "gemini"})
    with pytest.raises(ImproperlyConfiguredError, match="LLM_API_KEY"):
        load_llm_settings(
            {"DOXFORGE_LLM": "openai_compatible", "LLM_BASE_URL": "https://x/v1", "LLM_MODEL": "m"}
        )


def test_settings_normalize_base_url():
    env = {"DOXFORGE_LLM": "openai_compatible", "LLM_BASE_URL": "https://api.eu.mistral.ai/v1"}
    loaded = load_llm_settings(env | {"LLM_MODEL": "ministral-8b-latest", "LLM_API_KEY": "k"})
    assert loaded == LLMSettings("https://api.eu.mistral.ai/v1/", "ministral-8b-latest", "k")


def test_llm_schema_is_strict_and_clean():
    schema = llm_schema(load_doc_types()["odwolanie"].config.output_schema)
    text = json.dumps(schema)
    assert "x-source" not in text and "$ref" not in text and "pattern" not in text
    appeal = schema["properties"]["OdwolanieKIO"]
    assert appeal["additionalProperties"] is False
    assert appeal["required"] == list(appeal["properties"])
    nip = schema["properties"]["Odwolujacy"]["items"]["properties"]["nip"]
    assert nip["type"] == ["string", "null"]


@pytest.mark.parametrize(
    ("value", "value_type", "expected"),
    [
        ("Odwołujący", None, True),  # OCR zgubił ogonki, model je przywrócił
        ("Opalit Serwis sp. k.", None, True),
        ("Jan Kowalski", "name", True),  # mianownik, w tekście celownik
        ("Wymyślona Firma S.A.", None, False),
        ("Anna Nowak", "name", False),
    ],
)
def test_grounding(value, value_type, expected):
    assert is_grounded(value, value_type, squash(DOCUMENT)) is expected


def _empty_appeal() -> dict:
    return {
        "OdwolanieKIO": dict.fromkeys(
            ["data", "czynnosc_dzien", "czynnosc_powod_id", "czynnosc_opis", "miejscowosc"]
        ),
        "Odwolujacy": [
            {
                **dict.fromkeys(["krs", "nazwa", "nip", "regon"]),
                "przez-pelnomocnika": None,
                "pelnomocnik": dict.fromkeys(["imie_nazwisko", "nazwa", "mail"]),
            }
        ],
        "Zamawiajacy": [dict.fromkeys(["krs", "nazwa", "nip", "regon", "prowadzacy_postepowanie"])],
        "ZamowieniePubliczne": dict.fromkeys(["numer_bzp", "numer_postepowania", "numer_ted", "tytul"]),
    }


def test_merge_policies_grounding_and_normalization():
    doc_type = load_doc_types()["odwolanie"]
    rules = _empty_appeal()
    rules["Odwolujacy"][0]["nip"] = "9992251714"
    rules["Odwolujacy"][0]["nazwa"] = "Opalit Serwis sp. k."
    llm = _empty_appeal()
    llm["Odwolujacy"][0]["nip"] = "999-225-17-15"  # polityka rules: reguła wygrywa
    llm["Odwolujacy"][0]["nazwa"] = "Firma Zmyślona sp. z o.o."  # niezgodna z tekstem: odrzucona
    llm["Odwolujacy"][0]["regon"] = "999 950 475"  # brak w regułach: uzupełnione po normalizacji
    llm["OdwolanieKIO"]["miejscowosc"] = "Gdańsk"
    llm["OdwolanieKIO"]["czynnosc_powod_id"] = 9
    llm["Zamawiajacy"] = [
        {
            "krs": None,
            "nazwa": "Opalit Wyposażenie S.A.",
            "nip": None,
            "regon": None,
            "prowadzacy_postepowanie": 7,
        }
    ]
    llm["ZamowieniePubliczne"]["tytul"] = "Dostawa oprogramowania"
    config = doc_type.config.llm
    assert config is not None
    merged = merge_results(doc_type.config.output_schema, config, DOCUMENT, rules, llm)
    apply_vocabularies(merged, doc_type.config.extraction, doc_type.config.vocabularies)

    appellant = merged["Odwolujacy"][0]
    assert appellant["nip"] == "9992251714"
    assert appellant["nazwa"] == "Opalit Serwis sp. k."
    assert appellant["regon"] == "999950475"
    assert merged["OdwolanieKIO"]["miejscowosc"] == "Gdańsk"
    assert merged["OdwolanieKIO"]["czynnosc_opis"] == "Decyzja o unieważnieniu postępowania"
    ordering = merged["Zamawiajacy"][0]
    assert ordering["nazwa"] == "Opalit Wyposażenie S.A."
    assert ordering["prowadzacy_postepowanie"] is None  # 7 spoza enum
    assert merged["ZamowieniePubliczne"]["tytul"] == "Dostawa oprogramowania"
    assert doc_type.validation_errors(merged) == []


def test_llm_list_replaces_rules_when_lengths_differ():
    doc_type = load_doc_types()["pelnomocnictwo"]
    config = doc_type.config.llm
    assert config is not None
    rules = {
        "mocodawca": [
            {"data": dict.fromkeys(["imie_nazwisko", "krs", "nazwa", "nip", "typ"]), "podpisujacy": []}
        ],
        "pelnomocnicy": [{"imie_nazwisko": "Patrycja Bartoszewicz", "pesel": None}],
    }
    llm = {
        "mocodawca": [
            {"data": dict.fromkeys(["imie_nazwisko", "krs", "nazwa", "nip", "typ"]), "podpisujacy": []}
        ],
        "pelnomocnicy": [
            {"imie_nazwisko": "adw. Patrycja Bartoszewicz", "pesel": None},
            {"imie_nazwisko": "Jan Kowalski", "pesel": None},
            {"imie_nazwisko": "Ktoś Wymyślony", "pesel": None},  # brak w tekście: odrzucony
        ],
    }
    merged = merge_results(doc_type.config.output_schema, config, DOCUMENT, rules, llm)
    assert merged["pelnomocnicy"] == [
        {"imie_nazwisko": "Patrycja Bartoszewicz", "pesel": None},
        {"imie_nazwisko": "Jan Kowalski", "pesel": None},
    ]


def test_unknown_policy_path_fails_at_startup(tmp_path: Path):
    (tmp_path / "a.yaml").write_text(
        "name: a\ndescription: x\n"
        "schema: {type: object, required: [x], properties: {x: {type: string, x-source: text}}}\n"
        "classification: {min_score: 1, rules: [{pattern: x, weight: 1}]}\n"
        "llm: {policies: {y: rules}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="LLM policy for unknown field 'y'"):
        load_doc_types(tmp_path)


def _client(tmp_path: Path, handler) -> LLMClient:
    client = LLMClient(LLMSettings("https://llm.test/v1/", "m", "secret"), cache_dir=tmp_path)
    client._http = httpx.Client(base_url="https://llm.test/v1/", transport=httpx.MockTransport(handler))
    client._interval_s = 0  # rate-specific tests adapt the interval using a simulated clock
    return client


def _answer(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def test_client_retries_rate_limit_then_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(429) if len(calls) == 1 else _answer({"a": 1})

    client = _client(tmp_path, handler)
    assert client.complete_json("s", "u", {"type": "object"}, "x") == {"a": 1}
    assert client.complete_json("s", "u", {"type": "object"}, "x") == {"a": 1}  # z pamięci podręcznej
    assert len(calls) == 2
    assert calls[1]["response_format"]["type"] == "json_schema"
    assert calls[1]["temperature"] == 0


def test_client_raises_on_client_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    client = _client(tmp_path, lambda request: httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        client.complete_json("s", "u", {"type": "object"}, "x")


def test_pipeline_falls_back_to_rules_when_llm_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    text = "Prezes Krajowej Izby Odwoławczej\nOdwołujący:\nFirma sp. k.\nNIP: 9992251714\nODWOŁANIE\n"
    monkeypatch.setattr(
        pipeline, "load_document", lambda pdf, pace: Document([Page(text, TextSource.TEXT_LAYER)])
    )
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    client = _client(tmp_path, lambda request: httpx.Response(503))
    result = pipeline.process_document(Path("x.pdf"), load_doc_types(), client)
    assert result["Odwolujacy"][0]["nip"] == "9992251714"
    assert settings.LLM_MAX_RETRIES >= 1


@pytest.mark.parametrize(
    ("value", "kind", "source"),
    [
        ("1234567890", "nip", "NIP: 123-456-78-91"),
        ("123456789", "regon", "REGON 123456788"),
        ("12345678901", "pesel", "PESEL 12345678902"),
        ("12345678901234567890123456", "iban", "PL12 3456 7890 1234 5678 9012 3457"),
        ("anna@example.pl", "email", "E-mail: anna@example.com"),
    ],
)
def test_identifiers_never_use_fuzzy_grounding(value, kind, source):
    assert not is_grounded(value, kind, squash(source))


def test_reordered_entities_keep_their_own_identifiers():
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "nazwa": {"type": ["string", "null"], "x-source": "text"},
                "nip": {"type": ["string", "null"], "x-source": "text", "x-type": "nip"},
            },
        },
    }
    rules = [{"nazwa": "Alfa", "nip": "1234567890"}, {"nazwa": "Beta", "nip": "9876543210"}]
    llm = [{"nazwa": "Beta", "nip": None}, {"nazwa": "Alfa", "nip": None}]
    config = LLMConfig(policies={"[].nip": "rules"})
    merged = merge_results(schema, config, "Alfa 1234567890; Beta 9876543210", rules, llm)
    assert merged == list(reversed(rules))


def test_unmatched_entity_does_not_inherit_another_entity_id():
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "nazwa": {"type": ["string", "null"], "x-source": "text"},
                "nip": {"type": ["string", "null"], "x-source": "text", "x-type": "nip"},
            },
        },
    }
    rules = [{"nazwa": "Alfa", "nip": "1234567890"}, {"nazwa": "Beta", "nip": "9876543210"}]
    llm = [{"nazwa": "Gamma", "nip": None}, {"nazwa": "Alfa", "nip": None}]
    merged = merge_results(schema, LLMConfig(), "Alfa 1234567890 Beta 9876543210 Gamma", rules, llm)
    assert merged == [{"nazwa": "Gamma", "nip": None}, rules[0]]


def test_adapted_rate_applies_to_next_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clock = [100.0]
    sleeps = []

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "sleep", sleep)
    client = _client(tmp_path, lambda request: _answer({"a": 1}))
    client._throttle()
    client._adapt_interval(httpx.Headers({"x-ratelimit-limit-req-minute": "2"}))
    client._throttle()
    assert sleeps == [31.5]


def test_sleeping_worker_rechecks_new_provider_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clock = [100.0]
    sleeps = []
    client = _client(tmp_path, lambda request: _answer({"a": 1}))
    client._interval_s = 1.2

    def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 1:
            # A response from another worker arrives while this one is asleep.
            client._adapt_interval(httpx.Headers({"x-ratelimit-limit-req-minute": "2"}))
        clock[0] += delay

    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "sleep", sleep)
    client._throttle()
    client._throttle()
    assert sum(sleeps) == pytest.approx(31.5)
    assert client._last_request_at == pytest.approx(131.5)


def test_retry_after_is_not_capped_at_thirty_seconds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clock = [100.0]
    sleeps, calls = [], []

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "65"}) if len(calls) == 1 else _answer({"a": 1})

    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "sleep", sleep)
    client = _client(tmp_path, handler)
    assert client.complete_json("s", "u", {}, "x") == {"a": 1}
    assert sleeps == [65.0]


def test_bad_model_json_does_not_poison_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "bad json"}}]})
        return _answer({"a": 1})

    client = _client(tmp_path, handler)
    with pytest.raises(json.JSONDecodeError):
        client.complete_json("s", "u", {}, "x")
    assert list(tmp_path.glob("*.json")) == []
    assert client.complete_json("s", "u", {}, "x") == {"a": 1}


@pytest.mark.parametrize(
    ("value", "kind", "source"),
    [
        ("123456789", "regon", "REGON: 12345678900000"),  # fragment 14-cyfrowego REGON
        ("2170000891", "nip", "PL62 2170 0008 9152 5017 5661 7062"),  # fragment rachunku
        ("ZP.271.16.2024", None, "Numer postępowania: ZP.271.15.2024"),
        ("ZP.271.15.2023", None, "Numer postępowania: ZP.271.15.2024"),
    ],
)
def test_grounding_rejects_changed_or_partial_numbers(value, kind, source):
    assert not is_grounded(value, kind, squash(source))


@pytest.mark.parametrize(
    ("value", "kind", "source"),
    [
        ("1234567890", "nip", "NIP: 123-456-78-90, REGON: 123456789"),
        ("ZP.271.15.2024", None, "Numer postçpowania: ZP-271.l5.2024"),  # OCR: litera zamiast cyfry
        ("ZP.271.15.2024", None, "Numer postępowania: ZP 271/15/2024"),
    ],
)
def test_grounding_keeps_exact_numbers(value, kind, source):
    assert is_grounded(value, kind, squash(source))


@pytest.mark.parametrize(
    ("rule", "llm", "expected"),
    [
        ("DUDEK-TECH Jarosław Dudek", "DUDEK-TECH", True),
        ("Firma sp. z o.o. ul. Polna 1", "Firma sp. z o.o.", False),  # cyfry: adres, nie nazwa
        ("Firma", "Firma", False),
        ("Firmowa Grupa", "Firma", False),  # nie jest kontynuacją po granicy słowa
        ("ABC Jan Kowalski z siedzibą w Kielcach", "ABC Jan Kowalski", False),  # za długi dopisek
    ],
)
def test_rule_completes_truncated_organization_name_only_by_a_few_words(rule, llm, expected):
    assert _completes(rule, llm) is expected


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (
            "Kancelaria Adwokacka Spółka z ograniczoną odpowiedzialnością",
            "Kancelaria Adwokacka Spółka z o.o.",
            True,
        ),
        ("Usługa ochrony w latach 2026\u20132028", "Usługa ochrony w latach 2026-2028", True),
        ("Alfa Spółka Akcyjna", "Alfa S.A.", True),
        ("Kancelaria Adwokacka Jan Nowak", "Kancelaria Adwokacka Anna Kowal sp.k.", False),
    ],
)
def test_spelling_key_matches_only_rewritten_forms_of_the_same_value(a, b, same):
    assert (_spelling_key(a) == _spelling_key(b)) is same
