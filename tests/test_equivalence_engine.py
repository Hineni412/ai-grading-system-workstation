from __future__ import annotations

from equivalence_engine import expand_equivalent_forms, merge_equivalent_forms


def test_degree_answer_adds_chinese_degree_variant() -> None:
    assert "72度" in expand_equivalent_forms("72°")


def test_answer_with_common_count_unit_adds_unitless_variant() -> None:
    assert "4" in expand_equivalent_forms("4条")


def test_simple_addition_equation_adds_commuted_variant() -> None:
    assert "y=20+48x" in expand_equivalent_forms("y=48x+20")


def test_merge_expands_each_explicitly_accepted_answer() -> None:
    forms = merge_equivalent_forms(["54°"], "72°")

    assert {"72°", "72度", "54°", "54度"}.issubset(set(forms))


def test_integer_answers_do_not_lose_trailing_zeroes() -> None:
    assert "8" not in expand_equivalent_forms("80")
    assert "3" not in expand_equivalent_forms("300")
