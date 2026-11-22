"""DoxForge web GUI (Streamlit): transparency viewer, ensemble confidence, type registration.

Run: `pip install -r requirements.txt -r requirements-web.txt && streamlit run app.py`.
All logic lives in `webapp/`; this file only wires it to the browser.
"""

import html
import json
import tempfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from doxforge import settings
from doxforge.doc_types import DocType, load_doc_types
from doxforge.ingest import load_document
from doxforge.llm.client import LLMSettings, load_llm_settings
from webapp.provenance import Provenance, leaves
from webapp.recording_client import RecordingClient, from_settings
from webapp.registry import draft_schema, register_type
from webapp.run import CaseResult, run_pdf

st.set_page_config(page_title="DoxForge - Odwolania2json", layout="wide")

_DOT = {"full": "🟢", "partial": "🟡", "split": "🔴", "none": "⚪", "rules": "🔧"}


# --- helpers ----------------------------------------------------------------
def _highlight(text: str, span: tuple[int, int]) -> str:
    start, end = span
    before, hit, after = text[:start], text[start:end], text[end:]
    return (
        html.escape(before)
        + f"<mark style='background:#fde68a'>{html.escape(hit)}</mark>"
        + html.escape(after)
    )


def _make_client() -> RecordingClient | None:
    llm = load_llm_settings()
    return from_settings(llm) if llm is not None else None


# --- viewer -----------------------------------------------------------------
def _render_viewer(case: CaseResult) -> None:
    if case.doc_class is None:
        st.warning("Dokument nie należy do żadnej skonfigurowanej klasy — wynik to puste `{}`.")
        st.json(case.result)
        return

    value_map = dict(leaves(case.result))
    st.caption(f"Klasa: **{case.doc_class}**")

    left, right = st.columns([3, 2])
    with right:
        st.subheader("Pola (kliknij, aby podświetlić źródło)")
        for path, value in value_map.items():
            dot = _DOT[case.confidence.get(path, "none")]
            label = f"{dot} {path} = {json.dumps(value, ensure_ascii=False)}"
            if st.button(label, key=f"f_{path}", use_container_width=True):
                st.session_state.selected = path
        st.download_button(
            "Pobierz JSON",
            json.dumps(case.result, ensure_ascii=False, indent=2),
            file_name=f"{case.doc_class}.json",
            mime="application/json",
        )
        st.caption("🟢 pełna zgoda · 🟡 większość · 🔴 rozbieżność · ⚪ brak sygnału · 🔧 z reguł")

    with left:
        st.subheader("Dokument")
        selected = st.session_state.get("selected")
        prov: Provenance | None = case.provenance.get(selected) if selected else None
        if selected:
            if prov and prov.status == "source" and prov.page is not None:
                st.caption(f"Pole **{selected}** — strona {prov.page + 1}")
            elif prov and prov.status == "absent":
                st.caption(f"Pole **{selected}** — brak wartości w tekście")
        for page_index, text in enumerate(case.pages):
            body = (
                _highlight(text, prov.span)
                if prov and prov.status == "source" and prov.page == page_index and prov.span
                else html.escape(text)
            )
            st.markdown(f"**Strona {page_index + 1}**", help=None)
            st.markdown(
                f"<div style='white-space:pre-wrap;font-size:0.85rem'>{body}</div>",
                unsafe_allow_html=True,
            )


# --- app --------------------------------------------------------------------
def main() -> None:
    load_dotenv(settings.ENV_FILE)  # like the CLI: read .env (real env vars take precedence)
    doc_types = load_doc_types()
    llm = load_llm_settings()

    with st.sidebar:
        st.title("DoxForge")
        if llm is None:
            st.info("LLM wyłączony (`DOXFORGE_LLM=none`): tryb regułowy, bez pewności ensemble i Mode A.")
        else:
            members = [llm.model, *llm.extra_models]
            st.success(f"LLM: {', '.join(members)} x{llm.samples}")
        st.caption("Klasy: " + ", ".join(doc_types))

    viewer_tab, register_tab = st.tabs(["Podgląd ekstrakcji", "Zarejestruj typ"])

    with viewer_tab:
        uploaded = st.file_uploader(
            "Wrzuć PDF (jeden lub cała sprawa)", type=["pdf"], accept_multiple_files=True
        )
        if uploaded and st.button("Uruchom ekstrakcję", type="primary"):
            st.session_state.pop("selected", None)
            client = _make_client()
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    cases = []
                    for file in uploaded:
                        pdf = Path(tmp) / file.name
                        pdf.write_bytes(file.getbuffer())
                        with st.spinner(f"Przetwarzanie {file.name}…"):
                            cases.append((file.name, run_pdf(pdf, doc_types, settings.DOC_TYPES_DIR, client)))
                    st.session_state.cases = cases
            finally:
                if client is not None:
                    client.close()

        cases = st.session_state.get("cases", [])
        if cases:
            names = [name for name, _ in cases]
            chosen = st.selectbox("Dokument", names) if len(names) > 1 else names[0]
            case = dict(cases)[chosen]
            _render_viewer(case)

    with register_tab:
        _render_register(doc_types, llm)


def _render_register(doc_types: dict[str, DocType], llm: LLMSettings | None) -> None:
    st.write("Dodaj nowy typ dokumentu — zapisuje jeden plik `config/doc_types/<nazwa>.yaml`.")
    mode = st.radio("Tryb", ["Podaj strukturę (Mode B)", "AI proponuje strukturę (Mode A)"])

    draft = ""
    if mode.startswith("AI"):
        if llm is None:
            st.info("Mode A wymaga włączonego LLM.")
        pdf = st.file_uploader("PDF nowego typu", type=["pdf"], key="draft_pdf")
        if pdf and llm is not None and st.button("Zaproponuj schemat"):
            client = _make_client()
            assert client is not None  # llm is not None -> _make_client built one
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / pdf.name
                    path.write_bytes(pdf.getbuffer())
                    with st.spinner("Model proponuje schemat…"):
                        schema = draft_schema(load_document(path).text, client)
                st.session_state.draft_schema = json.dumps(schema, ensure_ascii=False, indent=2)
            except Exception as error:  # a model hiccup must not crash the page
                st.error(f"Nie udało się zaproponować schematu: {error}. Spróbuj ponownie lub użyj Mode B.")
            finally:
                client.close()
        draft = st.session_state.get("draft_schema", "")

    name = st.text_input("Nazwa typu (małe litery, cyfry, _)", key="reg_name")
    description = st.text_input("Opis (używany przez klasyfikator LLM)", key="reg_desc")
    schema_text = st.text_area("JSON Schema wyniku", value=draft, height=300, key="reg_schema")
    example = st.file_uploader("Przykładowy PDF (opcjonalnie)", type=["pdf"], key="reg_example")

    if st.button("Zapisz typ", type="primary"):
        try:
            schema = json.loads(schema_text)
        except json.JSONDecodeError as error:
            st.error(f"Niepoprawny JSON schematu: {error}")
            return
        try:
            path = register_type(
                name.strip(),
                description.strip(),
                schema,
                settings.DOC_TYPES_DIR,
                example_pdf=example.getbuffer().tobytes() if example else None,
            )
        except ValueError as error:
            st.error(str(error))
            return
        st.success(f"Zapisano {path.name}. Typ będzie rozpoznawany (klasyfikator LLM) przy kolejnym wgraniu.")


if __name__ == "__main__":
    main()
