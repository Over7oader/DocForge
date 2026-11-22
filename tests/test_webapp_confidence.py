from webapp.confidence import agreement

LEAF = {"properties": {"a": {}}}
ARR = {"properties": {"O": {"items": {"properties": {"nip": {}, "nazwa": {}}}}}}


def test_full_when_members_and_result_agree():
    assert agreement(LEAF, {"a": "X"}, [{"a": "X"}, {"a": "X"}, {"a": "X"}])["a"] == "full"


def test_partial_and_split():
    assert agreement(LEAF, {"a": "X"}, [{"a": "X"}, {"a": "X"}, {"a": "Y"}])["a"] == "partial"
    assert agreement(LEAF, {"a": "X"}, [{"a": "X"}, {"a": "Y"}])["a"] == "split"


def test_single_member_is_no_signal():
    assert agreement(LEAF, {"a": "X"}, [{"a": "X"}])["a"] == "none"


def test_folded_values_count_as_agreement():
    assert (
        agreement(LEAF, {"a": "Jan Kowalski"}, [{"a": "Jan Kowalski"}, {"a": "jan  kowalski!"}])["a"]
        == "full"
    )


def test_rules_value_when_members_agree_null():
    # members unanimously null but the rule engine filled the value -> honest "rules", not green
    assert agreement(LEAF, {"a": "X"}, [{"a": None}, {"a": None}])["a"] == "rules"


def test_rules_value_when_final_differs_from_vote():
    assert agreement(LEAF, {"a": "RULE"}, [{"a": "X"}, {"a": "X"}])["a"] == "rules"


def test_null_result_is_no_signal_not_green():
    assert agreement(LEAF, {"a": None}, [{"a": None}, {"a": None}])["a"] == "none"


def test_array_count_disagreement_marks_leaves_split():
    result = {"O": [{"nip": "1", "nazwa": "Alfa"}]}
    ans = [
        {"O": [{"nip": "1", "nazwa": "Alfa"}]},
        {"O": [{"nip": "1", "nazwa": "Alfa"}, {"nip": "2", "nazwa": "Beta"}]},
    ]
    conf = agreement(ARR, result, ans)
    assert conf["O[0].nip"] == "split" and conf["O[0].nazwa"] == "split"


def test_array_matched_by_key_despite_reorder():
    result = {"O": [{"nip": "1", "nazwa": "Alfa"}, {"nip": "2", "nazwa": "Beta"}]}
    ans = [
        {"O": [{"nip": "1", "nazwa": "Alfa"}, {"nip": "2", "nazwa": "Beta"}]},
        {"O": [{"nip": "2", "nazwa": "BETA"}, {"nip": "1", "nazwa": "Gamma"}]},
    ]
    conf = agreement(ARR, result, ans)
    assert conf["O[0].nip"] == "full"  # nip 1 in both members
    assert conf["O[0].nazwa"] == "split"  # Alfa vs Gamma for the nip=1 entity


def test_reordered_rules_value_is_not_false_green():
    # Regression (Critical): members unanimous nip1->A, nip2->B; final reordered so O[0]=nip2 with v=A
    # filled by rules. Positional comparison would show green; key-matched must show "rules".
    schema = {"properties": {"O": {"items": {"properties": {"nip": {}, "v": {}}}}}}
    ans = [
        {"O": [{"nip": "1", "v": "A"}, {"nip": "2", "v": "B"}]},
        {"O": [{"nip": "1", "v": "A"}, {"nip": "2", "v": "B"}]},
    ]
    result = {"O": [{"nip": "2", "v": "A"}, {"nip": "1", "v": "A"}]}
    conf = agreement(schema, result, ans)
    assert conf["O[0].v"] == "rules"  # members say nip2 -> B
    assert conf["O[0].nip"] == "full"
