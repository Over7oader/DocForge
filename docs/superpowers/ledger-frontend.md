# SDD ledger — plan: docs/superpowers/plans/2026-11-22-doxforge-frontend.md

Executor: inline (native), no worktree.
Setup Ruling: skip git worktree + per-task commits — the DoxForge project is untracked inside the outer Hacknation2026 repo, so a worktree would not contain it and committing would pollute the outer repo with a 2 GB tree. Cost if wrong: no per-task git checkpoints; mitigated by running the full suite after each task.
Baseline: `python -m pytest -q` → 259 passed, 1 failed (tests/test_budget.py::test_deadline_covers_local_ocr_without_llm_and_preserves_valid_outputs) — pre-existing, timing-dependent, unrelated to webapp.
Pre-flight: tasks share interfaces via webapp/ modules; Task 6 consumes 1-5, Task 7 consumes 1-6. Signatures pinned in plan; no conflicts found.

Task 1: complete — webapp/provenance.py (locate, concrete_to_schema, Provenance). tests/test_webapp_provenance.py 6/6 RED→GREEN. Full suite: `pytest -q` → 265 passed, 1 pre-existing fail (test_budget).

Task 2: complete — webapp/confidence.py (agreement, entity matching). tests/test_webapp_confidence.py 6/6 RED→GREEN. Full suite: 271 passed, 1 pre-existing fail.

Task 3: complete — webapp/recording_client.py (RecordingClient, from_settings). tests/test_webapp_recording_client.py 5/5 RED→GREEN. Ruling: test stub must raise an LLM failure type (httpx/Validation/JSONDecode), not bare ValueError — production deliberately only tolerates those, matching EnsembleClient. Full suite: 276 passed, 1 pre-existing fail.

Task 4: complete — webapp/classifier_llm.py (classify_llm, enum = candidates+none, text capped 6000). tests/test_webapp_classifier_llm.py 5/5 RED→GREEN. Full suite: 281 passed, 1 pre-existing fail.

Task 5: complete — webapp/registry.py (register_type, registered_names/candidates, placeholder_classification, draft_schema). tests/test_webapp_registry.py 8/8 RED→GREEN. Full suite: 289 passed, 1 pre-existing fail.

Task 6: complete — webapp/run.py (CaseResult, assemble, run_pdf with LLM-classify fallback). tests/test_webapp_run.py 5/5 RED→GREEN. Full suite: 294 passed, 1 pre-existing fail.

Task 7: complete — app.py (Streamlit viewer/confidence/registration), requirements-web.txt, README GUI section. Ruling: added "rules" to confidence.Level and CaseResult._mark_rules_sourced so a field filled by rules never shows a false green (anti-goal). Verified: app imports, headless boot HTTP 200 + health ok (DOXFORGE_LLM=none), run_pdf on real przyklad_03/odwolanie.pdf -> class odwolanie, 6 source-highlighted fields with correct spans. app.py streamlit import type-ignored (no stubs).

Task 8: complete — my 14 files: ruff check + ruff format + mypy all clean. Full suite 295 passed, 1 pre-existing (test_budget). CLI `python -m doxforge run` (rules-only) on przyklad_03 still produces correct JSON (webapp not imported by core).
Ruling: added "docs" to ruff extend-exclude (ruff formats python inside my .md plan; specs/plans are not lint targets). Cost if wrong: none for code.
Final: pre-existing repo lint not fixed (out of scope): scripts/gliner_cpu_probe.py:24,34 E501 (optional research script). The whole DoxForge1/ tree is untracked in the outer repo, so there is no git baseline — these were in the extracted copy.

Final review (fresh subagent): 1 Critical, 2 Important, 3 Minor.
Final: fixed Critical (positional rules/confidence misattribution + false-green on reordered multi-entity arrays) — made confidence result-driven and key-matched (webapp/confidence.py agreement(schema,result,answers)); test_reordered_rules_value_is_not_false_green RED→GREEN.
Final: fixed Important-1 (array-count split never shown) — count disagreement now marks each result leaf "split"; test_array_count_disagreement_marks_leaves_split RED→GREEN.
Final: fixed Important-2 (agreed-null showed green) — leaf with null result => "none", rule-filled while members null => "rules"; tests test_rules_value_when_members_agree_null / test_null_result_is_no_signal_not_green RED→GREEN.
Final: fixed Minor-1 (registered-type confidence leak) — run_pdf uses extraction answers only when doc_type.config.llm is set; test_run_pdf_registered_type_has_no_bogus_confidence RED→GREEN.
Final: fixed Minor-2 (classify fallback not fault-tolerant) — run_pdf wraps classify_llm in _LLM_FAILURES => unclassified.
Final: minor (deferred): confidence._match returns None for unmatched entities, injecting None into leaf voting for sparsely-present entities (edge case, conservative — lowers confidence, never false green).
Final suite: ruff + ruff format + mypy clean on 8 webapp/app files; pytest 300 passed, 1 pre-existing (test_budget). Real-PDF run_pdf OK (odwolanie, 14 source spans).
Ruling: keep docs/superpowers workspace (ledger/plan/spec) — there is no git history (DoxForge1 untracked), so these are the only record.
