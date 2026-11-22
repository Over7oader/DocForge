import numpy as np
import pytest

from doxforge import ingest
from doxforge.classify import ClassificationConfig, ClassificationRule, classify
from doxforge.ingest import Document, Page, TextSource, _clean
from doxforge.ocr import _boxes, _lines
from doxforge.text import fold

CONFIGS = {
    "odwolanie": ClassificationConfig.model_validate(
        {
            "min_score": 6,
            "rules": [
                {"pattern": r"^\s*odwo.anie\s*$", "weight": 4},
                {"pattern": "prezes\\w* krajowej izby odwo.awczej", "weight": 2},
                {"pattern": r"sygn\. akt", "weight": -8, "zone": "title"},
            ],
        }
    ),
    "pelnomocnictwo": ClassificationConfig.model_validate(
        {"min_score": 5, "rules": [{"pattern": "pelnomocnictw", "weight": 5, "zone": "title"}]}
    ),
}


def doc(text: str) -> Document:
    return Document([Page(text, TextSource.TEXT_LAYER)])


def test_classify_by_content():
    assert classify(doc("Prezes Krajowej Izby Odwoławczej\nODWOŁANIE\n"), CONFIGS) == "odwolanie"
    assert classify(doc("PEŁNOMOCNICTWO\nMocodawca"), CONFIGS) == "pelnomocnictwo"


def test_negative_signal_rejects_similar_document():
    ruling = "Sygn. akt: KIO 2617/21\nPOSTANOWIENIE\nPrezes Krajowej Izby Odwoławczej\nODWOŁANIE\n"
    assert classify(doc(ruling), CONFIGS) is None


def test_below_threshold_is_unclassified():
    assert classify(doc("Broszura JPK"), CONFIGS) is None


def test_clean_removes_page_numbers_and_spaced_titles():
    text = "polegających na:\nStrona 1 z 11\n- 2 -\n3/10\n1) wyborze\nP E Ł N O M O C N I C T W O"
    assert _clean(text) == "polegających na:\n1) wyborze\nPEŁNOMOCNICTWO"


def test_ocr_lines_follow_skewed_reading_order():
    # Dwie linie tekstu przekrzywione o ok. 2°: prawa ramka górnej linii leży niżej niż lewa ramka dolnej.
    def quad(x: float, y: float, width: float, slope: float) -> list[list[float]]:
        return [[x, y], [x + width, y + width * slope], [x + width, y + width * slope + 20], [x, y + 20]]

    slope = 0.035
    quads = np.asarray(
        [quad(0, 100, 300, slope), quad(900, 100 + 900 * slope, 300, slope), quad(0, 125, 300, slope)],
        dtype=np.float64,
    )
    lines = _lines(_boxes(quads, ["Odwołujący:", "Firma sp. k.", "ul. Polna 1"]))
    assert lines == ["Odwołujący:   Firma sp. k.", "ul. Polna 1"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Rachunek beneficjenta PLO2 9855 2693 8978 6814 9636 5038",
            "Rachunek beneficjenta PL02 9855 2693 8978 6814 9636 5038",
        ),
        ("na rachunek 6O 1010 1010 0081 3622 3100 0000", "na rachunek 60 1010 1010 0081 3622 3100 0000"),
        ("PL62 2170 0008 9152 5017 5661 7062", "PL62 2170 0008 9152 5017 5661 7062"),
        ("KR-441o, Olsztyn 1010", "KR-441o, Olsztyn 1010"),  # poza rachunkiem nic się nie zmienia
        (
            "Rachunek zleceniodawcy   PL.64 6217 1965 0035 1583 6600 8774",
            "Rachunek zleceniodawcy   PL64 6217 1965 0035 1583 6600 8774",
        ),
        ("Dostawa oprogramowania wraz Z serwisem", "Dostawa oprogramowania wraz z serwisem"),
        ("Zorina Technologie sp. Z o.o.", "Zorina Technologie sp. z o.o."),
        ("Zorina Technologie sp. Z 0.0.", "Zorina Technologie sp. z o.o."),
        (
            "ZAMAWIAJĄCY: GMINA Z SIEDZIBĄ W LESIE",
            "ZAMAWIAJĄCY: GMINA Z SIEDZIBĄ W LESIE",
        ),  # wersaliki bez zmian
        ("Etap I. Z uwagi na termin", "Etap I. Z uwagi na termin"),  # po kropce i wielkiej literze
    ],
)
def test_ocr_lookalike_letters_are_fixed_only_inside_account_numbers(raw, expected):
    assert ingest._clean_ocr(raw) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("PEHNOMOCNICTWO", True),  # OCR: ł -> h
        ("Pełnomocnictwo procesowe", True),
        ("Kopia pełnomocnictwa", False),  # inna forma wyrazu, nie tytuł
        ("Niniejszym udzielam pehnomocnictwa wyżej wskazanemu pełnomocnikowi", False),  # długa linia
    ],
)
def test_title_rule_tolerates_one_ocr_error_in_a_short_title_line(title, expected):
    rule = ClassificationRule(
        pattern=r"^\s*pelnomocnictw[o0]\s*$", weight=4, zone="title", similar="pelnomocnictwo"
    )
    assert rule.matches(fold(title)) is expected
