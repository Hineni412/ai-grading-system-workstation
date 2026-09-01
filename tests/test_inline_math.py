from __future__ import annotations

from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from question_bank.services.inline_math import omml_parts


def _omath(inner: str) -> object:
    return parse_xml(f'<m:oMath {nsdecls("m")}>{inner}</m:oMath>')


def test_fraction_serializes_to_latex_and_linear_text() -> None:
    parts = omml_parts(
        _omath(
            "<m:f>"
            "<m:num><m:r><m:t>1</m:t></m:r></m:num>"
            "<m:den><m:r><m:t>3</m:t></m:r></m:den>"
            "</m:f>"
        )
    )

    assert parts == ("\\frac{1}{3}", "(1)/(3)")


def test_radical_degree_rules() -> None:
    cubed = omml_parts(
        _omath(
            "<m:rad>"
            "<m:deg><m:r><m:t>3</m:t></m:r></m:deg>"
            "<m:e><m:r><m:t>-8</m:t></m:r></m:e>"
            "</m:rad>"
        )
    )
    square = omml_parts(
        _omath(
            "<m:rad>"
            "<m:deg><m:r><m:t>2</m:t></m:r></m:deg>"
            "<m:e><m:r><m:t>5</m:t></m:r></m:e>"
            "</m:rad>"
        )
    )

    assert cubed == ("\\sqrt[3]{-8}", "3√(-8)")
    assert square == ("\\sqrt{5}", "√(5)")


def test_scripts_cover_sup_and_subsup() -> None:
    parts = omml_parts(
        _omath(
            "<m:sSup>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "<m:sup><m:r><m:t>2</m:t></m:r></m:sup>"
            "</m:sSup>"
            "<m:sSubSup>"
            "<m:e><m:r><m:t>a</m:t></m:r></m:e>"
            "<m:sub><m:r><m:t>1</m:t></m:r></m:sub>"
            "<m:sup><m:r><m:t>2</m:t></m:r></m:sup>"
            "</m:sSubSup>"
        )
    )

    assert parts is not None
    latex, plain = parts
    assert "x^{2}" in latex
    assert "a_{1}^{2}" in latex
    assert plain == "x2a12"


def test_equation_system_with_open_brace_and_empty_end() -> None:
    parts = omml_parts(
        _omath(
            '<m:d><m:dPr><m:begChr m:val="{"/><m:endChr m:val=""/></m:dPr>'
            "<m:e>"
            "<m:eqArr>"
            "<m:e><m:r><m:t>2x+3y=4</m:t></m:r><m:r><m:t>①</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>3x-3y=6</m:t></m:r><m:r><m:t>②</m:t></m:r></m:e>"
            "</m:eqArr>"
            "</m:e></m:d>"
        )
    )

    assert parts is not None
    latex, plain = parts
    assert latex == (
        "\\left\\{\\begin{array}{l}2x+3y=4① \\\\ 3x-3y=6②\\end{array}\\right."
    )
    # Linear form mirrors the importer: no closing brace, rows concatenated.
    assert plain == "{2x+3y=4①3x-3y=6②"


def test_delimiter_defaults_apply_only_when_attribute_absent() -> None:
    parts = omml_parts(
        _omath(
            "<m:d><m:e><m:r><m:t>-8</m:t></m:r></m:e></m:d>"
        )
    )

    assert parts == ("\\left(-8\\right)", "(-8)")


def test_nary_uses_operator_symbol_in_latex_but_not_in_plain_text() -> None:
    parts = omml_parts(
        _omath(
            "<m:nary>"
            '<m:naryPr><m:chr m:val="∑"/></m:naryPr>'
            "<m:sub><m:r><m:t>i=1</m:t></m:r></m:sub>"
            "<m:sup><m:r><m:t>n</m:t></m:r></m:sup>"
            "<m:e><m:r><m:t>i</m:t></m:r></m:e>"
            "</m:nary>"
        )
    )

    assert parts is not None
    latex, plain = parts
    assert latex == "\\sum_{i=1}^{n} i"
    assert plain == "i=1ni"


def test_unknown_structure_degrades_to_linear_text() -> None:
    parts = omml_parts(
        _omath(
            "<m:m>"
            "<m:mr><m:e><m:r><m:t>1</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>2</m:t></m:r></m:e></m:mr>"
            "</m:m>"
        )
    )

    assert parts is not None
    assert parts[1] == "12"


def test_empty_math_returns_none() -> None:
    assert omml_parts(_omath("")) is None
