from __future__ import annotations

from typing import Any

from .sop_workflow_service import SopWorkflowService


def _step(
    key: str,
    title: str,
    *,
    details: str,
    depends_on: list[str] | None = None,
    required: bool = True,
    waivable: bool = False,
    safety_required: bool = False,
    activation: dict[str, object] | None = None,
    decision_key: str | None = None,
    decision_prompt: str | None = None,
    decision_options: list[tuple[str, str]] | None = None,
    communication_templates: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "key": key,
        "title": title,
        "details": details,
        "required": required,
        "waivable": waivable,
        "safety_required": safety_required,
        "depends_on": depends_on or [],
        "activation": activation,
        "decision_key": decision_key,
        "decision_prompt": decision_prompt,
        "decision_options": [
            {"value": value, "label": label}
            for value, label in (decision_options or [])
        ],
        "communication_templates": communication_templates or [],
    }


def _communication(
    kind: str,
    audience: str,
    content: str,
    basis: str,
    unknowns: list[str] | None = None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "audience": audience,
        "content": f"{content} 此文案尚未发送。",
        "basis": basis,
        "unknowns": unknowns or [],
    }


_SCHOOL_GAPS = [
    "本校紧急联系人和替代联系人尚未配置",
    "本校报告时限、接收角色和审批链尚未配置",
    "本校正式表单、档案归属和结案角色尚未配置",
]


def _baselines() -> list[dict[str, Any]]:
    return [
        {
            "template_key": "baseline.student_conflict",
            "title": "普通学生矛盾 · 个人工作清单",
            "risk_level": "elevated",
            "emergency_prompt": (
                "若冲突仍在发生、出现伤害、围堵、威胁或其他迫近危险，"
                "先制止、保护和人工报告，不等待记录完成。"
            ),
            "steps": [
                _step(
                    "safety_check",
                    "确认现场已停止冲突且学生当前安全",
                    details="教师人工核实；存在迫近危险时立即转紧急处置。",
                    safety_required=True,
                ),
                _step(
                    "separate_statements",
                    "分别记录各方表述",
                    details="区分学生自述、他人转述和教师直接观察，不先要求当面对质。",
                    depends_on=["safety_check"],
                ),
                _step(
                    "fact_check",
                    "区分一致事实、直接观察和争议内容",
                    details="只记录可核对事实和仍待核实事项。",
                    depends_on=["separate_statements"],
                ),
                _step(
                    "route",
                    "由教师重新检查安全与疑似欺凌信号",
                    details="系统不作欺凌认定；教师只选择当前工作分流。",
                    depends_on=["fact_check"],
                    decision_key="conflict_route",
                    decision_prompt="当前应进入哪一条人工处理路径？",
                    decision_options=[
                        ("ordinary", "暂按普通矛盾继续"),
                        (
                            "suspected_bullying",
                            "疑似欺凌，需学校核查",
                        ),
                        ("emergency", "存在紧急安全风险"),
                    ],
                ),
                _step(
                    "ordinary_support",
                    "明确普通矛盾处理目标和支持措施",
                    details="选择教育、协商或支持措施，不自动决定惩戒。",
                    depends_on=["route"],
                    activation={
                        "decision_key": "conflict_route",
                        "allowed_values": ["ordinary"],
                    },
                ),
                _step(
                    "bullying_handoff",
                    "按“疑似欺凌”向学校指定角色交接",
                    details="只标记疑似、待学校核查；记录人工接收人和时间。",
                    depends_on=["route"],
                    safety_required=True,
                    activation={
                        "decision_key": "conflict_route",
                        "allowed_values": ["suspected_bullying"],
                    },
                ),
                _step(
                    "emergency_handoff",
                    "立即执行救护、保护和人工报告",
                    details="不等待 AI、完整表单、家长回复或内部调查。",
                    depends_on=["route"],
                    safety_required=True,
                    activation={
                        "decision_key": "conflict_route",
                        "allowed_values": ["emergency"],
                    },
                ),
                _step(
                    "follow_up",
                    "分别设置跟进并记录结果",
                    details="按确认的分流分别复查安全、支持措施和后续变化。",
                    depends_on=[
                        "ordinary_support",
                        "bullying_handoff",
                        "emergency_handoff",
                    ],
                ),
            ],
        },
        {
            "template_key": "baseline.suspected_bullying",
            "title": "疑似欺凌核查与学校交接 · 个人工作清单",
            "risk_level": "emergency",
            "emergency_prompt": (
                "仍在发生的行为先制止并保护学生；如有伤害或迫近危险，"
                "并行进入救护和人工报告，不等待学校最终认定。"
            ),
            "steps": [
                _step(
                    "protect",
                    "立即制止并安排临时保护",
                    details="避免继续接触和报复风险；不强迫当面对质。",
                    safety_required=True,
                ),
                _step(
                    "school_report",
                    "向学校指定负责人报告",
                    details="记录人工接收角色和时间；联系人未配置时不能声称已交接。",
                    safety_required=True,
                ),
                _step(
                    "separate_records",
                    "分开记录各方与见证人转述",
                    details="不使用“欺凌者”“受害者”等已定性标签。",
                    depends_on=["protect"],
                ),
                _step(
                    "necessary_facts",
                    "核实行为方式、持续性、力量差异和影响",
                    details="整理给学校核查，不由系统或班主任作最终认定。",
                    depends_on=["separate_records"],
                ),
                _step(
                    "school_handoff",
                    "提交学校治理组织核查并记录接收",
                    details="学校交接未完成时不能结案。",
                    depends_on=["school_report", "necessary_facts"],
                    safety_required=True,
                ),
                _step(
                    "family_contact",
                    "按学校流程分别沟通并保护另一学生身份",
                    details="不在群内公开事件，不向一方家长泄露另一学生身份。",
                    depends_on=["school_handoff"],
                    communication_templates=[
                        _communication(
                            "phone_outline",
                            "当前目标学生家长",
                            (
                                "您好，学校正在核实一项与孩子有关的情况。"
                                "本次只核实与您孩子直接相关的事实和支持需要，"
                                "不提供其他学生身份或未经确认的判断。"
                            ),
                            "学校交接状态和当前学生的必要事实",
                            ["学校最终核查结论尚未形成"],
                        )
                    ],
                ),
                _step(
                    "follow_up",
                    "分别跟进安全、身心状态和后续支持",
                    details="由教师记录实际结果，再申请结案。",
                    depends_on=["family_contact"],
                ),
            ],
        },
        {
            "template_key": "baseline.student_injury",
            "title": "学生伤害与紧急安全 · 个人工作清单",
            "risk_level": "emergency",
            "emergency_prompt": (
                "立即救护、制止危险并按现场情况人工联系紧急服务；"
                "同时启动学校应急联系人链。记录和 AI 辅助全部后置。"
            ),
            "steps": [
                _step(
                    "first_aid",
                    "立即救护并制止持续危险",
                    details="必要时由现场人员人工联系 120、110 等紧急服务。",
                    safety_required=True,
                ),
                _step(
                    "school_emergency_report",
                    "同时启动学校应急联系人链",
                    details="不等待 AI、完整表单、内部调查或家长回复。",
                    safety_required=True,
                ),
                _step(
                    "guardian_contact",
                    "按保护规则人工通知监护人",
                    details="疑似家庭侵害时不机械通知可能的侵害人，按学校保护链处理。",
                    safety_required=True,
                ),
                _step(
                    "factual_record",
                    "在不影响救护的前提下记录现场事实",
                    details="记录时间、地点、直接观察和已采取措施，不判断伤情轻重。",
                    depends_on=["first_aid"],
                ),
                _step(
                    "safe_handoff",
                    "确认学生已安全交接",
                    details="交接给学校指定人员、医疗机构、监护人或有关部门。",
                    depends_on=[
                        "first_aid",
                        "school_emergency_report",
                        "guardian_contact",
                    ],
                    safety_required=True,
                ),
                _step(
                    "return_follow_up",
                    "设置健康、安全和返校人工跟进",
                    details="由有权人员确认可以结束集中跟进后再结案。",
                    depends_on=["safe_handoff", "factual_record"],
                ),
            ],
        },
        {
            "template_key": "baseline.family_communication",
            "title": "家校沟通 · 个人工作清单",
            "risk_level": "ordinary",
            "emergency_prompt": None,
            "steps": [
                _step(
                    "purpose",
                    "明确沟通目的和关联事项",
                    details="沟通只能来自学校要求、教师创建或已确认事务跟进。",
                ),
                _step(
                    "fact_selection",
                    "只选择必要的已确认事实",
                    details="排除其他学生姓名、家庭信息和不必要敏感内容。",
                    depends_on=["purpose"],
                ),
                _step(
                    "outline",
                    "形成电话或面谈提纲",
                    details="教师检查后在系统外沟通；系统不自动拨打或发送。",
                    depends_on=["fact_selection"],
                    communication_templates=[
                        _communication(
                            "phone_outline",
                            "当前学生家长",
                            (
                                "先说明本次沟通目的；陈述时间明确、可核实的观察；"
                                "询问家长看到的情况；不推断家庭原因，不给学生贴标签；"
                                "最后确认共同目标和再次反馈时间。"
                            ),
                            "教师选择的已确认事实",
                            ["家长看到的情况仍待核实"],
                        )
                    ],
                ),
                _step(
                    "contact_result",
                    "记录实际沟通方式、时间和家长转述",
                    details="家长反馈保存为转述，不自动升级为事实。",
                    depends_on=["outline"],
                ),
                _step(
                    "agreement",
                    "记录双方约定、负责人和复查日期",
                    details="不能停留在“已经谈过”。",
                    depends_on=["contact_result"],
                ),
                _step(
                    "review",
                    "到期核对约定结果",
                    details="教师决定结案或继续跟进。",
                    depends_on=["agreement"],
                ),
            ],
        },
        {
            "template_key": "baseline.care_conversation",
            "title": "日常关怀谈话与跟进 · 个人工作清单",
            "risk_level": "elevated",
            "emergency_prompt": (
                "若谈话中出现自伤、伤害他人、不法侵害、欺凌或迫近危险信号，"
                "立即转学校保护和人工报告，不承诺保密。"
            ),
            "steps": [
                _step(
                    "observation",
                    "记录具体观察而非定性结论",
                    details="不写“心理有问题”“态度差”等标签。",
                ),
                _step(
                    "setting",
                    "选择保护隐私的谈话时间和地点",
                    details="使用开放、非指责的问题。",
                    depends_on=["observation"],
                    communication_templates=[
                        _communication(
                            "neutral_questions",
                            "当前学生",
                            (
                                "最近我注意到一个具体变化，想听听你的感受。"
                                "这段时间发生了什么？你希望得到什么帮助？"
                                "有没有需要我立即协助确保安全的事情？"
                            ),
                            "教师记录的具体观察",
                            ["学生的解释和支持需要仍待了解"],
                        )
                    ],
                ),
                _step(
                    "student_statement",
                    "把学生表达保存为学生转述",
                    details="不把转述自动写成已确认事实。",
                    depends_on=["setting"],
                ),
                _step(
                    "safety_recheck",
                    "由教师再次检查安全和保护信号",
                    details="系统不诊断；出现风险时人工升级。",
                    depends_on=["student_statement"],
                    safety_required=True,
                ),
                _step(
                    "support",
                    "记录实际提供的支持和短期目标",
                    details="可选择一般关怀、家校沟通或学校专业人员转介。",
                    depends_on=["safety_recheck"],
                ),
                _step(
                    "review",
                    "设置复查并对照最初事实记录变化",
                    details="教师决定结束、继续观察或人工升级。",
                    depends_on=["support"],
                ),
            ],
        },
        {
            "template_key": "baseline.school_activity",
            "title": "学校活动 · 个人工作清单",
            "risk_level": "ordinary",
            "emergency_prompt": (
                "活动中出现伤害或迫近危险时，立即执行救护和学校应急报告，"
                "不等待活动记录完成。"
            ),
            "steps": [
                _step(
                    "scope",
                    "确认活动目标、范围和学校要求",
                    details="未配置学校规则时仅作为个人工作清单。",
                ),
                _step(
                    "assignment",
                    "明确人员分工和人工负责人",
                    details="每项关键职责应有人确认。",
                    depends_on=["scope"],
                ),
                _step(
                    "materials",
                    "准备材料和设备清单",
                    details="核对数量、领取、保管和归还。",
                    depends_on=["scope"],
                ),
                _step(
                    "safety_plan",
                    "核对安全组织和应急安排",
                    details="大型集体活动需由学校补齐正式安全方案和联系人。",
                    depends_on=["scope"],
                    safety_required=True,
                ),
                _step(
                    "final_check",
                    "活动前完成分工、材料与安全检查",
                    details="三项必须共同完成后进入执行。",
                    depends_on=["assignment", "materials", "safety_plan"],
                ),
                _step(
                    "activity_result",
                    "记录活动执行结果和异常",
                    details="只记录实际发生和已处理事项。",
                    depends_on=["final_check"],
                ),
                _step(
                    "retrospective",
                    "完成复盘和后续行动",
                    details="记录可复用经验、未完成事项和负责人。",
                    depends_on=["activity_result"],
                ),
            ],
        },
    ]


class SopBaselineService:
    def __init__(self, sop: SopWorkflowService) -> None:
        self.sop = sop

    def ensure_baselines(
        self,
        *,
        token: str,
    ) -> dict[str, object]:
        existing = self.sop.list_templates(token=token)["items"]
        by_key = {
            (str(item["template_key"]), int(item["version"])): item
            for item in existing
        }
        items = []
        for definition in _baselines():
            key = str(definition["template_key"])
            current = by_key.get((key, 1))
            if current is None:
                current = self.sop.publish_template(
                    token=token,
                    operation_id=f"baseline-{key}-v1",
                    template_key=key,
                    version=1,
                    title=str(definition["title"]),
                    steps=list(definition["steps"]),
                    workflow_scope="personal_checklist",
                    risk_level=str(definition["risk_level"]),
                    emergency_prompt=definition["emergency_prompt"],
                    school_config_gaps=_SCHOOL_GAPS,
                )
            items.append(current)
        return {
            "items": items,
            "workflow_scope": "personal_checklist",
            "model_enabled": False,
            "physical_request_count": 0,
        }


__all__ = ["SopBaselineService"]
