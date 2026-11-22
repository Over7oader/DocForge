from doxforge.extraction.constraints import enforce_constraints

SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "leader": {"type": ["integer", "null"], "x-null-when-singleton": True},
            "name": {"type": "string"},
        },
    },
}


def test_singleton_flag_is_not_inferred_from_unrelated_roles():
    assert enforce_constraints(SCHEMA, [{"name": "Zespół A", "leader": 1}]) == [
        {"name": "Zespół A", "leader": None}
    ]


def test_multiple_parties_keep_explicit_leader_and_partner_flags():
    value = [{"name": "Zespół B", "leader": 0}, {"name": "Zespół A", "leader": 1}]
    assert enforce_constraints(SCHEMA, value) == value
