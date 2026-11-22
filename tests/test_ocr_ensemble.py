import hashlib
from pathlib import Path

import numpy as np
import pytest

from doxforge import ingest, ocr, settings
from doxforge.ocr_consensus import OCRLine, consensus


def line(engine, text, score=0.95, top=10, bounds=(0, 500)):
    left, right = bounds
    return OCRLine(engine, text, score, left, top, right, top + 20)


def test_majority_recovers_missing_line_without_character_splicing():
    candidates = [
        line("ppocr_v6", "a   i as ( a ai", 0.65),
        line("ppocr_v5", "1. postanowien Opisu Przedmiotu Zamówienia (OPZ)", 0.98),
        line("tesseract", "1. postanowień Opisu Przedmiotu Zamówienia (OPZ)", 0.94),
    ]
    result = consensus(candidates)
    assert len(result) == 1
    assert result[0].votes == 2
    assert result[0].selected == candidates[2]


def test_disagreeing_digits_never_count_as_agreement_or_form_new_identifier():
    candidates = [
        line("ppocr_v6", "REGON: 123456781", 0.96),
        line("ppocr_v5", "REGON: 123456782", 0.99),
        line("tesseract", "REGON: 123456783", 0.98),
    ]
    result = consensus(candidates)
    assert len(result) == 1
    assert result[0].votes == 1
    assert result[0].selected == candidates[0]


def test_physical_rows_and_distinct_columns_are_not_cross_matched():
    candidates = [
        line("ppocr_v6", "Firma A", top=10, bounds=(0, 100)),
        line("tesseract", "Firma B", top=10, bounds=(300, 500)),
        line("ppocr_v5", "Firma A", top=40, bounds=(0, 100)),
    ]
    result = consensus(candidates)
    assert len(result) == 3
    assert all(row.votes == 1 for row in result)


def test_independent_high_confidence_observation_can_fill_missing_primary():
    result = consensus(
        [
            line("tesseract", "ABC/41/2030", 0.96),
            line("ppocr_v5", "s   a   x   e", 0.6, top=50),
        ]
    )
    assert len(result) == 1
    assert result[0].selected.text == "ABC/41/2030"


def test_duplicated_engine_output_is_not_an_extra_vote():
    observation = line("ppocr_v6", "REGON: 123456789")
    result = consensus([observation, observation, line("tesseract", "REGON: 123456788")])
    assert len(result) == 1
    assert result[0].votes == 1
    assert result[0].selected == observation


def test_split_table_cells_are_one_engine_vote_and_do_not_duplicate_full_row():
    candidates = [
        line("ppocr_v6", "Amount   Date   Reference", bounds=(0, 500)),
        line("ppocr_v5", "Amount   Date   Reference", bounds=(0, 500)),
        line("tesseract", "Amount", bounds=(0, 70)),
        line("tesseract", "Date", bounds=(200, 250)),
        line("tesseract", "Reference", bounds=(400, 500)),
    ]
    result = consensus(candidates)
    assert len(result) == 1
    assert result[0].selected.text == "Amount   Date   Reference"
    assert result[0].votes == 3


def test_overlapping_fragments_from_same_engine_cannot_cast_multiple_votes():
    result = consensus(
        [
            line("ppocr_v6", "Reference: AB123456"),
            line("tesseract", "Reference: AB123456"),
            line("tesseract", "AB123456", bounds=(300, 500)),
        ]
    )
    assert len(result) == 1
    assert result[0].votes == 2


def test_tesseract_initialization_failure_is_reported(monkeypatch):
    monkeypatch.setenv("DOXFORGE_OCR", "ensemble3")

    def fail():
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(ocr, "_tesseract", fail)
    quad = np.asarray([[[0, 10], [100, 10], [100, 30], [0, 30]]], dtype=float)
    monkeypatch.setattr(
        ocr, "_timed", lambda image, engine, threads: (ocr.Observations(quad, ["Tekst"], [0.97]), 0.01)
    )
    result = ocr.recognize_page(np.zeros((50, 150, 3), dtype=np.uint8))
    assert result.text == "Tekst"
    assert result.failed == ("tesseract",)


def test_tesseract_retains_native_line_and_low_confidence_words():
    tsv = "\n".join(
        [
            "5\t1\t1\t1\t1\t1\t10\t10\t70\t20\t96\tNumer:",
            "5\t1\t1\t1\t1\t2\t90\t5\t100\t30\t40\tAB/91/2031",
            "5\t1\t1\t1\t2\t1\t10\t50\t90\t20\t95\tInny wiersz",
        ]
    )
    result = ocr._parse_tsv(tsv)
    assert result.texts == ["Numer: AB/91/2031", "Inny wiersz"]
    assert len(result.quads) == 2


def test_mode_has_separate_cache_and_is_loaded_after_dotenv(monkeypatch):
    monkeypatch.setenv("DOXFORGE_OCR", "single")
    old = ocr.cache_version()
    assert old == settings.OCR_CACHE_VERSION
    monkeypatch.setenv("DOXFORGE_OCR", "ensemble3")
    new = ocr.cache_version()
    assert new != old
    monkeypatch.setattr(settings, "OCR_DPI", 250)
    assert ocr.cache_version() != new
    monkeypatch.setenv("DOXFORGE_OCR", "typo")
    with pytest.raises(ValueError):
        ocr.load_ocr_settings()


def test_degraded_ensemble_is_returned_but_not_cached(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"test")
    cache = tmp_path / "cache"
    monkeypatch.setattr(settings, "OCR_CACHE_DIR", cache)
    monkeypatch.setattr(ocr, "cache_version", lambda: "test-ensemble")
    pages = {0: ocr.PageOCR("usable partial", [], {}, ("tesseract",)), 1: ocr.PageOCR("complete", [], {}, ())}
    monkeypatch.setattr(ingest, "_ocr_pages", lambda pdf, indexes, pace: pages)
    assert ingest._cached_ocr(pdf, [0, 1], None) == {0: "usable partial", 1: "complete"}
    cache_file = cache / f"{hashlib.sha256(b'testtest-ensemble').hexdigest()}.json"
    assert '"0"' not in cache_file.read_text()
    assert '"1"' in cache_file.read_text()


def test_one_engine_failure_preserves_other_observations(monkeypatch):
    monkeypatch.setenv("DOXFORGE_OCR", "ensemble3")
    monkeypatch.setattr(ocr, "_tesseract", object)
    quad = np.asarray([[[0, 10], [100, 10], [100, 30], [0, 30]]], dtype=float)

    def fake(image, engine, threads):
        if engine == "ppocr_v5":
            raise RuntimeError("simulated engine failure")
        return ocr.Observations(quad, ["Przykładowy tekst"], [0.97]), 0.01

    monkeypatch.setattr(ocr, "_timed", fake)
    result = ocr.recognize_page(np.zeros((50, 150, 3), dtype=np.uint8))
    assert result.text == "Przykładowy tekst"
    assert result.failed == ("ppocr_v5",)
    assert result.decisions[0].votes == 2


def test_pace_keeps_ensemble_while_remaining_pages_fit_and_degrades_when_late(monkeypatch):
    monkeypatch.setattr(settings, "OCR_ENSEMBLE_PAGE_S", 16.0)
    monkeypatch.setattr(settings, "OCR_SINGLE_PAGE_S", 6.0)
    # 10 stron: ensemble dla bieżącej (16 s) + 9 x 6 s = 70 s.
    assert ocr.OCRPace(10, 100).use_ensemble()
    assert not ocr.OCRPace(10, 60).use_ensemble()
    pace = ocr.OCRPace(2, 22.5)
    assert pace.use_ensemble()  # 16 + 6 <= 22,5
    pace.record(ensemble=True, seconds=40)  # wolniejsza maszyna podnosi oszacowanie
    assert pace.remaining == 1
    assert pace.ensemble_seconds > 16.0
    assert not pace.use_ensemble()


def test_degraded_page_is_returned_but_not_cached_as_ensemble(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"pdf bytes")
    monkeypatch.setattr(settings, "OCR_CACHE_DIR", tmp_path / "cache")
    page = ocr.PageOCR("single engine text", [], {}, (), degraded=True)
    monkeypatch.setattr(ingest, "_ocr_pages", lambda pdf, indexes, pace: {0: page})
    assert ingest._cached_ocr(pdf, [0], None) == {0: "single engine text"}
    monkeypatch.setattr(
        ingest, "_ocr_pages", lambda pdf, indexes, pace: {0: ocr.PageOCR("ensemble", [], {}, ())}
    )
    assert ingest._cached_ocr(pdf, [0], None) == {0: "ensemble"}


def test_diacritics_seen_by_another_engine_are_restored_without_other_changes():
    candidates = [
        line("ppocr_v6", "udziela pelnomocnictwa: r. pr. Joanna Stepień (KR-441o)", 0.98),
        line("ppocr_v5", "udziela pelnomocnictwa: r. pr. Joanna Stepień (KR-441o)", 0.97),
        line("tesseract", "udziela pełnomocnictwa: r. pr. Joanna Stępień (KR-4410)", 0.96),
    ]
    [decision] = consensus(candidates)
    # Ogonki z Tesseracta, ale „441o” (różnica cyfr/liter) zostaje z wybranego odczytu.
    assert decision.selected.text == "udziela pełnomocnictwa: r. pr. Joanna Stępień (KR-441o)"


def test_diacritics_are_never_added_to_email_addresses_or_unrelated_letters():
    candidates = [
        line("ppocr_v6", "e-mail: biuro@kowalczyk.example, Lodz", 0.98),
        line("tesseract", "e-mail: biuro@kowałczyk.example, Łódź", 0.96),
    ]
    [decision] = consensus(candidates)
    assert decision.selected.text == "e-mail: biuro@kowalczyk.example, Łódź"
