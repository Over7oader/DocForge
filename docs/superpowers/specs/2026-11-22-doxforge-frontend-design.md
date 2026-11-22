# DoxForge Frontend — Design

Date: 2026-11-22
Status: awaiting approval

## Intent and why

A Streamlit web GUI on top of the existing DoxForge (Odwołania2json) pipeline that makes
extraction **transparent**, surfaces **ensemble confidence**, and lets users **register new
document types from the browser** without touching code. The payoff is hackathon scoring:
the GUI points plus the explicitly rewarded "transparentność procesu ekstrakcji", and a
30-second demo (drop a PDF → see every value with its source and agreement colour; register
a brand-new type → drop a matching PDF → watch it extract).

## Goals

1. Transparency Viewer: upload a PDF, run extraction, show per-page document text beside the
   extracted JSON tree; click a JSON field → highlight the text it came from (page + char span).
2. Ensemble-agreement confidence: per-field colour from how many ensemble members agreed.
3. Self-service type registration: Mode B (user supplies JSON schema) and Mode A (LLM drafts a
   schema from a PDF), both writing one `config/doc_types/<name>.yaml`.
4. Upload & run from the browser (single file or a whole case folder), with JSON download.

## Non-goals

- Pixel-accurate bounding-box highlighting over a rendered PDF image. Confirmed with user:
  text-highlight (page + character span) is enough.
- Changing the CLI/eval pipeline behaviour, the scoring, or the existing three doc types.
- Auth, multi-user, persistence beyond the config files the app already uses.
- Editing `doxforge/` core modules where a non-invasive wrapper in the webapp will do.

## Anti-goals (would be a failure even if it "works")

- A confidence badge that lies: showing "high confidence" when only one model ran, or a green
  "found in source" highlight for a value that was derived/normalized and is not in the text.
- A registered type that silently never classifies, making the killer demo fall flat.
- Breaking `python -m doxforge run` / the 172 tests / ruff / mypy.

## Constraints

- Python 3.13+; add `streamlit` to a separate `requirements-web.txt` (keep core deps clean).
- Reuse existing code: `prepare()`/`finish()` (pipeline.py), `fold()` (text.py), `load_doc_types()`
  (doc_types.py), `build_client()` (ensemble.py), `DocType.field_sources`.
- LLM is optional and may be `none`; every feature must degrade gracefully without it
  (rules-only run, no confidence signal, Mode A disabled, LLM-fallback classifier skipped).
- The project lives in `DoxForge1/DoxForge/` (an extracted copy, currently untracked in the
  outer repo). No commits unless asked.

## Approaches considered

**Stack.**
- *Streamlit (chosen).* Stateful widgets, two-column layout, `st.file_uploader`, pure Python.
  Fits the viewer (click-field-to-highlight via session state) and ships fast.
- *Gradio (rejected).* Great for single input→output demos, clumsier for a persistent
  two-pane viewer with click-driven cross-highlighting.
- *Custom web (React/FastAPI) (rejected).* A separate frontend build is too slow for a hackathon
  and buys nothing the judges reward here.

**Provenance.**
- *Derive by folded search (chosen).* `fold()` is length- and position-preserving, so folding a
  leaf value and each page's text and searching yields `{page, [start,end]}` that maps back to
  the original text. Zero backend storage, reuses the grounding idea already in the codebase.
- *Persist provenance in the pipeline (rejected).* Would touch core extraction/merge for every
  value and risk the eval path; unnecessary for a text-highlight viewer.

**Confidence.**
- *Record member answers in a client wrapper (chosen).* A thin `JsonCompleter` wrapper collects
  every member's answer (which `EnsembleClient` currently discards) and, after the final merge,
  per-field agreement is computed over those answers along the schema. Non-invasive.
- *Modify `EnsembleClient` to return votes (rejected).* Changes a core signature used by the CLI;
  more blast radius for the same result.

**New-type classification (decided with user: LLM fallback).**
- *LLM fallback (chosen).* When pattern scoring returns no class, ask the LLM to pick among the
  registered types by name+description, with an explicit "none" option so unknown/negative docs
  still return `{}`. Runs only as a fallback, so the existing three types and the negatives path
  are untouched. Matches the spec's "recognized on the next upload" narrative.
- *Auto-derive patterns from the example PDF (rejected).* Pure rules, offline, but fragile — the
  demo can misfire when title words don't generalize.
- *Ask the user for keywords (rejected).* Deterministic but less compelling, and the user chose
  the LLM path.

## How it works (the parts decided)

### Module layout (new; no edits to `doxforge/` core expected)
```
DoxForge1/DoxForge/
  app.py                     # Streamlit entry: `streamlit run app.py`
  webapp/
    __init__.py
    run.py                   # run one/many PDFs via prepare()/finish(); returns result + captured votes
    provenance.py            # leaf-path walk + folded-search → {path: {page, span}} or "derived"
    confidence.py            # per-path agreement from recorded member answers
    recording_client.py      # JsonCompleter wrapper that records every member answer
    classifier_llm.py        # LLM fallback: pick a registered type by name+description, or "none"
    registry.py              # write/validate config/doc_types/<name>.yaml; Mode A schema drafting
  requirements-web.txt       # streamlit (+ anything web-only)
```

### #4 Upload & run
`app.py` writes uploads to a temp case dir, then `webapp/run.py` loads doc types once, builds a
recording client (if LLM enabled), and for each PDF calls `prepare()` then `finish()`. Output JSON
is shown in the viewer and offered for download. A whole-folder upload groups files as one case.

### #1 Transparency Viewer
Left: document text per page (from `Document.pages`), with the selected field's span highlighted.
Right: the JSON tree; clicking a leaf sets the selected path in session state. `provenance.py`
walks the result with concrete indices (`Odwolujacy[0].nip`), folds the leaf string and each page,
searches, and returns `{page, [start,end]}`. Values with `x-source` in {`normalized`,`derived`} or
no verbatim match get a neutral "derived — not a literal source span" badge instead of a highlight.

### #2 Ensemble confidence
`recording_client.py` wraps each member client, records every answer per document, then
`confidence.py` walks the schema (reusing the `vote()`/`_key` folding idea) and, per leaf, counts
how many members produced the winning folded value → green (all), yellow (majority), red (split).
A separate badge marks fields whose final value came from **rules** (merge policy / LLM absent),
and the whole panel shows "single model — no agreement signal" when one member ran. Array sections
are matched on a key field (NIP/KRS then fuzzy name) before field-by-field voting; a disagreement
on entity count is itself flagged low-confidence.

### #3 Type registration
`registry.py` builds a `DocTypeConfig`-shaped dict and writes `config/doc_types/<name>.yaml`,
validating by constructing `DocType` before saving (same checks the app start uses) so a bad schema
is rejected in the form, not at next run. Mode B: user provides name, description, JSON schema, and
an example PDF (kept as a reference). Mode A: the LLM reads an uploaded PDF and drafts a schema in
the house style (field name/type/`x-source`/description), shown as an editable table before save.
Classification for registered types is handled by the LLM fallback (see above); the three built-in
types keep their pattern-based classification. "None of the above" still returns `{}`.

## Left to the builder

- Exact Streamlit widget choices for the JSON tree + click handling (e.g. nested expanders vs a
  flat clickable path list); either is fine as long as click→highlight works.
- Precise colour thresholds for yellow vs red (majority rule, documented in `confidence.py`).
- The Mode A drafting prompt wording and how many fields it proposes by default.
- Which ensemble members the app defaults to for the demo (2–3 small models via `OPENROUTER_MODEL`).

## Verification

- `python -m doxforge run` on `input/`, the test suite, ruff, and mypy still pass unchanged.
- Manual: upload a known odwołanie → fields highlight to the right spans; confidence badges render;
  register a new type (Mode B) → drop a matching PDF → it classifies and extracts; rules-only mode
  (`DOXFORGE_LLM=none`) runs with confidence/Mode A gracefully disabled.
