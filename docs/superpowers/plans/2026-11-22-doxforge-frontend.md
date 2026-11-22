# DoxForge Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Streamlit GUI over the existing DoxForge pipeline that shows per-field provenance and ensemble-agreement confidence, runs extraction from the browser, and lets users register new document types (classified via an LLM fallback).

**Architecture:** A new `webapp/` package beside `doxforge/` holding pure, unit-testable helpers (provenance, confidence, LLM classifier, type registry, a recording LLM client) plus a thin `run.py` orchestrator and a `app.py` Streamlit UI. Core `doxforge/` modules are imported, not modified. Provenance is derived by position-preserving `fold()` search; confidence is computed from the per-member answers a recording client captures that `EnsembleClient` normally discards.

**Tech Stack:** Python 3.13+, Streamlit, existing deps (pydantic, PyYAML, rapidfuzz, pypdfium2). Reuses `doxforge.pipeline.prepare/finish`, `doxforge.text.fold`, `doxforge.doc_types.load_doc_types/DocType`, `doxforge.llm.client.load_llm_settings/LLMClient`, `doxforge.llm.ensemble.vote/_key`, `doxforge.classify.classify`.

**Spec:** `docs/superpowers/specs/2026-11-22-doxforge-frontend-design.md`

## Global Constraints

- Python 3.13+. Streamlit goes in a new `requirements-web.txt`; core `requirements.txt` is unchanged.
- No edits to `doxforge/` core modules; the webapp imports them. (If an edit proves unavoidable, stop and flag it.)
- Every feature degrades gracefully when `DOXFORGE_LLM=none` / `load_llm_settings()` returns `None`: rules-only run, confidence shows "no signal", Mode A and the LLM-fallback classifier are skipped.
- Doc-type names match `^[a-z][a-z0-9_]*$` (enforced by `DocTypeConfig`). Registered types are tracked in a sidecar `config/doc_types/.registered.json` (list of names); built-in types are never in it.
- `python -m doxforge run`, the test suite, `ruff format --check`, `ruff check`, and `mypy doxforge` continue to pass. New code also passes ruff/mypy.
- Unknown/negative documents still return `{}` — the LLM fallback classifier always has an explicit "none" choice and only ranks **registered** types.

## Review Focus

- **Value appears on multiple pages / multiple times:** provenance search must return the *first* match deterministically, not crash or pick arbitrarily. (Task 1)
- **Leaf value is `null`, empty, or whitespace-only:** no highlight and no false match; status `derived`/`absent`. (Task 1)
- **Single model configured (no ensemble):** confidence must report "no signal", never green. (Task 2)
- **Ensemble members disagree on array length (entity count):** flagged low-confidence rather than mismatched field-by-field. (Task 2)
- **Registering a type whose name collides with a built-in or has a bad schema:** rejected in the form with a message, nothing written. (Task 5)

---

## Task 1: Provenance derivation

**Files:**
- Create: `webapp/__init__.py`, `webapp/provenance.py`
- Test: `tests/test_webapp_provenance.py`

**Interfaces:**
- Consumes: `doxforge.ingest.Page` (`.text`), `doxforge.text.fold`, `DocType.field_sources` (`dict[str, str]`, keys like `Odwolujacy[].nip`).
- Produces: `locate(result: JsonValue, field_sources: dict[str, str], pages: list[str]) -> dict[str, Provenance]` where `Provenance` is a dataclass `{status: Literal["source","derived","absent"], page: int | None, span: tuple[int,int] | None}`. Keys are concrete JSON paths (`Odwolujacy[0].nip`). `concrete_to_schema(path: str) -> str` maps `[0]`→`[]`.

- [ ] **Step 1: Write failing tests**

```python
def test_locate_finds_literal_value_span():
    pages = ["Odwołujący\nNIP 1234567890\n", "druga strona"]
    fs = {"Odwolujacy[].nip": "text"}
    prov = locate({"Odwolujacy": [{"nip": "1234567890"}]}, fs, pages)
    p = prov["Odwolujacy[0].nip"]
    assert p.status == "source" and p.page == 0
    assert pages[0][p.span[0]:p.span[1]] == "1234567890"

def test_derived_source_is_not_highlighted():
    fs = {"OdwolanieKIO.data": "normalized"}
    prov = locate({"OdwolanieKIO": {"data": "2021-05-10"}}, fs, ["dnia 10 maja 2021 r."])
    assert prov["OdwolanieKIO.data"].status == "derived"

def test_null_and_missing_are_absent():
    fs = {"Odwolujacy[].nip": "text"}
    prov = locate({"Odwolujacy": [{"nip": None}]}, fs, ["x"])
    assert prov["Odwolujacy[0].nip"].status == "absent"

def test_fold_insensitive_match_on_diacritics():
    # fold() preserves positions, so the span indexes back into the ORIGINAL page text
    pages = ["Pełnomocnik: Jan Kowalski"]
    fs = {"pelnomocnik.imie": "text"}
    prov = locate({"pelnomocnik": {"imie": "Jan Kowalski"}}, fs, pages)
    p = prov["pelnomocnik.imie"]
    assert pages[0][p.span[0]:p.span[1]] == "Jan Kowalski"
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_webapp_provenance.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement `locate` and helpers in `webapp/provenance.py`.** Walk `result` to leaves tracking the concrete path; map to schema path via `concrete_to_schema`; look up `field_sources`. For `x-source == "text"` and a non-empty string value, fold the value and each page text, `str.find` the folded value, return the first page+span; otherwise status `derived` (non-text source) or `absent` (null/empty/missing/not found). Spans index into original page text (fold is position-preserving).
- [ ] **Step 4: Run to verify pass** — `pytest tests/test_webapp_provenance.py -v` → PASS.
- [ ] **Step 5: Commit** — `feat(webapp): derive per-field provenance by folded search`.

## Task 2: Ensemble-agreement confidence

**Files:**
- Create: `webapp/confidence.py`
- Test: `tests/test_webapp_confidence.py`

**Interfaces:**
- Consumes: `doxforge.llm.ensemble._key`, output schema (`dict`), list of member answers (`list[JsonValue]`).
- Produces: `agreement(schema: JsonValue, answers: list[JsonValue]) -> dict[str, Level]` keyed by concrete path; `Level = Literal["full","partial","split","none"]`. `"none"` when `len(answers) < 2`. Arrays: match entities across answers on key field (`nip`/`krs`, then fuzzy `name`/`nazwa` via rapidfuzz), vote matched fields; a disagreement on element count marks the array path `"split"`.

- [ ] **Step 1: Write failing tests**

```python
def test_full_agreement_all_members_same():
    ans = [{"a": "X"}, {"a": "X"}, {"a": "X"}]
    assert agreement({"properties": {"a": {}}}, ans)["a"] == "full"

def test_partial_and_split():
    assert agreement({"properties": {"a": {}}}, [{"a":"X"},{"a":"X"},{"a":"Y"}])["a"] == "partial"
    assert agreement({"properties": {"a": {}}}, [{"a":"X"},{"a":"Y"}])["a"] == "split"

def test_single_member_is_no_signal():
    assert agreement({"properties": {"a": {}}}, [{"a": "X"}])["a"] == "none"

def test_array_count_disagreement_flags_split():
    schema = {"properties": {"O": {"items": {"properties": {"nip": {}}}}}}
    ans = [{"O":[{"nip":"1"}]}, {"O":[{"nip":"1"},{"nip":"2"}]}]
    assert agreement(schema, ans)["O"] == "split"
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement `agreement` in `webapp/confidence.py`.** Recurse the schema like `ensemble.vote`. For a leaf: fold each member's value with `_key`, count the winning key; ratio 1.0→`full`, `>0.5`→`partial`, else `split`; `<2` members→`none`. For arrays: if members disagree on length, mark the array path `split`; else match elements by key field and recurse per matched field.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `feat(webapp): compute per-field ensemble agreement`.

## Task 3: Recording LLM client

**Files:**
- Create: `webapp/recording_client.py`
- Test: `tests/test_webapp_recording_client.py`

**Interfaces:**
- Consumes: `doxforge.llm.ensemble.JsonCompleter` (protocol: `complete_json(system,user,schema,schema_name,sampling=None)`, `close()`), `vote`, `doxforge.llm.client.LLMSettings`, `LLMClient`, `Sampling`.
- Produces: `RecordingClient(members: list[tuple[JsonCompleter, int]], temperature: float)` implementing `JsonCompleter`; `.answers: list[JsonValue]` holds the most recent call's member answers; `.member_count: int`. `from_settings(settings: LLMSettings) -> RecordingClient` builds members exactly as `ensemble.build_client` does.

- [ ] **Step 1: Write failing test** (stub completers, no network)

```python
class _Stub:
    def __init__(self, value): self.value = value
    def complete_json(self, *a, **k): return self.value
    def close(self): pass

def test_records_all_member_answers_and_returns_vote():
    c = RecordingClient([(_Stub({"a":"X"}),1),(_Stub({"a":"X"}),1),(_Stub({"a":"Y"}),1)], 0.0)
    out = c.complete_json("s","u",{"properties":{"a":{}}},"n")
    assert out == {"a":"X"}            # majority vote
    assert c.answers == [{"a":"X"},{"a":"X"},{"a":"Y"}]
    assert c.member_count == 3
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement `RecordingClient` in `webapp/recording_client.py`.** Mirror `EnsembleClient.complete_json` (iterate members×samples, tolerate member failures), store answers in `self.answers` (reset at the start of each `complete_json`), return `vote(schema, answers)`. `from_settings` replicates `build_client`'s member construction.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `feat(webapp): recording ensemble client that keeps member votes`.

## Task 4: LLM fallback classifier

**Files:**
- Create: `webapp/classifier_llm.py`
- Test: `tests/test_webapp_classifier_llm.py`

**Interfaces:**
- Consumes: `JsonCompleter`, a list of `(name, description)` for **registered** types only.
- Produces: `classify_llm(text: str, candidates: list[tuple[str,str]], completer: JsonCompleter) -> str | None`. Returns a candidate name or `None` for "none"/unknown. Uses a fixed JSON schema `{choice: enum[names..., "none"]}` via `completer.complete_json`.

- [ ] **Step 1: Write failing test** (stub completer)

```python
class _Pick:
    def __init__(self, choice): self.choice = choice
    def complete_json(self, *a, **k): return {"choice": self.choice}
    def close(self): pass

def test_picks_registered_type():
    assert classify_llm("...", [("faktura","FV")], _Pick("faktura")) == "faktura"

def test_none_choice_returns_none():
    assert classify_llm("...", [("faktura","FV")], _Pick("none")) is None

def test_empty_candidates_short_circuits_without_llm():
    assert classify_llm("...", [], _Pick("faktura")) is None
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement `classify_llm` in `webapp/classifier_llm.py`.** Short-circuit to `None` when no candidates. Build a prompt listing each candidate name+description and the document text, request `{choice}` constrained to the names plus `"none"`; map `"none"`/unknown → `None`.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `feat(webapp): LLM fallback classifier for registered types`.

## Task 5: Type registry (Mode B + Mode A drafting)

**Files:**
- Create: `webapp/registry.py`
- Test: `tests/test_webapp_registry.py`

**Interfaces:**
- Consumes: `doxforge.doc_types.DocTypeConfig`, `DocType`, `load_doc_types`, `settings.DOC_TYPES_DIR`, `JsonCompleter`.
- Produces:
  - `register_type(name, description, schema, directory, example_pdf=None) -> Path` — validates by constructing `DocType(DocTypeConfig(...))`, writes `<directory>/<name>.yaml`, adds `name` to `<directory>/.registered.json`, saves `example_pdf` bytes under `<directory>/examples/<name>.pdf` when given. Raises `ValueError` on collision with an existing type or invalid schema; writes nothing on error.
  - `registered_names(directory) -> list[str]`; `registered_candidates(doc_types, directory) -> list[tuple[str,str]]`.
  - `placeholder_classification() -> dict` — `{min_score: 999.0, rules: [{pattern: "(?!x)x", weight: 0.0}]}` so pattern scoring never selects a registered type (LLM fallback does).
  - `draft_schema(text: str, completer: JsonCompleter) -> dict` — Mode A: LLM proposes a JSON-Schema dict in house style (`x-source`, `description` per field).

- [ ] **Step 1: Write failing tests** (no network for register path)

```python
def test_register_and_reload_roundtrip(tmp_path):
    schema = {"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object",
              "additionalProperties": False, "required":["foo"],
              "properties":{"foo":{"type":["string","null"],"x-source":"text"}}}
    p = register_type("faktura","Faktura VAT", schema, tmp_path)
    assert p.exists()
    assert "faktura" in registered_names(tmp_path)
    assert "faktura" in load_doc_types(tmp_path)   # classifier/extractor see it next run

def test_bad_schema_rejected_and_nothing_written(tmp_path):
    with pytest.raises(ValueError):
        register_type("bad","x", {"type":"not-a-type"}, tmp_path)
    assert not list(tmp_path.glob("*.yaml"))

def test_name_collision_rejected(tmp_path):
    schema = {...}  # minimal valid, as above
    register_type("faktura","a",schema,tmp_path)
    with pytest.raises(ValueError):
        register_type("faktura","b",schema,tmp_path)
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement `webapp/registry.py`.** Assemble a config dict (`name`, `description`, `schema`, `classification=placeholder_classification()`); construct `DocTypeConfig.model_validate` then `DocType(...)` to validate (raises on bad schema); on success dump YAML and update `.registered.json`; `draft_schema` calls the completer with a house-style prompt. Collision = name already a `*.yaml` in the directory.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `feat(webapp): self-service doc-type registration with LLM drafting`.

## Task 6: Run orchestration

**Files:**
- Create: `webapp/run.py`
- Test: `tests/test_webapp_run.py`

**Interfaces:**
- Consumes: `pipeline.prepare/finish`, `classify.classify`, Tasks 1–5.
- Produces:
  - `CaseResult` dataclass: `result: JsonValue`, `doc_class: str | None`, `pages: list[str]`, `provenance: dict[str, Provenance]`, `confidence: dict[str, Level]`, `member_count: int`.
  - `run_pdf(pdf: Path, doc_types, directory, client: RecordingClient | None) -> CaseResult`.
  - `assemble(result, doc_class, doc_types, pages, client) -> CaseResult` — pure glue (provenance+confidence from already-computed pieces) so it is unit-testable without a PDF.

- [ ] **Step 1: Write failing test** for the pure `assemble` (build `pages`/`result` by hand, pass a `RecordingClient` whose `.answers` is pre-populated, assert `provenance` and `confidence` keys/levels are wired through).
- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement `webapp/run.py`.** `run_pdf`: `prepare()`; if it returns `None` (pattern classify found nothing) and a client + registered candidates exist, `classify_llm` on `document.text`; if that names a type, re-extract with it. Call `finish()`; read `client.answers`/`member_count`. Build provenance (`locate`) and confidence (`agreement`). `assemble` holds the pure part the test drives.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `feat(webapp): run a PDF to result + provenance + confidence`.

## Task 7: Streamlit app (UI)

**Files:**
- Create: `app.py`, `requirements-web.txt`
- Modify: `README.md` (one "Web GUI" section: `pip install -r requirements-web.txt && streamlit run app.py`)

**Interfaces:**
- Consumes: Tasks 1–6, `load_doc_types`, `load_llm_settings`, `RecordingClient.from_settings`.

No automated UI tests; verification is manual (steps below).

- [ ] **Step 1: Implement `app.py`.** Sidebar: LLM status (enabled/`none`) and selected ensemble members. Tabs: **Viewer** (upload one PDF or a case folder → run → left pane per-page text with the selected field's span highlighted; right pane JSON tree where clicking a leaf sets the selected path in `st.session_state`; each leaf shows a confidence dot green/yellow/red, a "rules" badge where no member produced the value, and a "no signal" note when `member_count < 2`; JSON download button). **Register type**: Mode B form (name, description, schema textarea, optional example PDF) and Mode A (upload PDF → `draft_schema` → editable table → save), both via `register_type`.
- [ ] **Step 2: Add `requirements-web.txt`** with `streamlit` pinned.
- [ ] **Step 3: Manual verification** — `pip install -r requirements-web.txt && streamlit run app.py`; upload a known odwołanie → fields highlight to correct spans, confidence dots render; register a type (Mode B) → drop a matching PDF → it classifies (LLM fallback) and extracts; set `DOXFORGE_LLM=none` → runs rules-only with confidence/Mode A disabled.
- [ ] **Step 4: Commit** — `feat(webapp): Streamlit transparency viewer, confidence, type registration`.

## Task 8: Final guards

**Files:** none new.

- [ ] **Step 1:** Run `ruff format --check . && ruff check . && mypy doxforge && pytest` → all pass. (Add `# type: ignore`/config only if Streamlit stubs force it; do not weaken `doxforge` checks.)
- [ ] **Step 2:** Run `python -m doxforge run --input input --output /tmp/out` (rules-only) → unchanged behaviour, no import errors from the new package.
- [ ] **Step 3: Commit** — `chore(webapp): lint/type/test pass`.

---

## Self-review notes

- **Spec coverage:** #1 Viewer → Tasks 1,7; #2 Confidence → Tasks 2,3,7; #3 Registration + LLM classify → Tasks 4,5,6,7; #4 Upload & run → Tasks 6,7. Graceful `none` mode → Constraints + Tasks 6,7. No-core-edits → Constraints + Task 8.
- **Review Focus:** each of the five lines has a test in Task 1, 2, or 5.
- **Proportion:** plan gives signatures + test assertions, not function bodies; comparable in length to the spec.
