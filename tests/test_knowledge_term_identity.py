from integration.knowledge_term_identity import build_grading_knowledge_term


def test_numbered_label_keeps_display_and_strips_mapping_prefix() -> None:
    term = build_grading_knowledge_term("G7_15", "G7_15 · 角平分线性质")

    assert term.knowledge_id == "G7_15"
    assert term.display_value == "G7_15 · 角平分线性质"
    assert term.source_value == "角平分线性质"


def test_plain_label_and_missing_label_have_stable_fallbacks() -> None:
    plain = build_grading_knowledge_term("K1", "二次函数")
    missing = build_grading_knowledge_term("K2", "")

    assert plain.source_value == "二次函数"
    assert plain.display_value == "二次函数"
    assert missing.source_value == "K2"
    assert missing.display_value == "K2"


def test_supported_separators_are_removed_only_after_exact_id_prefix() -> None:
    assert build_grading_knowledge_term("G7_01", "G7_01：尺规作图").source_value == "尺规作图"
    assert build_grading_knowledge_term("G7_01", "G7_01 - 尺规作图").source_value == "尺规作图"
    assert (
        build_grading_knowledge_term("G7_01", "其他 G7_01 尺规作图").source_value
        == "其他 G7_01 尺规作图"
    )
