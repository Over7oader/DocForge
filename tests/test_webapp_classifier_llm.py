from webapp.classifier_llm import classify_llm


class _Pick:
    def __init__(self, choice):
        self.choice = choice
        self.schema = None

    def complete_json(self, system, user, schema, schema_name, sampling=None):
        self.schema = schema
        return {"choice": self.choice}

    def close(self):
        pass


class _Never:
    def complete_json(self, *a, **k):
        raise AssertionError("LLM must not be called")

    def close(self):
        pass


def test_picks_registered_type():
    assert classify_llm("...", [("faktura", "FV")], _Pick("faktura")) == "faktura"


def test_none_choice_returns_none():
    assert classify_llm("...", [("faktura", "FV")], _Pick("none")) is None


def test_unknown_choice_returns_none():
    assert classify_llm("...", [("faktura", "FV")], _Pick("cos_innego")) is None


def test_empty_candidates_short_circuits_without_llm():
    assert classify_llm("...", [], _Never()) is None


def test_choice_enum_is_candidates_plus_none():
    picker = _Pick("faktura")
    classify_llm("...", [("faktura", "FV"), ("umowa", "U")], picker)
    enum = picker.schema["properties"]["choice"]["enum"]
    assert set(enum) == {"faktura", "umowa", "none"}
