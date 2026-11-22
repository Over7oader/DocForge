from webapp.provenance import concrete_to_schema, locate


def test_locate_finds_literal_value_span():
    pages = ["Odwołujący\nNIP 1234567890\n", "druga strona"]
    fs = {"Odwolujacy[].nip": "text"}
    prov = locate({"Odwolujacy": [{"nip": "1234567890"}]}, fs, pages)
    p = prov["Odwolujacy[0].nip"]
    assert p.status == "source" and p.page == 0
    assert pages[0][p.span[0] : p.span[1]] == "1234567890"


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
    assert pages[0][p.span[0] : p.span[1]] == "Jan Kowalski"


def test_concrete_to_schema_maps_indices():
    assert concrete_to_schema("Odwolujacy[0].pelnomocnik.mail") == "Odwolujacy[].pelnomocnik.mail"
    assert concrete_to_schema("a[0].b[12].c") == "a[].b[].c"


def test_first_match_when_value_repeats():
    pages = ["kwota 100 zł", "znowu kwota 100 zł i 100 zł"]
    fs = {"kwota": "text"}
    prov = locate({"kwota": "100"}, fs, pages)
    p = prov["kwota"]
    assert p.page == 0 and pages[0][p.span[0] : p.span[1]] == "100"
