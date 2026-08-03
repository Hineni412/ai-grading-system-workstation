from __future__ import annotations

import re
from dataclasses import dataclass


_COMMON_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华"
    "金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞"
    "任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于傅皮卞齐康"
    "伍余元卜顾孟平黄和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋"
    "茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林"
    "刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗"
    "丁宣邓郁单杭洪包诸左石崔吉龚程嵇邢裴陆荣翁荀羊甄家封芮储靳"
    "汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁"
    "仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶黎蓟薄印宿白怀蒲邰从"
    "鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉"
    "宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连"
    "茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙"
    "东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜"
    "养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)
_COMPOUND_SURNAMES = (
    "欧阳",
    "太史",
    "端木",
    "上官",
    "司马",
    "东方",
    "独孤",
    "南宫",
    "万俟",
    "闻人",
    "夏侯",
    "诸葛",
    "尉迟",
    "公羊",
    "赫连",
    "澹台",
    "皇甫",
    "宗政",
    "濮阳",
    "公冶",
    "申屠",
    "公孙",
    "慕容",
    "仲孙",
    "钟离",
    "长孙",
    "宇文",
    "司徒",
    "鲜于",
    "司空",
    "闾丘",
    "子车",
    "亓官",
    "司寇",
)
_COMPOUND_SURNAME_PATTERN = "(?:" + "|".join(_COMPOUND_SURNAMES) + ")"
_NAME_BODY = (
    rf"(?:{_COMPOUND_SURNAME_PATTERN}[\u4e00-\u9fff]{{1,2}}|"
    rf"[{_COMMON_SURNAMES}][\u4e00-\u9fff]{{1,2}})"
)

_NON_PERSON_SUBJECTS = (
    "任课老师",
    "班主任",
    "各年级",
    "年级组",
    "教研组",
    "备课组",
    "家委会",
    "学生会",
    "全校",
    "全班",
    "班级",
    "班会",
    "周会",
    "校会",
    "学校",
    "年级",
    "全体",
    "各班",
)
_NON_PERSON_SUBJECT_PATTERN = "(?:" + "|".join(
    re.escape(value) for value in sorted(_NON_PERSON_SUBJECTS, key=len, reverse=True)
) + ")"
_NON_PERSON_REFERENCE_PATTERN = re.compile(
    rf"{_NON_PERSON_SUBJECT_PATTERN}"
    r"(?:同学|学生|家长|师生)?"
    r"(?:(?:里|内|中)?的|里|内|中)?"
)
_NON_PERSON_MASK = "\ufff0"

_NAME_CONTEXT_SUFFIX = (
    r"(?:(?:\d{4}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*(?:日|号)|"
    r"(?:\d{4}[./-])?\d{1,2}[./-]\d{1,2}|今天|明天|后天|最近|本周|下周|"
    r"未|没|缺|需要|希望|说|谈话|沟通|联系|家访|家长|同学|学生|说明|确认|"
    r"提交|补交|回复|交材料|交作业|讨论|处理|请假|迟到|情绪|作业|课堂|"
    r"安排(?:任务|工作|值日)?|，|。|；|、|\s|$)"
)

_MOBILE_PHONE_TEXT = (
    r"(?<!\d)(?:(?:\+?86|0086)[\s-]*)?1[3-9]\d(?:[\s-]*\d){8}(?!\d)"
)
_LANDLINE_PHONE_TEXT = (
    r"(?<!\d)(?:\(\s*0\d{2,3}\s*\)|0\d{2,3})[\s-]*"
    r"\d(?:[\s-]*\d){6,7}(?:[\s-]*(?:转|ext\.?\s*)?\d{1,6})?(?!\d)"
)
_MOBILE_PHONE_PATTERN = re.compile(_MOBILE_PHONE_TEXT, re.IGNORECASE)
_LANDLINE_PHONE_PATTERN = re.compile(_LANDLINE_PHONE_TEXT, re.IGNORECASE)
_PARENT_PHONE_LABEL_TEXT = (
    r"(?:家长|父亲|母亲|爸爸|妈妈|监护人)(?:的)?"
    r"(?:联系电话|电话号码|电话|手机号|手机|联系方式)"
)
_PARENT_PHONE_LABEL_PATTERN = re.compile(_PARENT_PHONE_LABEL_TEXT)
_PARENT_PHONE_VALUE_PATTERN = re.compile(
    rf"{_PARENT_PHONE_LABEL_TEXT}\s*(?:是|为|：|:)?\s*"
    rf"(?:{_MOBILE_PHONE_TEXT}|{_LANDLINE_PHONE_TEXT})",
    re.IGNORECASE,
)

_DIRECT_IDENTIFIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("手机号", _MOBILE_PHONE_PATTERN),
    ("座机号", _LANDLINE_PHONE_PATTERN),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("邮箱", re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")),
)

_DIRECT_NAME_PATTERN = re.compile(
    rf"(?:{_COMPOUND_SURNAME_PATTERN}[\u4e00-\u9fff]{{0,2}}|"
    rf"[{_COMMON_SURNAMES}][\u4e00-\u9fff]{{0,2}})"
    r"(?:同学|学生|家长)"
)
_NON_NAME_CONTEXT_START = r"(?!家长|学生|同学|老师|今天|明天|后天|最近|本周|下周)"

_CONTEXTUAL_NAME_PATTERNS = (
    re.compile(
        rf"(?:跟|与|找|联系|通知|约谈|提醒|请|让|给)(?:\s|{_NON_PERSON_MASK})*"
        rf"{_NON_NAME_CONTEXT_START}(?P<name>{_NAME_BODY})"
        rf"(?={_NAME_CONTEXT_SUFFIX})"
    ),
    re.compile(
        rf"(?:^|(?<=[，。；、]))(?:\s|{_NON_PERSON_MASK})*"
        rf"{_NON_NAME_CONTEXT_START}(?P<name>{_NAME_BODY})"
        rf"(?={_NAME_CONTEXT_SUFFIX})"
    ),
)
_CLASS_NAME_PATTERNS = (
    re.compile(
        rf"(?P<class>(?:[一二三四五六七八九十百\d]+年级\s*)?"
        rf"[一二三四五六七八九十百\d]+\s*班(?:\s*的)?\s*(?:学生|同学)?\s*)"
        rf"(?P<name>{_NAME_BODY})(?={_NAME_CONTEXT_SUFFIX})"
    ),
)
_INCIDENT_NAME_PATTERNS = (
    re.compile(
        rf"(?P<name>{_NAME_BODY})(?:同学|学生)?"
        rf"(?=\s*(?:和|与|、)\s*{_NAME_BODY}(?:同学|学生)?"
        r"\s*(?:发生|打架|冲突|争执|斗殴|推搡|受伤))"
    ),
    re.compile(
        rf"(?:和|与|、)\s*(?P<name>{_NAME_BODY})(?:同学|学生)?"
        r"(?=\s*(?:发生|打架|冲突|争执|斗殴|推搡|受伤|[，。；]|$))"
    ),
    re.compile(
        rf"(?:^|(?<=[，。；]))\s*(?P<name>{_NAME_BODY})(?:同学|学生)?"
        r"(?=\s*(?:发生|打架|冲突|争执|斗殴|推搡|受伤))"
    ),
)


def _mask_non_person_references(value: str) -> str:
    """Keep positions stable while making organization phrases indivisible."""

    masked = list(value)
    for match in _NON_PERSON_REFERENCE_PATTERN.finditer(value):
        masked[match.start() : match.end()] = _NON_PERSON_MASK * (
            match.end() - match.start()
        )
    return "".join(masked)


def _name_findings(value: str) -> bool:
    masked = _mask_non_person_references(value)
    return bool(
        _DIRECT_NAME_PATTERN.search(masked)
        or any(pattern.search(masked) for pattern in _CONTEXTUAL_NAME_PATTERNS)
        or any(pattern.search(value) for pattern in _CLASS_NAME_PATTERNS)
    )


def _replace_spans(
    value: str,
    spans: list[tuple[int, int]],
    replacement: str,
) -> str:
    for start, end in reversed(sorted(set(spans))):
        value = value[:start] + replacement + value[end:]
    return value

_MODEL_DECISION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "诊断结论",
        re.compile(
            r"(?:诊断为|确诊为?|心理诊断|判定为[^，。；]{0,12}(?:障碍|疾病)|"
            r"(?:患有|罹患|得了|就是)[^，。；]{0,8}"
            r"(?:抑郁症|焦虑症|双相情感障碍|精神障碍|心理障碍|人格障碍|"
            r"注意缺陷多动障碍|自闭症|孤独症|心理疾病|精神疾病)|"
            r"(?:该生|该学生|这个学生|当事学生|学生[A-Z]{0,2})\s*(?:是|有)\s*"
            r"(?:轻度|中度|重度)?(?:抑郁症|焦虑症|双相情感障碍|精神障碍|"
            r"心理障碍|人格障碍|注意缺陷多动障碍|自闭症|孤独症|心理疾病|精神疾病))"
        ),
    ),
    (
        "欺凌认定",
        re.compile(
            r"(?:认定|判定|确认|属于|构成)[^，。；]{0,10}欺凌|"
            r"(?:这是|此事是|该行为是)\s*(?:一起|一宗|一种|典型的|明确的|严重的)?\s*"
            r"(?:校园|网络)?欺凌(?:行为|事件)"
        ),
    ),
    (
        "惩戒决定",
        re.compile(
            r"(?:决定|应当|应该|必须|建议|要求|责令|予以|给予)"
            r"[^，。；]{0,10}(?:惩戒|处分|处罚|警告|记过|留校察看|停课|停学|"
            r"禁止返校|转学|劝退|退学|开除)|"
            r"(?:停课|停学)\s*[一二三四五六七八九十两\d]+\s*(?:天|周|月|个月)"
        ),
    ),
    ("自动外发", re.compile(r"自动(?:发送|通知|外发|联系)")),
    ("自动完成", re.compile(r"自动(?:标记)?完成")),
    ("自动结案", re.compile(r"自动结案|已结案")),
)

_RESTRICTED_CONTEXT_TERMS = (
    "家庭住址",
    "家长电话",
    "成绩排名",
    "心理诊断",
    "病历",
    "用药",
    "欺凌证据",
    "伤情照片",
    "私人聊天",
)

_MODEL_HARD_BLOCK_TERMS = (
    "身份证",
    "家庭住址",
    "家长电话",
    "银行卡",
    "护照",
    "病历原文",
    "心理量表",
    "伤情照片",
    "欺凌证据",
    "性侵",
    "私人聊天",
    "恢复密钥",
    "API Key",
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    outbound_text: str
    removed_categories: tuple[str, ...]
    blocked_categories: tuple[str, ...]
    identity_aliases: tuple[tuple[str, str], ...] = ()


class SensitiveContentPolicy:
    """One local policy seam for ordinary persistence and model previews."""

    @staticmethod
    def ordinary_findings(value: str) -> tuple[str, ...]:
        findings = [
            label
            for label, pattern in _DIRECT_IDENTIFIER_PATTERNS
            if pattern.search(value)
        ]
        findings.extend(term for term in _RESTRICTED_CONTEXT_TERMS if term in value)
        if _PARENT_PHONE_LABEL_PATTERN.search(value):
            findings.append("家长电话")
        if _name_findings(value):
            findings.append("可能的具体姓名")
        return tuple(dict.fromkeys(findings))

    @staticmethod
    def model_output_findings(value: str) -> tuple[str, ...]:
        findings = list(SensitiveContentPolicy.ordinary_findings(value))
        findings.extend(
            label
            for label, pattern in _MODEL_DECISION_PATTERNS
            if pattern.search(value)
        )
        return tuple(dict.fromkeys(findings))

    @staticmethod
    def prepare_model_text(
        value: str,
        *,
        identity_terms: tuple[str, ...] = (),
    ) -> RedactionResult:
        blocked = [term for term in _MODEL_HARD_BLOCK_TERMS if term in value]
        for label, pattern in _DIRECT_IDENTIFIER_PATTERNS:
            if pattern.search(value):
                blocked.append(label)

        outbound = value.strip()
        removed: list[str] = []
        for term in sorted(
            SensitiveContentPolicy._identity_aliases(identity_terms),
            key=len,
            reverse=True,
        ):
            if term in outbound:
                outbound = outbound.replace(term, "学生A")
                removed.append("姓名或称呼")
        masked_outbound = _mask_non_person_references(outbound)
        direct_name_spans = [
            match.span() for match in _DIRECT_NAME_PATTERN.finditer(masked_outbound)
        ]
        if direct_name_spans:
            outbound = _replace_spans(outbound, direct_name_spans, "学生A")
            removed.append("姓名或称呼")

        for pattern in _CONTEXTUAL_NAME_PATTERNS:
            masked_outbound = _mask_non_person_references(outbound)
            contextual_name_spans = [
                match.span("name") for match in pattern.finditer(masked_outbound)
            ]
            if contextual_name_spans:
                outbound = _replace_spans(outbound, contextual_name_spans, "学生B")
                removed.append("姓名或称呼")

        # Exact scores/ranks are not required for a task-planning draft.
        score_pattern = re.compile(r"(?<!\d)\d{1,3}(?:\.\d+)?\s*(?:分|名)(?!\d)")
        if score_pattern.search(outbound):
            outbound = score_pattern.sub("[已移除具体数值]", outbound)
            removed.append("具体分数或名次")

        return RedactionResult(
            outbound_text=outbound,
            removed_categories=tuple(dict.fromkeys(removed)),
            blocked_categories=tuple(dict.fromkeys(blocked)),
        )

    @staticmethod
    def prepare_stable_model_text(
        value: str,
        *,
        existing_aliases: tuple[tuple[str, str], ...] = (),
    ) -> RedactionResult:
        """Create a deterministic multi-student preview without exposing names.

        Existing mappings are supplied only from encrypted workflow context. New
        names are assigned in first-appearance order, so later logical rounds
        keep the same student aliases without a student-record lookup. Direct
        contact identifiers and address details are removed from the exact
        outbound preview instead of forcing the teacher to retype the incident.
        """

        blocked = [
            term
            for term in _MODEL_HARD_BLOCK_TERMS
            if term in value and term not in {"家庭住址", "家长电话"}
        ]
        aliases: list[tuple[str, str]] = []
        seen_terms: set[str] = set()
        outbound = value.strip()
        removed: list[str] = []
        parent_phone_values = list(_PARENT_PHONE_VALUE_PATTERN.finditer(outbound))
        if parent_phone_values:
            for match in parent_phone_values:
                matched_value = match.group(0)
                removed.append(
                    "座机号"
                    if _LANDLINE_PHONE_PATTERN.search(matched_value)
                    else "手机号"
                )
            outbound = _PARENT_PHONE_VALUE_PATTERN.sub("[已移除联系电话]", outbound)
            removed.append("家长电话")
        for label, pattern in _DIRECT_IDENTIFIER_PATTERNS:
            if pattern.search(outbound):
                outbound = pattern.sub(f"[已移除{label}]", outbound)
                removed.append(label)
        if _PARENT_PHONE_LABEL_PATTERN.search(outbound):
            blocked.append("家长电话")
        address_pattern = re.compile(
            r"(?:(?:家庭住址|住址|地址)\s*(?:是|为|：|:)?\s*|"
            r"(?:家住|住在)\s*)[^，。；\n]{2,80}"
        )
        if address_pattern.search(outbound):
            outbound = address_pattern.sub("[已移除住址]", outbound)
            removed.append("家庭住址")
        known_spans: list[tuple[int, int, str]] = []
        for raw_term, raw_alias in existing_aliases:
            term = re.sub(r"(?:同学|学生)$", "", str(raw_term).strip())
            alias = str(raw_alias).strip()
            if (
                term
                and term not in seen_terms
                and re.fullmatch(r"学生[A-Z]{1,2}", alias)
            ):
                aliases.append((term, alias))
                seen_terms.add(term)
                known_spans.extend(
                    (match.start(), match.end(), term)
                    for match in re.finditer(re.escape(term), outbound)
                )

        masked = _mask_non_person_references(outbound)
        spans: list[tuple[int, int, str]] = list(known_spans)
        class_matches = [
            match
            for pattern in _CLASS_NAME_PATTERNS
            for match in pattern.finditer(outbound)
        ]
        class_spans = [match.span("class") for match in class_matches]
        for match in _DIRECT_NAME_PATTERN.finditer(masked):
            start, end = match.span()
            if any(
                start < class_end and class_start < end
                for class_start, class_end in class_spans
            ):
                continue
            term = re.sub(r"(?:同学|学生|家长)$", "", match.group(0)).strip()
            spans.append((start, end, term))
        for pattern in (*_CONTEXTUAL_NAME_PATTERNS, *_INCIDENT_NAME_PATTERNS):
            for match in pattern.finditer(masked):
                start, end = match.span("name")
                term = re.sub(r"(?:同学|学生)$", "", match.group("name")).strip()
                spans.append((start, end, term))
        for match in class_matches:
            start, end = match.span("name")
            spans.append((start, end, match.group("name").strip()))

        occupied: list[tuple[int, int]] = []
        replacements: list[tuple[int, int, str]] = []
        for start, end, term in sorted(set(spans), key=lambda item: (item[0], -item[1])):
            if any(start < prior_end and prior_start < end for prior_start, prior_end in occupied):
                continue
            alias = next((label for name, label in aliases if name == term), None)
            if alias is None:
                alias = SensitiveContentPolicy._student_alias(len(aliases))
                aliases.append((term, alias))
                seen_terms.add(term)
            replacements.append((start, end, alias))
            occupied.append((start, end))
        combined_replacements = [
            *replacements,
            *((start, end, "[已移除班级]") for start, end in class_spans),
        ]
        for start, end, replacement in sorted(
            set(combined_replacements),
            key=lambda item: item[0],
            reverse=True,
        ):
            outbound = outbound[:start] + replacement + outbound[end:]
        if replacements:
            removed.append("姓名或称呼")
        if class_spans:
            removed.append("班级")

        score_pattern = re.compile(r"(?<!\d)\d{1,3}(?:\.\d+)?\s*(?:分|名)(?!\d)")
        if score_pattern.search(outbound):
            outbound = score_pattern.sub("[已移除具体数值]", outbound)
            removed.append("具体分数或名次")

        return RedactionResult(
            outbound_text=outbound,
            removed_categories=tuple(dict.fromkeys(removed)),
            blocked_categories=tuple(dict.fromkeys(blocked)),
            identity_aliases=tuple(aliases),
        )

    @staticmethod
    def _student_alias(index: int) -> str:
        if index < 26:
            return f"学生{chr(ord('A') + index)}"
        first, second = divmod(index, 26)
        return f"学生{chr(ord('A') + first - 1)}{chr(ord('A') + second)}"

    @staticmethod
    def _identity_aliases(identity_terms: tuple[str, ...]) -> set[str]:
        aliases: set[str] = set()
        for value in identity_terms:
            term = str(value).strip()
            if not term:
                continue
            aliases.add(term)
            bare = re.sub(r"(?:同学|学生)$", "", term).strip()
            if bare:
                aliases.add(bare)
            if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", bare):
                surname_length = 2 if bare.startswith(_COMPOUND_SURNAMES) else 1
                if bare[0] in _COMMON_SURNAMES or surname_length == 2:
                    given_name = bare[surname_length:]
                    if len(given_name) >= 2:
                        aliases.add(given_name)
        return aliases


__all__ = ["RedactionResult", "SensitiveContentPolicy"]
