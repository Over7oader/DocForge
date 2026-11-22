import json
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from doxforge import ingest, pipeline
from doxforge.doc_types import load_doc_types


def _blank_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pdfium.PdfDocument.new() as document:
        document.new_page(595, 842).close()
        document.save(path)


def test_run_mirrors_input_tree_and_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # puste strony nie mają warstwy tekstu; OCR zastępujemy, żeby test nie ładował modeli
    monkeypatch.setattr(ingest, "_cached_ocr", lambda pdf, indexes, pace: dict.fromkeys(indexes, ""))
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    _blank_pdf(input_dir / "case1" / "odwolanie_nr_1.pdf")
    _blank_pdf(input_dir / "case2" / "Skan.PDF")
    (input_dir / "case2" / "notatka.txt").write_text("nie PDF", encoding="utf-8")

    assert pipeline.run(input_dir, output_dir, load_doc_types()) == 2
    produced = sorted(p.relative_to(output_dir).as_posix() for p in output_dir.rglob("*") if p.is_file())
    assert produced == ["case1/odwolanie_nr_1.json", "case2/Skan.json"]
    assert json.loads((output_dir / "case1" / "odwolanie_nr_1.json").read_text(encoding="utf-8")) == {}
