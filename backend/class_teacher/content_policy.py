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

_DIRECT_IDENTIFIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
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
    ("诊断结论", re.compile(r"(?:诊断为|确诊为?|心理诊断|判定为[^，。；]{0,12}(?:障碍|疾病))")),
    ("欺凌认定", re.compile(r"(?:认定|判定|确认|属于)[^，。；]{0,10}欺凌")),
    ("惩戒决定", re.compile(r"(?:决定|应当|必须|建议)?[^，。；]{0,8}(?:惩戒|处分|处罚|开除)")),
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
