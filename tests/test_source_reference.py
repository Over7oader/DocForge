import json
from pathlib import Path

from doxforge.ingest import Document, Page, TextSource
from scripts import source_reference


def test_reference_format_uses_source_and_never_reads_prediction(tmp_path: Path, monkeypatch):
    run = tmp_path / "run"
    (run / "gold").mkdir(parents=True)
    reference = {"przelew_bankowy": {"z_rachunku": "05470680287581539721455024", "na_rachunek": None}}
    original = json.dumps(reference)
    (run / "gold/a.json").write_text(original)
    monkeypatch.setattr(
        source_reference,
        "load_document",
        lambda pdf: Document([Page("PL05 4706 8028 7581 5397 2145 5024", TextSource.TEXT_LAYER)]),
    )
    target = tmp_path / "separate-reference"
    changes = source_reference.prepare_reference(run, target)
    assert len(changes) == 1
    assert json.loads((target / "a.json").read_text())["przelew_bankowy"]["z_rachunku"].startswith("PL")
    assert (run / "gold/a.json").read_text() == original
    assert not (run / "output").exists()
