# -*- coding: utf-8 -*-
"""detect_question_type 大题守护规则测试。

背景：docx 解析后解答题的作答下划线常残留为全角空格（<u>　　</u>），
旧规则一见全角空格即判填空题，导致大量高分解答题被误标。
守护规则：题干开头（N 分）且 N>=6，或含成对（1）（2）小问时，跳过填空分支。
"""

from question_bank.parsers.type_detector import (
    QUESTION_TYPES,
    detect_essay_subtype,
    detect_question_type,
    question_type_from_rubric,
    split_legacy_question_type,
)


def test_real_fill_blank_with_fullwidth_space_stays_fill_blank() -> None:
    text = "（3分）某学校举行合唱比赛，则抽到前3个出场的概率为 <u>　　　　</u> ．"
    assert detect_question_type(text) == "填空题"


def test_real_fill_blank_with_underscore_stays_fill_blank() -> None:
    assert detect_question_type("若AD∥BC，则∠ABD＝______°。") == "填空题"


def test_high_score_with_fullwidth_space_is_not_fill_blank() -> None:
    text = (
        "（12分）光明乳鸽是广东省深圳市的传统名菜．某外卖平台计划销售光明乳鸽． "
        "（1）求日销售利润； （2）设降价x元，求利润函数．"
    )
    assert detect_question_type(text) != "填空题"


def test_high_score_blank_inside_subquestion_is_not_fill_blank() -> None:
    # 8 分解答题的小问里带真空空（<u>　　</u>），不应因填空信号误判。
    text = (
        "（8分）某景区向雪糕厂定制了一批文创盲盒雪糕． "
        "（1）小方买一个雪糕，能买到巧克力口味是一个 <u>　　　</u> 事件．"
    )
    assert detect_question_type(text) != "填空题"


def test_subquestion_pair_without_score_is_not_fill_blank() -> None:
    text = (
        "如图所示，在平面直角坐标系中，已知A（0，1），B（2，0）． "
        "（1）求出△ABC的面积为 <u>　　</u> ． （2）画出△ABC关于x轴对称的图形．"
    )
    assert detect_question_type(text) != "填空题"


def test_class_number_not_treated_as_subquestion() -> None:
    text = "（3分）某学校七年级举行班级合唱比赛，则七年级（2）班抽到前3个出场的概率为 <u>　　　　</u> ．"
    assert detect_question_type(text) == "填空题"


def test_fraction_notation_not_treated_as_subquestion() -> None:
    text = "（3分）如图，一次函数y=(1)/(2)x+2的图象与x轴交于点A，则表达式为 <u>　　　　</u> ．"
    assert detect_question_type(text) == "填空题"


def test_single_subquestion_mark_alone_stays_fill_blank() -> None:
    text = "（4分）化简：（1）a·a<sup>2</sup>＝ <u>　　</u> ．"
    assert detect_question_type(text) == "填空题"


def test_choice_question_detection_unchanged() -> None:
    text = "（3分）下列计算正确的是（ ） A．a+a=a2 B．a·a=2a C．a3·a=a3 D．(a2)3=a5"
    assert detect_question_type(text) == "选择题"


def test_granular_current_type_kept() -> None:
    assert detect_question_type("（8分）……　", current_type="选择题") == "选择题"


def test_legacy_subtype_current_type_normalizes_to_essay() -> None:
    # 兼容输入：归一前的旧子类题型按大类"解答题"保留。
    assert detect_question_type("（8分）……　", current_type="解答题（证明）") == "解答题"


def test_detector_only_emits_unified_types() -> None:
    # 尺规作图、说理、计算信号都不再改变题型枚举，只影响子类标签。
    assert detect_question_type("（8分）用尺规作图作出∠ABC的平分线。") == "解答题"
    assert detect_question_type("（8分）证明：△ABC≌△DEF。") == "解答题"
    assert detect_question_type("（5分）计算：|-1|+(-2)^3。") == "解答题"
    assert QUESTION_TYPES == ("选择题", "多选题", "填空题", "解答题")


def test_essay_subtype_drawing_wins_over_proof() -> None:
    # 带尺规作图的证明也算画图（作图优先级最高）。
    text = "如图，用尺规作图作出∠ABC的平分线，并证明你的结论。"
    assert detect_essay_subtype(text) == "画图"


def test_essay_subtype_proof_signals() -> None:
    assert detect_essay_subtype("（1）试说明△ABC≌△DEF成立的理由。") == "证明"
    assert detect_essay_subtype("求证：四边形ABCD是菱形。") == "证明"
    assert detect_essay_subtype("判定四边形ABCD是否为平行四边形。") == "证明"


def test_essay_subtype_calculation_requires_leading_instruction() -> None:
    assert detect_essay_subtype("（5分）计算：|-1|+(-2)^3。") == "计算"
    assert detect_essay_subtype("（1）化简：a·a^2。") == "计算"
    # 题面中段出现的"计算"叙述不算信号。
    assert detect_essay_subtype("阅读材料，根据计算结果回答下列问题。") is None


def test_essay_subtype_weak_geometry_words_do_not_misfire() -> None:
    # "垂直/平行"等旧易误伤词不再触发证明子类。
    assert detect_essay_subtype("如图，AB⊥CD于点O，求∠AOC的度数。") is None
    assert detect_essay_subtype("已知AD∥BC，求四边形ABCD的面积。") is None


def test_essay_subtype_no_signal_returns_none() -> None:
    assert detect_essay_subtype("阅读材料，回答下列问题：材料中的规律是什么？") is None
    assert detect_essay_subtype("") is None


def test_question_type_from_rubric_maps_to_unified_types() -> None:
    assert question_type_from_rubric("choice") == "选择题"
    assert question_type_from_rubric("fill_blank") == "填空题"
    assert question_type_from_rubric("calculation") == "解答题"
    assert question_type_from_rubric("proof") == "解答题"
    assert question_type_from_rubric("comprehensive") == "解答题"
    assert question_type_from_rubric("unknown") is None


def test_split_legacy_question_type() -> None:
    assert split_legacy_question_type("解答题（画图）") == ("解答题", "画图")
    assert split_legacy_question_type("解答题（计算）") == ("解答题", "计算")
    assert split_legacy_question_type("解答题（证明）") == ("解答题", "证明")
    assert split_legacy_question_type("选择题") == ("选择题", None)
    assert split_legacy_question_type(None) == (None, None)
