import pytest

from doxforge.extraction.values import VALUE_TYPES
from doxforge.text import collapse_spaced_letters, fold


@pytest.mark.parametrize(
    ("type_name", "raw", "expected"),
    [
        ("nip", "999-295-65-50", "9992956550"),
        ("nip", "PL 5540240549", "5540240549"),
        ("nip", "12345", None),
        ("regon", "000279077", "000279077"),
        ("regon", "12345678901234", "12345678901234"),
        ("iban", "PL65 1632 9461 5868 4543 6827 7984", "PL65163294615868454368277984"),
        ("amount", "10 000,00 PLN", 10000.0),
        ("amount", "17.00", 17.0),
        ("amount", "10.000,00 zł", 10000.0),
        ("amount", "7 500 zł", 7500.0),
        ("date", "6 września 2026 r.", "2026-09-06"),
        ("date", "6 wrzesnia 2026", "2026-09-06"),  # OCR bez ogonków
        ("date", "27.03.2026", "2026-03-27"),
        ("date", "01-08-2026", "2026-08-01"),
        ("date", "2026-10-26", "2026-10-26"),
        ("date", "31.02.2026", None),
        ("bzp", "2026/BZP 00767819/01", "2026/BZP00767819/01"),
        ("ted", "2026/S 224-429185", "429185-2026"),
        ("ted", "570628-2026", "570628-2026"),
        ("token", "ZP.621.2026,", "ZP.621.2026"),
        ("text", "  „Tytuł  przelewu”. ", "Tytuł przelewu"),
        ("name", "r. pr. Jan Nowak", "Jan Nowak"),
        ("name", "radca prawny Aleksandra Rybak", "Aleksandra Rybak"),
        ("name", "Patrycja Bartoszewicz, adw.", "Patrycja Bartoszewicz"),
    ],
)
def test_normalize(type_name, raw, expected):
    assert VALUE_TYPES[type_name].normalize(raw) == expected


def test_search_patterns_work_on_folded_text():
    text = fold("Data wykonania: 6 Września 2026, kwota 1 234,56 PLN")
    date = VALUE_TYPES["date"].pattern
    amount = VALUE_TYPES["amount"].pattern
    assert date is not None and amount is not None
    assert date.search(text)
    assert amount.search(text)


def test_fold_keeps_positions():
    text = "Łódź, Odwołujący ŻÓŁĆ"
    folded = fold(text)
    assert folded == "lodz, odwolujacy zolc"
    assert len(folded) == len(text)


def test_collapse_spaced_letters():
    assert collapse_spaced_letters("P E Ł N O M O C N I C T W O") == "PEŁNOMOCNICTWO"
    assert collapse_spaced_letters("Zarzut nr 1 a b") == "Zarzut nr 1 a b"


def test_iban_country_prefix_and_line_wrapped_account():
    spec = VALUE_TYPES["iban"]
    text = fold("Rachunek: PL10 3863 9361 8709 7408 4853\n4519, prowadzony dla odbiorcy.")
    match = spec.pattern.search(text)
    assert match is not None
    assert spec.normalize(match[0]) == "PL10386393618709740848534519"
    assert spec.normalize("PL10 3863 9361 8709 7408 4853 ABCD") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Pracownia Zatokowa sp. k. z siedzibą w Gdyni", "Pracownia Zatokowa sp. k."),
        (
            "Fundusz Spółka z ograniczoną odpowiedzialnością z siedzibą we Wrocławiu",
            "Fundusz Spółka z ograniczoną odpowiedzialnością",
        ),
        ("Firma sp. z o.o.", "Firma sp. z o.o."),
        ("Muzeum z siedzibą w parku", "Muzeum z siedzibą w parku"),
        (
            "Lubelskie Przedsiębiorstwo Wodociągów sp. z o.o. al. Kraśnicka 76, 20-718 Lublin",
            "Lubelskie Przedsiębiorstwo Wodociągów sp. z o.o.",
        ),
        (
            "Szpital im. dr. Jana Nowaka w Lesie ul. Słoneczna 10, 26-266 Las",
            "Szpital im. dr. Jana Nowaka w Lesie",
        ),
        ("Centaur Usługi sp. z o.o., ul. Zaczarowana 8/2", "Centaur Usługi sp. z o.o."),
        ("Biuro Rachunkowe 01-234 Warszawa", "Biuro Rachunkowe"),
        ("Zakład Usług ul. Polna", "Zakład Usług ul. Polna"),  # bez numeru domu: to nie jest adres
    ],
)
def test_company_address_is_not_part_of_legal_name(raw, expected):
    assert VALUE_TYPES["organization"].normalize(raw) == expected
