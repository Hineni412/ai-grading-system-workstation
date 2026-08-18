from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from backend.llm.json_repair import parse_json_object_locally
from backend.workspaces.ai_tasks.model_gateway import WorkspaceAITaskModelGateway
from backend.workspaces.ai_tasks.models import (
    AdapterResult,
    AdoptionResult,
    HandoffDraft,
    HandoffSnapshot,
    InvalidAdapterResultError,
    OpaqueRef,
    RevisionConflictError,
    StoredTask,
)

from ..errors import VaultError
from ..intake_draft import compose_sensitive_draft
from ..model_approval import ModelResponseTruncatedError
from ..roster_ref import SUBJECT_REF_PATTERN, task_safe_ref_id
from ..student_card_service import CORE_PROFILE_DIMENSIONS
from .affair_flow_contract import parse_affair_flow_revision
from .conversations import ConversationStore
from .triage_contract import DOMAINS, HANDLING_MODES, INTENTS


_TRIAGE_INSTRUCTION = """你是班主任事务整理助手。只返回一个 JSON 对象，不要 Markdown。顶层只能有 contract_version、assistant_message、clarification_questions、work_items。严格按下面的完整结构返回：
{"contract_version":"class_teacher_triage.v1","assistant_message":"给教师的简短说明","clarification_questions":[],"work_items":[{"work_item_id":"item_001","domain":"student_support","primary_mode":"record","secondary_modes":[],"intent":"create","reason_summary":"为什么这样整理","subject_refs":[],"time_facts":[{"text":"暑假期间计划复查"}],"safety_level":"teacher_review_required","missing_fields":[],"draft":{"summary":"待教师核对的草稿摘要","record_kind":"reported_statement","source":"教师当前输入，采用前核对","basis":"待核对的书面或专业依据","observed_at":"带时区的 ISO 日期时间","profile_base_revision":0,"profile_update":{"summary":"合并新信息后的学生当前总体认识","dimensions":[{"key":"learning_ability","label":"学习与能力","items":["当前有效信息"]}],"open_questions":["仍值得继续了解的问题"],"support_focus":[{"key":"focus_attention","title":"支持重点","need":"当前需要","effective_methods":[],"next_actions":["下一步可尝试的支持"]}]}}}]}

domain 只能是 student_growth、student_support、conflict_safety、class_operations、activities_culture、school_coordination；primary_mode 和 secondary_modes 只能使用 record、plan_calendar、sop；intent 只能使用 create、append、follow_up、plan、review；safety_level 只能使用 normal、teacher_review_required、urgent_attention。time_facts 每一项都必须是对象，例如 {"text":"暑假期间计划复查"}，不能直接放字符串。subject_refs 只能原样复制当前候选中的完整 {"kind":"student","id":"...","revision":"..."} 对象；一件事务涉及多名不同学生时必须保留全部对应引用，只有同一个姓名存在多个候选时才留空并把“请选择学生”放入 missing_fields。

计划与日历规则：一条输入同时包含多项同一目标下的班务时，优先形成一个 plan_calendar 工作项，在 draft 中完整返回 {"plan_title":"计划标题","summary":"计划说明","reference_at":"参考时间","final_deadline":"带时区的 ISO 日期时间","candidates":[{"label":"候选事项","reason":"为什么通常需要准备","suggested":true}],"actions":[{"draft_action_id":"action-1","title":"可执行行动","details":"准备内容和完成标准","due_at":"带时区的 ISO 日期时间","depends_on_draft_action_ids":[]}]}。当教师只说出目标而事项明显不完整时（例如只说"9月1日开学"），candidates 必须主动列出该类事务的常见准备工作（4 至 8 项），suggested 标记你建议默认纳入的项；教师已明确提到的事项必须进入 candidates 且 suggested=true。总截止日 final_deadline 必须与教师明确说出的目标日期一致，不得用最后一个行动的日期替代；教师没有给出目标日期时 final_deadline 留空并追问。每个明确工作都要成为独立 action，准备、确认、执行或复查确有必要时继续细分。根据本机参考日期解释“今天、明天、前一天、9月1日”等表达，并在 time_facts 中保留推导依据。缺少具体日期、结算周期、人员范围、兑奖规则等会影响执行的信息时，在 clarification_questions 中追问；不得编造教师没有提供且无法从日期关系推出的时间。仍可先形成草稿，但不以总截止日期静默填补各行动空日期。

学生专业结论规则：教师报告“确诊、诊断、专业评估结论”等内容时，AI 不作诊断，只整理教师转述或已有专业材料。若专业结论来源、结论日期或书面依据未明确，record_kind 使用 reported_statement，并追问专业结论来源、日期和依据；信息齐全时才建议 professional_conclusion。draft 必须包含 record_kind、source、basis、observed_at、current_school_support、professional_recommendations、avoidances 和合并后的 profile_update；追问当前在校支持、专业建议、需要避免的做法或后续复查中最必要的内容。不得把医学或心理结论写成永久性格、能力或纪律标签。

学生冲突规则：冲突、安全、受伤或异常线索必须使用 conflict_safety 和 sop，第一轮同时形成可执行初稿与最多 3 个必要追问；追问必须放在顶层 clarification_questions，不能只放入 profile_update.open_questions。冲突 SOP 的 draft 至少返回 template_key、summary、steps、to_verify。对每名已唯一匹配的学生分别返回 student_profile_updates，结构为 [{"subject_ref":{"kind":"student","id":"原样复制候选 id","revision":"原样复制候选 revision"},"include":true,"display_name":"学生显示名","record_kind":"reported_statement","source":"教师当前输入，采用前核对","basis":"事实或材料依据，未知可为空","observed_at":"已知时填写带时区的 ISO 日期时间，时刻未知则留空并追问","review_at":"教师观察或阶段性判断的复查时间","expires_at":"教师观察或阶段性判断的失效时间","record_summary":"只描述这名学生与本次事件有关的待核事实","profile_base_revision":0,"profile_update":{"summary":"合并后的当前档案摘要","dimensions":[],"open_questions":[],"support_focus":[]}}]。不要把两名学生合并成一份 profile_update，不预设责任方，不认定欺凌。后续补充必须同时修订 SOP 和各学生拟更新内容；profile_base_revision 只作占位，本机会绑定真实当前版本。

若本轮是在回答上一轮追问并提供了上一轮待核对草稿，必须在其基础上修订，保留未被新事实否定的内容；明确是另一件新事项时不得合并旧草稿。你只能形成草稿，不得自动诊断、认定欺凌、决定惩戒、对外发送或结案。即时危险必须提醒教师先保护学生并联系有权角色。"""
_PROFILE_INSTRUCTION = """当前会话从一个已选学生的档案页发起。只处理这名学生，不得改选其他学生。每次在已提供的当前档案上持续补充、修正和完善，而不是新建历史版本。若信息足以整理，返回且只返回一个 student_growth 或 student_support 的 record 工作项，并原样复制已选学生引用。draft.profile_update 必须是合并后的完整当前档案，包含非空 summary、dimensions、open_questions、support_focus；dimensions 必须使用学生档案整理规则中的核心维度 key，都不合适时才可为学生新增简短英文 key。不得删除与本轮无关的已有维度。发现明显矛盾或关键缺失时，在 clarification_questions 中最多追问 3 个真正有帮助的问题；仍可把已经确定的内容形成完整更新草稿。"""
_AUDIO_TRIAGE_INSTRUCTION = """你是班主任事务整理助手。当前最后一条用户消息包含教师录音。只返回 json 对象，contract_version 必须是 class_teacher_audio_triage.v1。先在 transcript 字段逐字转写教师说话，保留姓名、日期、数字和否定词，不推断录音中没有的内容；再返回与 class_teacher_triage.v1 相同的 assistant_message、clarification_questions、work_items。把事务分到 student_growth、student_support、conflict_safety、class_operations、activities_culture、school_coordination 六域，并选择 record、plan_calendar、sop 之一。你只能形成草稿，不得自动诊断、分析情绪、认定欺凌、决定惩戒、对外发送或结案。即时危险必须提醒教师先保护学生并联系有权角色。同名学生或无法唯一匹配时 subject_refs 留空并加入待核对项。"""
_REVISION_INSTRUCTION = """你只调整现有班主任草稿。只返回 JSON 对象：contract_version 必须是 class_teacher_draft_revision.v1，content 只返回需要新增或改动的顶层字段，不要重复未改字段；系统会按键合并并保留未返回内容。计划 actions 只返回新增或改动项，每项必须带原 draft_action_id；SOP steps 只返回新增或改动项，每项必须带原 key，绝不能改写或删除安全必做步骤和教师分流步骤。需要修改学生档案建议时，只返回受影响学生的完整 student_profile_updates 项并保留其 subject_ref；未修改的学生不要重复返回。根据教师要求同时修订后续 SOP 与档案建议。不得正式保存、外发、诊断、作欺凌认定、决定惩戒或结案。"""
_AFFAIR_FLOW_INSTRUCTION = """你是班主任事务流程助理。教师正在推进一个已经建立的事务处理流程，现在补充了新情况。只返回一个 JSON 对象，不要 Markdown。严格按这个结构返回：
{"contract_version":"class_teacher_affair_flow_revision.v1","assistant_message":"给教师的简短说明","items":[{"item_id":"rev-1","kind":"add_step","title":"可执行步骤名","details":"具体做法和完成标准","depends_on":["已存在步骤key"],"reason":"为什么建议这一步"},{"item_id":"rev-2","kind":"revise_step","target_step_key":"未开始普通步骤key","title":"新步骤名","details":"新步骤说明","reason":"为什么这样改"},{"item_id":"rev-3","kind":"note","text":"提醒教师核对的建议","reason":"依据"}]}

规则：kind 只能是 add_step、revise_step、note。只能新增普通步骤、修改未开始普通步骤的文案、或给出核对建议；绝不能删除或弱化安全必做步骤，绝不能改动教师分流决策点及其选项，绝不新增决策点。新步骤和修改必须基于教师补充的新情况、已完成步骤的结果和分流决定；depends_on 只能引用给定事务里已存在的步骤 key。不提惩戒、诊断、欺凌认定或对外发送建议。items 最多 5 条，没有需要调整时 items 返回一条 note 说明当前流程无需改动。"""
_STUDENT_REFERENCE_RESELECTION_MESSAGE = "学生版本信息不一致，请重新选择"
_PROFILE_DIMENSION_GUIDE = "、".join(
    f"{key}（{label}）" for key, label in CORE_PROFILE_DIMENSIONS
)
# 档案整理规则依赖核心维度清单，维度变化时随 CORE_PROFILE_DIMENSIONS 自动同步。
_PROFILE_ORGANIZATION_RULES = (
    "学生档案整理规则：只要输入描述了学生情况，profile_update 必须把信息分入所有适用维度，"
    "不能只写进 summary。核心维度固定为："
    + _PROFILE_DIMENSION_GUIDE
    + "。优先使用这些核心 key；都不合适时才可为学生新增简短英文 key。"
    "dimensions 的每一项必须是包含 key、label、items 三个字段的对象，items 至少写一条具体事实；"
    "不能只返回维度 key 名称，也不能返回空 items。"
    "每个适用维度的 items 写教师提供的具体事实，不写空泛评价；"
    "教师已说明有效或无效的做法必须进入 effective_methods。"
    "support_focus 的 need、effective_methods、next_actions 必须结合这名学生的具体情况给出可执行做法"
    "（例如利用其优势安排班级角色、约定具体提醒信号），"
    "不得只写“多观察、多沟通、多关注”这类套话。"
    "追问规则：clarification_questions 优先问能帮助理解学生的问题——"
    "什么情境下表现好或差、什么方式对他有效或无效、家庭与同伴中的关键细节、教师试过的办法及效果；"
    "只有涉及专业结论规则时才追问材料来源、日期与依据。"
)
# 档案整理规则对分诊与学生档案页两种会话同样生效。
_TRIAGE_INSTRUCTION = _TRIAGE_INSTRUCTION + "\n\n" + _PROFILE_ORGANIZATION_RULES
_PROFILE_INSTRUCTION = _PROFILE_INSTRUCTION + "\n\n" + _PROFILE_ORGANIZATION_RULES
# 学生引用是稳定学籍标识（班级|学号/姓名）或内部主体编号，均为无控制字符短文本。
_STUDENT_REFERENCE_PATTERN = SUBJECT_REF_PATTERN
_STUDENT_REFERENCE_ISSUE_CODES = frozenset(
    {"student_revision_mismatch", "unknown_student_reference"}
)
_EXPLICIT_NEW_TOPIC_PATTERN = re.compile(
    r"(?:另外|另一个|另一件|新事项|再说一件|还有一件).{0,8}(?:新|另|学生|事情|事项|冲突)"
)
_FOLLOW_UP_DETAIL_PATTERN = re.compile(
    r"(?:补充|已确认|已经|目前|双方|无人受伤|没有受伤|起因|经过|后来)"
)
_PROFESSIONAL_REPORT_PATTERN = re.compile(r"(?:确诊|诊断|专业评估|专业结论)")
_PROFESSIONAL_NEGATION_PATTERN = re.compile(r"(?:没有|无|未|没|并非|不是|尚无|尚未)")
_PROFESSIONAL_NEGATION_WINDOW = 6
_DOMAIN_ALIASES = {
    "conflict_support": "conflict_safety",
    "conflict": "conflict_safety",
    "safety_incident": "conflict_safety",
    "student_record": "student_growth",
    "student_profile": "student_growth",
    "growth_record": "student_growth",
    "support": "student_support",
    "home_school": "student_support",
    "class_management": "class_operations",
    "class_operation": "class_operations",
    "daily_operations": "class_operations",
    "activity": "activities_culture",
    "class_activity": "activities_culture",
    "school_cooperation": "school_coordination",
    "coordination": "school_coordination",
}
_MODE_ALIASES = {
    "plan": "plan_calendar",
    "calendar": "plan_calendar",
    "schedule": "plan_calendar",
    "sop_workflow": "sop",
    "workflow": "sop",
}
_INTENT_ALIASES = {
    "new": "create",
    "update": "append",
    "revise": "append",
    "ask": "follow_up",
    "decompose": "plan",
}
_PROFESSIONAL_SOURCE_PATTERN = re.compile(
    r"(?:医院|医师|医生|心理中心|医疗机构|专业机构|评估机构|诊断证明|评估报告)"
)
_PROFESSIONAL_BASIS_PATTERN = re.compile(r"(?:书面|报告|证明|病历|评估单|诊断书)")
_EXPLICIT_DATE_PATTERN = re.compile(
    r"(?:\d{4}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|(?:今天|今日|昨天|昨日|前天)"
)
_SCHOOL_SUPPORT_PATTERN = re.compile(
    r"(?:当前在校|在校|学校|课堂).{0,16}(?:支持|安排|措施|调整|协助|采用|已采用)|(?:座位调整|前排座位|任务拆分|任务分段|提醒方式|简短提醒|情绪安抚)"
)
_PROFESSIONAL_RECOMMENDATION_PATTERN = re.compile(
    r"(?:专业建议|医嘱|报告建议|建议学校|避免|不宜)"
)


def _local_reference_message() -> str:
    local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    return (
        f"本机参考日期：{local_now.date().isoformat()}；"
        "本机时区：Asia/Shanghai（UTC+08:00）。"
        "所有相对日期和未写年份的月日都以这个日期与时区解释，"
        "并把推导后的实际日期写入草稿供教师核对。"
    )


@dataclass(frozen=True, slots=True)
class DomainModelRequest:
    task_kind: str
    prompt_contract_version: str
    messages: tuple[dict[str, str], ...]
    metadata_only: bool = True
    max_send_attempts: int = 1
    automatic_retry: bool = False


class ClassTeacherAITaskAdapter:
    """B Adapter; domain text stays B-owned except approved local call diagnostics."""

    module = "class_teacher"
    task_kinds = {
        "class_teacher.intake_triage",
        "class_teacher.draft_revision",
        "class_teacher.affair_flow_revision",
    }
    legacy_task_kind = "class_teacher.intake"

    def __init__(
        self,
        conversations: ConversationStore,
        class_roster,
        adoption,
        configured_model,
        student_cards,
        sop=None,
    ) -> None:
        self.conversations = conversations
        self.class_roster = class_roster
        self.adoption = adoption
        self.configured_model = configured_model
        self.student_cards = student_cards
        self.sop = sop

    def execute(
        self,
        task: StoredTask,
        *,
        model_gateway: WorkspaceAITaskModelGateway,
    ) -> AdapterResult:
        task_kind = self._domain_task_kind(task)
        source_ref = _ref_mapping(task.source_ref)
        context_refs = [_ref_mapping(item) for item in task.context_refs]
        request = self.build_model_request(
            task_kind=task_kind,
            source_ref=source_ref,
            context_refs=context_refs,
        )
        if self.configured_model is None:
            raise RuntimeError("class_teacher_model_unavailable")
        if task_kind == "class_teacher.affair_flow_revision":
            diagnostic_task_kind = "class_teacher_affair_flow_revision"
        else:
            diagnostic_task_kind = (
                "class_teacher_draft_revision"
                if task_kind.endswith("draft_revision")
                else "class_teacher_intake"
            )
        try:
            raw = self.configured_model.invoke_workspace_task(
                task_gateway=model_gateway,
                messages=request.messages,
                operation_id=task.operation_id,
                purpose=diagnostic_task_kind,
                expected_destination_fingerprint=task.model_destination_fingerprint,
            )
        except ModelResponseTruncatedError as exc:
            self._mark_invalid(task, task_kind, task_state="truncated_result")
            raise InvalidAdapterResultError(
                "class-teacher model result is truncated"
            ) from exc
        try:
            payload = _parse_model_payload(raw)
            if not isinstance(payload, dict):
                raise TypeError("model result is not an object")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._mark_invalid(task, task_kind)
            raise InvalidAdapterResultError("class-teacher model result is invalid") from exc
        try:
            persisted = self.persist_model_result(
                task_id=task.task_id,
                source_ref=source_ref,
                context_refs=context_refs,
                result=(
                    payload
                    if task_kind == "class_teacher.affair_flow_revision"
                    else _normalize_triage_payload_compatibility(payload)
                ),
            )
        except VaultError as exc:
            if exc.code.endswith("invalid_result"):
                raise InvalidAdapterResultError("class-teacher model result is invalid") from exc
            raise
        validation_issue_codes = tuple(
            str(item)
            for item in persisted.get("validation_issue_codes", [])
            if isinstance(item, str)
        )
        if validation_issue_codes:
            model_gateway.record_validation(
                operation_id=task.operation_id,
                validation_issue_codes=validation_issue_codes,
                workspace_module=self.module,
                workspace_task_kind=diagnostic_task_kind,
            )
        recovered = self.recover(task)
        if recovered is None:
            raise RuntimeError("class_teacher_proposal_not_persisted")
        return recovered

    def recover(self, task: StoredTask) -> AdapterResult | None:
        if self._domain_task_kind(task) == "class_teacher.affair_flow_revision":
            sync_ref = next(
                (item for item in task.context_refs if item.kind == "affair_sync"),
                None,
            )
            if sync_ref is None or self.sop is None:
                return None
            entry = self.sop.flow_revision_for_sync(
                token="",
                affair_id=task.source_ref.id,
                sync_id=sync_ref.id,
            )
            if entry is None:
                return None
            return AdapterResult(
                proposal_ref_id=str(entry["revision_id"]),
                proposal_revision="1",
                handoffs=(),
                needs_input=False,
            )
        revision_ref = next(
            (item for item in task.context_refs if item.kind == "draft_revision_request"),
            None,
        )
        proposal = self.conversations.proposal_for_task(
            task.task_id,
            draft_revision_request_id=revision_ref.id if revision_ref else None,
        )
        if proposal is None:
            return None
        reference = proposal["proposal_ref"]
        return AdapterResult(
            proposal_ref_id=str(reference["id"]),
            proposal_revision=str(reference["revision"]),
            handoffs=tuple(
                self._handoff(item, expires_on_source_change=revision_ref is None)
                for item in proposal["handoffs"]
            ),
            needs_input=bool(proposal["needs_input"]),
        )

    def adopt(
        self,
        handoff: HandoffSnapshot,
        *,
        adoption_id: str,
        draft_revision: str,
        target_revision: str,
    ) -> AdoptionResult:
        domain = self.conversations.handoff_by_draft_id(handoff.draft_ref.id)
        try:
            receipt = self.adoption.adopt(
                token="",
                handoff_id=str(domain["handoff_id"]),
                draft_revision=int(draft_revision),
                target_revision=target_revision,
                operation_id=adoption_id,
                adoption_id=adoption_id,
            )
        except VaultError as exc:
            persisted = self.find_adoption(adoption_id)
            if persisted is not None:
                return persisted
            self.adoption.release_uncommitted(
                handoff_id=str(domain["handoff_id"]),
                adoption_id=adoption_id,
                target_revision=target_revision,
            )
            raise RevisionConflictError(exc.message) from exc
        return _adoption_result(receipt)

    def find_adoption(self, adoption_id: str) -> AdoptionResult | None:
        receipt = self.adoption.find_receipt(adoption_id)
        return _adoption_result(receipt) if receipt is not None else None

    def build_model_request(
        self,
        *,
        task_kind: str,
        source_ref: Mapping[str, object],
        context_refs: list[Mapping[str, object]],
    ) -> DomainModelRequest:
        if task_kind not in self.task_kinds:
            raise VaultError("class_teacher_task_kind_invalid", "班主任 AI 任务类型无效", status_code=422)
        if task_kind == "class_teacher.affair_flow_revision":
            if self.sop is None:
                raise VaultError("class_teacher_task_kind_invalid", "班主任事务流程修订不可用", status_code=422)
            sync_ref = next((item for item in context_refs if item.get("kind") == "affair_sync"), None)
            if source_ref.get("kind") != "affair" or sync_ref is None:
                raise VaultError("class_teacher_source_ref_invalid", "流程修订任务来源无效", status_code=422)
            affair_id = str(source_ref.get("id") or "")
            sync_id = str(sync_ref.get("id") or "")
            snapshot = self.sop.sop_snapshot_for_model(token="", affair_id=affair_id)
            sync_text = self.sop.sync_request_text(token="", affair_id=affair_id, sync_id=sync_id)
            return DomainModelRequest(
                task_kind=task_kind,
                prompt_contract_version="class_teacher_affair_flow_revision.v1",
                messages=(
                    {"role": "system", "content": _AFFAIR_FLOW_INSTRUCTION},
                    {
                        "role": "user",
                        "content": "事务当前状态（安全必做步骤和教师分流决策点不可改动）："
                        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                    },
                    {"role": "user", "content": "教师补充的新情况：" + sync_text},
                ),
            )
        if task_kind == "class_teacher.draft_revision":
            revision_ref = next((item for item in context_refs if item.get("kind") == "draft_revision_request"), None)
            handoff_ref = next((item for item in context_refs if item.get("kind") == "handoff"), None)
            if revision_ref is None or handoff_ref is None:
                raise VaultError("class_teacher_draft_revision_not_found", "AI 任务缺少草稿调整引用", status_code=422)
            material = self.conversations.draft_revision_material(str(revision_ref.get("id") or ""))
            request = material["request"]
            handoff = material["handoff"]
            if not isinstance(request, Mapping) or not isinstance(handoff, Mapping):
                raise VaultError("class_teacher_draft_revision_not_found", "草稿调整任务不存在", status_code=404)
            if (
                str(request.get("handoff_id") or "") != str(handoff_ref.get("id") or "")
                or str(request.get("source_draft_revision") or "") != str(handoff_ref.get("revision") or "")
                or str(handoff.get("draft_revision") or "") != str(handoff_ref.get("revision") or "")
                or source_ref.get("kind") != "handoff"
                or str(source_ref.get("id") or "") != str(handoff_ref.get("id") or "")
                or str(source_ref.get("revision") or "") != str(handoff_ref.get("revision") or "")
            ):
                raise VaultError("class_teacher_draft_conflict", "草稿调整引用已经变化", status_code=409)
            return DomainModelRequest(
                task_kind=task_kind,
                prompt_contract_version="class_teacher_draft_revision.v1",
                messages=(
                    {"role": "system", "content": _REVISION_INSTRUCTION},
                    *self._profile_messages_for_refs(list(handoff.get("subject_refs") or [])),
                    {"role": "user", "content": "当前草稿：" + json.dumps(handoff.get("content") or {}, ensure_ascii=False, separators=(",", ":"))},
                    {"role": "user", "content": "调整要求：" + str(request.get("instruction") or "")},
                ),
            )
        if source_ref.get("kind") != "conversation":
            raise VaultError("class_teacher_source_ref_invalid", "班主任 AI 任务来源无效", status_code=422)
        conversation = self.conversations.get(str(source_ref.get("id") or ""))
        if str(conversation["revision"]) != str(source_ref.get("revision") or ""):
            raise VaultError("class_teacher_source_revision_conflict", "会话已变化，请从最新内容重新整理", status_code=409)
        turn_ids = {str(item.get("id") or "") for item in context_refs if item.get("kind") == "turn"}
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _TRIAGE_INSTRUCTION},
            {"role": "system", "content": _local_reference_message()},
        ]
        profile_ref = next(
            (item for item in context_refs if item.get("kind") == "student_profile"),
            None,
        )
        candidates = self._candidates(conversation, profile_ref=profile_ref)
        current_turn_id = next(
            (
                str(item.get("id") or "")
                for item in context_refs
                if item.get("kind") == "turn"
            ),
            None,
        )
        previous_handoff = self.conversations.latest_revisable_handoff(
            conversation_id=str(conversation["conversation_id"]),
            exclude_turn_id=current_turn_id,
        )
        if previous_handoff is not None and not _continues_previous_draft(
            conversation,
            previous_handoff,
        ):
            previous_handoff = None
        prompt_candidates = _prompt_candidates(
            candidates,
            conversation=conversation,
            previous_handoff=previous_handoff,
        )
        if profile_ref is not None:
            messages.append({"role": "system", "content": _PROFILE_INSTRUCTION})
            messages.extend(self._profile_messages_for_refs([profile_ref]))
        else:
            conversation_text = "\n".join(
                str(turn.get("teacher_message") or "")
                for turn in list(conversation["turns"])
                if isinstance(turn, Mapping)
            )
            contexts = self.student_cards.model_contexts_for_mentions(
                token="",
                class_label=str(conversation.get("homeroom_class") or "").strip() or None,
                text=conversation_text,
            )
            if contexts:
                affair_contexts = [
                    {
                        key: value
                        for key, value in context.items()
                        if key != "subject_ref"
                    }
                    for context in contexts
                ]
                messages.append({
                    "role": "user",
                    "content": "本机找到的涉事学生当前档案（作为本次事务背景，也可据新信息提出档案更新建议）："
                    + json.dumps(affair_contexts, ensure_ascii=False, separators=(",", ":")),
                })
        if prompt_candidates:
            messages.append({
                "role": "user",
                "content": "当前班学生候选（同名时不得自行选择，无唯一匹配时留空）："
                + json.dumps(prompt_candidates, ensure_ascii=False, separators=(",", ":")),
            })
        if previous_handoff is not None:
            messages.append({
                "role": "user",
                "content": "上一轮待核对草稿（本轮补充应在此基础上修订，不要另起无关方案）："
                + json.dumps(
                    {
                        "draft_id": previous_handoff["draft_id"],
                        "domain": previous_handoff["domain"],
                        "handling_mode": previous_handoff["handling_mode"],
                        "content": previous_handoff["content"],
                        "subject_refs": previous_handoff["subject_refs"],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            })
        for turn in list(conversation["turns"]):
            if not isinstance(turn, Mapping):
                continue
            messages.append({"role": "user", "content": str(turn.get("teacher_message") or "")})
            if turn.get("assistant_message"):
                messages.append({"role": "assistant", "content": str(turn["assistant_message"])})
        if turn_ids and not any(str(turn.get("turn_id")) in turn_ids for turn in list(conversation["turns"])):
            raise VaultError("class_teacher_turn_not_found", "AI 任务轮次不存在", status_code=404)
        return DomainModelRequest(
            task_kind=task_kind,
            prompt_contract_version="class_teacher_triage.v1",
            messages=tuple(messages),
        )

    def build_audio_model_messages(
        self,
        *,
        conversation_id: str,
        audio_base64: str,
    ) -> tuple[dict[str, object], ...]:
        conversation = self.conversations.get(conversation_id)
        messages: list[dict[str, object]] = [
            {"role": "system", "content": _AUDIO_TRIAGE_INSTRUCTION},
            {"role": "system", "content": _local_reference_message()},
        ]
        focused = self._focused_ref(conversation)
        candidates = self._candidates(conversation, profile_ref=focused)
        if focused is not None:
            messages.append({"role": "system", "content": _PROFILE_INSTRUCTION})
            messages.extend(self._profile_messages_for_refs([focused]))
        if candidates:
            messages.append(
                {
                    "role": "user",
                    "content": "当前班学生候选（同名时不得自行选择，无唯一匹配时留空）："
                    + json.dumps(
                        candidates,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
        for turn in list(conversation["turns"]):
            if not isinstance(turn, Mapping):
                continue
            messages.append(
                {
                    "role": "user",
                    "content": str(turn.get("teacher_message") or ""),
                }
            )
            if turn.get("assistant_message"):
                messages.append(
                    {
                        "role": "assistant",
                        "content": str(turn["assistant_message"]),
                    }
                )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio_base64, "format": "wav"},
                    },
                    {
                        "type": "text",
                        "text": "转写这段普通话录音，并按合同整理班主任事务。",
                    },
                ],
            }
        )
        return tuple(messages)

    def normalize_triage_result(
        self,
        *,
        conversation: Mapping[str, object],
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        focused = self._focused_ref(conversation)
        candidate_items = self._candidates(conversation, profile_ref=focused)
        candidates = {
            (item["id"], item["revision"]): item for item in candidate_items
        }
        candidates_by_id = {item["id"]: item for item in candidate_items}
        focused_reference_issue_code: str | None = None
        if focused is not None:
            current_focused = candidates_by_id.get(str(focused["id"]))
            if (
                current_focused is not None
                and str(current_focused["revision"]) != str(focused["revision"])
            ):
                focused_reference_issue_code = "student_revision_mismatch"
        name_counts: dict[str, int] = {}
        for item in candidate_items:
            name = item["display_name"]
            name_counts[name] = name_counts.get(name, 0) + 1
        normalized_result = deepcopy(dict(result))
        _canonicalize_work_item_enums(normalized_result)
        _sanitize_profile_update_dimensions(normalized_result)
        normalized_result["clarification_questions"] = _bounded_clarification_questions(
            normalized_result.get("clarification_questions")
        )
        items = normalized_result.get("work_items")
        requested_mode = _explicitly_requested_mode(conversation)
        turns = [
            turn
            for turn in list(conversation.get("turns") or [])
            if isinstance(turn, Mapping)
        ]
        conversation_text = "\n".join(
            str(turn.get("teacher_message") or "")
            for turn in turns
        )
        conflict_items: list[dict[str, Any]] = []
        professional_items: list[dict[str, Any]] = []
        conflict_profiles: list[dict[str, object]] | None = None
        current_turn_id = str(turns[-1].get("turn_id") or "") if turns else ""
        previous_handoff = self.conversations.latest_revisable_handoff(
            conversation_id=str(conversation.get("conversation_id") or ""),
            exclude_turn_id=current_turn_id or None,
        )
        if previous_handoff is not None and not _continues_previous_draft(
            conversation,
            previous_handoff,
        ):
            previous_handoff = None
        if focused is not None and isinstance(items, list) and len(items) > 1:
            raise VaultError(
                "class_teacher_triage_invalid_result",
                "学生档案页一次只能形成一份当前档案更新",
                status_code=422,
            )
        for raw_item in items if isinstance(items, list) else []:
            if not isinstance(raw_item, dict):
                continue
            _normalize_plan_draft(raw_item, source_text=conversation_text)
            if requested_mode is not None:
                primary = str(raw_item.get("primary_mode") or "")
                secondary = raw_item.get("secondary_modes")
                if (
                    primary in {"record", "plan_calendar", "sop"}
                    and primary != requested_mode
                ):
                    alternatives = (
                        [str(item) for item in secondary]
                        if isinstance(secondary, list)
                        else []
                    )
                    raw_item["primary_mode"] = requested_mode
                    raw_item["secondary_modes"] = list(dict.fromkeys([
                        primary,
                        *(item for item in alternatives if item != requested_mode),
                    ]))[:2]
                    raw_item.pop("handoff_key", None)
            if (
                raw_item.get("domain") == "conflict_safety"
                and requested_mode is None
            ):
                _promote_conflict_to_sop(
                    raw_item,
                    source_text=conversation_text,
                    previous_handoff=(
                        previous_handoff
                        if previous_handoff is not None
                        and previous_handoff.get("domain") == "conflict_safety"
                        else None
                    ),
                )
                if conflict_profiles is None:
                    conflict_profiles = _conflict_student_profiles(
                        self.student_cards.model_contexts_for_mentions(
                            token="",
                            class_label=(
                                str(conversation.get("homeroom_class") or "").strip()
                                or None
                            ),
                            text=conversation_text,
                        )
                    )
                draft = raw_item.get("draft")
                if isinstance(draft, dict) and conflict_profiles:
                    draft["student_profiles"] = deepcopy(conflict_profiles)
                conflict_items.append(raw_item)
            refs = raw_item.get("subject_refs", [])
            if not isinstance(refs, list):
                raise VaultError(
                    "class_teacher_triage_invalid_result",
                    "AI 返回的学生引用无效",
                    status_code=422,
                )
            selected: list[dict[str, object]] = []
            requires_teacher_choice = False
            validation_issue_codes: list[str] = (
                [focused_reference_issue_code]
                if focused_reference_issue_code is not None
                else []
            )
            for ref in refs:
                if (
                    not isinstance(ref, Mapping)
                    or set(ref) != {"kind", "id", "revision"}
                    or ref.get("kind") != "student"
                    or not isinstance(ref.get("id"), str)
                    or _STUDENT_REFERENCE_PATTERN.fullmatch(str(ref.get("id"))) is None
                    or not isinstance(ref.get("revision"), str)
                    or not str(ref.get("revision")).strip()
                    or len(str(ref.get("revision")).strip()) > 128
                ):
                    raise VaultError(
                        "class_teacher_triage_invalid_result",
                        "AI 返回的学生引用无效",
                        status_code=422,
                    )
                key = (str(ref["id"]), str(ref["revision"]).strip())
                candidate = candidates.get(key)
                if candidate is None:
                    issue_code = (
                        "student_revision_mismatch"
                        if key[0] in candidates_by_id
                        else "unknown_student_reference"
                    )
                    if issue_code not in validation_issue_codes:
                        validation_issue_codes.append(issue_code)
                    requires_teacher_choice = True
                    continue
                if name_counts.get(candidate["display_name"], 0) > 1:
                    requires_teacher_choice = True
                else:
                    selected.append(dict(ref))
            if validation_issue_codes:
                raw_item["subject_refs"] = []
                missing = (
                    raw_item.get("missing_fields")
                    if isinstance(raw_item.get("missing_fields"), list)
                    else []
                )
                raw_item["missing_fields"] = list(dict.fromkeys([
                    *missing,
                    _STUDENT_REFERENCE_RESELECTION_MESSAGE,
                ]))
                draft = raw_item.get("draft")
                if not isinstance(draft, dict):
                    raise VaultError(
                        "class_teacher_triage_invalid_result",
                        "AI 返回的草稿无效",
                        status_code=422,
                    )
                draft["validation_issue_codes"] = validation_issue_codes
            elif requires_teacher_choice:
                raw_item["subject_refs"] = []
                missing = (
                    raw_item.get("missing_fields")
                    if isinstance(raw_item.get("missing_fields"), list)
                    else []
                )
                raw_item["missing_fields"] = [*missing, "请选择一名同名学生"]
            else:
                raw_item["subject_refs"] = selected
            if (
                raw_item.get("domain") == "conflict_safety"
                and not validation_issue_codes
                and not requires_teacher_choice
            ):
                self._normalize_conflict_profile_updates(
                    item=raw_item,
                    selected_refs=selected,
                    candidates_by_id=candidates_by_id,
                )
            if (
                _is_professional_report(conversation_text)
                and raw_item.get("domain") in {"student_growth", "student_support"}
                and raw_item.get("primary_mode") == "record"
                and not validation_issue_codes
                and not requires_teacher_choice
                and selected
            ):
                self._normalize_professional_record(
                    item=raw_item,
                    selected_ref=selected[0],
                    candidate=candidates_by_id.get(str(selected[0].get("id") or ""), {}),
                    source_text=conversation_text,
                )
                professional_items.append(raw_item)
            if focused is not None:
                if (
                    raw_item.get("primary_mode") != "record"
                    or raw_item.get("domain") not in {"student_growth", "student_support"}
                ):
                    raise VaultError(
                        "class_teacher_triage_invalid_result",
                        "AI 没有按学生档案方式整理本轮信息",
                        status_code=422,
                    )
                if not validation_issue_codes:
                    raw_item["subject_refs"] = [{
                        "kind": "student",
                        "id": str(focused["id"]),
                        "revision": str(focused["revision"]),
                    }]
                draft = raw_item.get("draft")
                update = draft.get("profile_update") if isinstance(draft, dict) else None
                if not isinstance(update, dict):
                    raise VaultError(
                        "class_teacher_triage_invalid_result",
                        "AI 没有形成学生档案更新草稿",
                        status_code=422,
                    )
                self.student_cards.validate_profile_update(update)
                current = self.student_cards.model_context(
                    token="",
                    subject_id=str(focused["id"]),
                )["profile"]
                draft["profile_base_revision"] = int(
                    current.get("revision") if isinstance(current, Mapping) else 0
                )
        if conflict_items:
            normalized_result["clarification_questions"] = (
                _conflict_clarification_questions(
                    source_text=conversation_text,
                    existing=normalized_result.get("clarification_questions"),
                    items=conflict_items,
                )
            )
        elif professional_items:
            normalized_result["clarification_questions"] = (
                _professional_clarification_questions(
                    source_text=conversation_text,
                    existing=normalized_result.get("clarification_questions"),
                )
            )
        return normalized_result

    @staticmethod
    def _focused_ref(conversation: Mapping[str, object]) -> dict[str, str] | None:
        subject_id = str(conversation.get("focused_subject_id") or "").strip()
        revision = str(conversation.get("focused_subject_revision") or "").strip()
        if not subject_id or not revision:
            return None
        return {"kind": "student_profile", "id": subject_id, "revision": revision}

    def _candidates(
        self,
        conversation: Mapping[str, object],
        *,
        profile_ref: Mapping[str, object] | None,
    ) -> list[dict[str, str]]:
        if profile_ref is not None:
            subject = self.student_cards.support.get_subject(
                token="",
                subject_id=str(profile_ref.get("id") or ""),
            )
            return [{
                "id": str(subject["subject_id"]),
                "revision": str(subject["revision"]),
                "display_name": str(subject.get("display_name") or ""),
                "class_label": str(subject.get("class_label") or ""),
            }]
        return self.class_roster.ai_candidates(
            token="",
            class_label=str(conversation.get("homeroom_class") or "").strip() or None,
        )

    def _profile_messages_for_refs(
        self,
        refs: list[Mapping[str, object]],
    ) -> tuple[dict[str, str], ...]:
        contexts: list[dict[str, object]] = []
        for ref in refs[:4]:
            subject_id = str(ref.get("id") or "")
            try:
                contexts.append(self.student_cards.model_context(token="", subject_id=subject_id))
            except VaultError:
                continue
        if not contexts:
            return ()
        return ({
            "role": "user",
            "content": "当前学生档案与支持情况："
            + json.dumps(contexts, ensure_ascii=False, separators=(",", ":")),
        },)

    def _normalize_conflict_profile_updates(
        self,
        *,
        item: dict[str, Any],
        selected_refs: list[dict[str, object]],
        candidates_by_id: dict[str, dict[str, str]],
    ) -> None:
        draft = item.get("draft")
        if not isinstance(draft, dict) or not selected_refs:
            return
        raw_updates = draft.get("student_profile_updates")
        supplied = raw_updates if isinstance(raw_updates, list) else []
        updates_by_id = {
            str(ref.get("id") or ""): update
            for update in supplied
            if isinstance(update, dict)
            for ref in [update.get("subject_ref")]
            if isinstance(ref, Mapping) and str(ref.get("id") or "")
        }
        normalized: list[dict[str, object]] = []
        verification = [
            str(question).strip()
            for question in list(draft.get("to_verify") or [])
            if str(question).strip()
        ]
        for selected in selected_refs:
            subject_id = str(selected.get("id") or "")
            candidate = candidates_by_id.get(subject_id, {})
            current_profile, base_revision = self._current_profile_for_candidate(
                subject_id=subject_id,
                candidate=candidate,
            )
            supplied_update = updates_by_id.get(subject_id)
            if supplied_update is None:
                open_questions = list(dict.fromkeys([
                    *[
                        str(question).strip()
                        for question in list(current_profile.get("open_questions") or [])
                        if str(question).strip()
                    ],
                    *verification,
                    "本次冲突经过与后续支持需要仍待教师核对。",
                ]))
                profile_update = {
                    "summary": str(current_profile.get("summary") or "").strip()
                    or "本次同伴冲突情况待教师核对。",
                    "dimensions": deepcopy(list(current_profile.get("dimensions") or [])),
                    "open_questions": open_questions,
                    "support_focus": deepcopy(list(current_profile.get("support_focus") or [])),
                }
                supplied_update = {
                    "include": False,
                    "record_kind": "reported_statement",
                    "source": "教师当前输入，采用前核对",
                    "basis": "",
                    "observed_at": str(draft.get("observed_at") or ""),
                    "record_summary": str(draft.get("summary") or "").strip(),
                    "profile_update": profile_update,
                }
            else:
                supplied_update = deepcopy(supplied_update)
                self.student_cards.validate_profile_update(
                    supplied_update.get("profile_update")
                )
            normalized.append({
                **supplied_update,
                "subject_ref": dict(selected),
                "display_name": str(candidate.get("display_name") or "").strip()
                or str(supplied_update.get("display_name") or "").strip(),
                "profile_base_revision": base_revision,
            })
        draft["student_profile_updates"] = normalized

    def _normalize_professional_record(
        self,
        *,
        item: dict[str, Any],
        selected_ref: dict[str, object],
        candidate: Mapping[str, object],
        source_text: str,
    ) -> None:
        draft = item.get("draft")
        if not isinstance(draft, dict):
            raise VaultError(
                "class_teacher_triage_invalid_result",
                "AI 返回的学生专业信息草稿无效",
                status_code=422,
            )
        current_profile, base_revision = self._current_profile_for_candidate(
            subject_id=str(selected_ref.get("id") or ""),
            candidate=candidate,
        )
        has_source = bool(_PROFESSIONAL_SOURCE_PATTERN.search(source_text))
        has_basis = bool(_PROFESSIONAL_BASIS_PATTERN.search(source_text))
        has_date = bool(_EXPLICIT_DATE_PATTERN.search(source_text))
        has_school_support = bool(_SCHOOL_SUPPORT_PATTERN.search(source_text))

        complete_evidence = has_source and has_basis and has_date
        draft["record_kind"] = (
            "professional_conclusion" if complete_evidence else "reported_statement"
        )
        draft["source"] = (
            "教师补充的专业书面材料，采用前核对"
            if has_source and has_basis
            else "教师当前输入，采用前核对"
        )
        draft["basis"] = str(draft.get("basis") or "").strip() if has_basis else ""
        draft["observed_at"] = _professional_date_from_text(source_text) if has_date else ""
        draft["current_school_support"] = (
            str(draft.get("current_school_support") or "").strip()
            or _school_support_from_text(source_text)
            if has_school_support
            else ""
        )
        if not _PROFESSIONAL_RECOMMENDATION_PATTERN.search(source_text):
            draft["professional_recommendations"] = ""
            draft["avoidances"] = ""

        questions = _professional_clarification_questions(
            source_text=source_text,
            existing=[],
        )
        raw_profile = draft.get("profile_update")
        try:
            profile = self.student_cards.validate_profile_update(raw_profile)
        except VaultError:
            profile = _professional_profile_fallback(
                current_profile=current_profile,
                questions=questions,
            )
        if not complete_evidence:
            profile = _professional_profile_fallback(
                current_profile=current_profile,
                questions=questions,
            )
        else:
            current_summary = str(current_profile.get("summary") or "").strip()
            safe_summary = "教师补充了可核对的专业书面材料、当前在校支持和需避免做法；正式采用前仍由教师核对原始材料。"
            profile["summary"] = (
                f"{current_summary} {safe_summary}".strip()
                if current_summary and safe_summary not in current_summary
                else current_summary or safe_summary
            )[:4000]
            profile["open_questions"] = list(dict.fromkeys([
                *[
                    str(question).strip()
                    for question in list(current_profile.get("open_questions") or [])
                    if str(question).strip()
                ],
                *questions,
            ]))
        draft["profile_update"] = profile
        draft["profile_base_revision"] = base_revision
        missing = item.get("missing_fields") if isinstance(item.get("missing_fields"), list) else []
        missing = [
            value
            for value in missing
            if not re.search(r"(?:诊断|专业|结论|书面|依据|在校支持|干预|规避)", str(value))
        ]
        additions: list[str] = []
        if not (has_source and has_basis):
            additions.append("请核对专业结论来源和书面依据")
        if not has_date:
            additions.append("请核对专业结论日期")
        if not has_school_support:
            additions.append("请补充当前在校支持")
        item["missing_fields"] = list(dict.fromkeys([*missing, *additions]))

    def _current_profile_for_candidate(
        self,
        *,
        subject_id: str,
        candidate: Mapping[str, object],
    ) -> tuple[dict[str, object], int]:
        context: Mapping[str, object] | None
        try:
            context = self.student_cards.model_context(token="", subject_id=subject_id)
        except VaultError:
            display_name = str(candidate.get("display_name") or "").strip()
            matches = (
                self.student_cards.model_contexts_for_mentions(
                    token="",
                    class_label=str(candidate.get("class_label") or "").strip() or None,
                    text=display_name,
                    maximum=1,
                )
                if display_name
                else []
            )
            context = matches[0] if matches else None
        raw_profile = context.get("profile") if isinstance(context, Mapping) else None
        if not isinstance(raw_profile, Mapping):
            return {
                "summary": "",
                "dimensions": [],
                "open_questions": [],
                "support_focus": [],
            }, 0
        return deepcopy(dict(raw_profile)), int(raw_profile.get("revision") or 0)

    def persist_model_result(
        self,
        *,
        task_id: str,
        source_ref: Mapping[str, object],
        context_refs: list[Mapping[str, object]],
        result: Mapping[str, Any],
    ) -> dict[str, object]:
        revision_ref = next((item for item in context_refs if item.get("kind") == "draft_revision_request"), None)
        sync_ref = next((item for item in context_refs if item.get("kind") == "affair_sync"), None)
        if sync_ref is not None:
            if self.sop is None:
                raise VaultError("class_teacher_task_kind_invalid", "班主任事务流程修订不可用", status_code=422)
            try:
                parsed = parse_affair_flow_revision(result)
            except VaultError as exc:
                if exc.code.endswith("invalid_result"):
                    self.sop.mark_sync_request_failed(
                        token="",
                        affair_id=str(source_ref.get("id") or ""),
                        sync_id=str(sync_ref.get("id") or ""),
                        outcome="invalid_result",
                    )
                raise
            entry = self.sop.persist_flow_revision(
                token="",
                affair_id=str(source_ref.get("id") or ""),
                sync_id=str(sync_ref.get("id") or ""),
                assistant_message=parsed.assistant_message,
                items=[
                    {
                        "item_id": item.item_id,
                        "kind": item.kind,
                        "target_step_key": item.target_step_key,
                        "title": item.title,
                        "details": item.details,
                        "depends_on": list(item.depends_on),
                        "reason": item.reason,
                        "text": item.text,
                    }
                    for item in parsed.items
                ],
            )
            return {
                "proposal_ref": {"kind": "flow_revision", "id": entry["revision_id"], "revision": "1"},
                "handoff_ids": [],
            }
        if revision_ref is not None:
            request_id = str(revision_ref.get("id") or "")
            try:
                draft = self.conversations.apply_draft_revision_result(
                    request_id=request_id,
                    task_id=task_id,
                    payload=dict(result),
                )
            except VaultError as exc:
                if exc.code == "class_teacher_draft_revision_invalid_result":
                    self.conversations.mark_draft_revision_outcome(
                        request_id=request_id,
                        task_id=task_id,
                        task_state="invalid_result",
                    )
                raise
            return {
                "proposal_ref": {"kind": "draft", "id": draft["draft_id"], "revision": str(draft["draft_revision"])},
                "handoff_ids": [str(draft["handoff_id"])],
            }
        turn = next((item for item in context_refs if item.get("kind") == "turn"), None)
        if turn is None:
            raise VaultError("class_teacher_turn_not_found", "AI 任务缺少会话轮次", status_code=422)
        turn_id = str(turn.get("id") or "")
        try:
            conversation = self.conversations.get(str(source_ref.get("id") or ""))
            normalized_result = self.normalize_triage_result(
                conversation=conversation,
                result=result,
            )
            work_items = normalized_result.get("work_items")
            for index, item in enumerate(work_items if isinstance(work_items, list) else []):
                if isinstance(item, dict):
                    item["work_item_id"] = f"turn-{turn_id}-{index + 1:03d}"
            conversation = self.conversations.apply_triage_result(
                turn_id=turn_id,
                task_id=task_id,
                payload=normalized_result,
            )
        except VaultError as exc:
            if exc.code == "student_profile_invalid":
                # 模型给的档案形状无效属于“模型结果无效”，按分诊无效处理，
                # 避免整轮落入“结果未知”。
                self.conversations.mark_task_outcome(
                    turn_id=turn_id,
                    task_id=task_id,
                    task_state="invalid_result",
                )
                raise VaultError(
                    "class_teacher_triage_invalid_result",
                    "AI 返回的学生档案格式无效",
                    status_code=422,
                ) from exc
            if exc.code == "class_teacher_triage_invalid_result":
                self.conversations.mark_task_outcome(
                    turn_id=turn_id,
                    task_id=task_id,
                    task_state="invalid_result",
                )
            raise
        return {
            "proposal_ref": {"kind": "conversation", "id": conversation["conversation_id"], "revision": str(conversation["revision"])},
            "handoff_ids": [
                str(item["handoff_id"])
                for item in list(conversation["handoffs"])
                if isinstance(item, Mapping) and str(item.get("turn_id")) == turn_id
            ],
            "validation_issue_codes": _validation_issue_codes(
                normalized_result
            ),
        }

    def _domain_task_kind(self, task: StoredTask) -> str:
        if task.task_kind in self.task_kinds:
            return task.task_kind
        if task.task_kind != self.legacy_task_kind:
            raise VaultError(
                "class_teacher_task_kind_invalid",
                "班主任 AI 任务类型无效",
                status_code=422,
            )
        return (
            "class_teacher.draft_revision"
            if any(item.kind == "draft_revision_request" for item in task.context_refs)
            else "class_teacher.intake_triage"
        )

    def _mark_invalid(
        self,
        task: StoredTask,
        task_kind: str,
        task_state: str = "invalid_result",
    ) -> None:
        if task_kind == "class_teacher.draft_revision":
            request = next(item for item in task.context_refs if item.kind == "draft_revision_request")
            self.conversations.mark_draft_revision_outcome(
                request_id=request.id,
                task_id=task.task_id,
                task_state=task_state,
            )
            return
        if task_kind == "class_teacher.affair_flow_revision":
            sync_ref = next((item for item in task.context_refs if item.kind == "affair_sync"), None)
            if sync_ref is not None and task.source_ref.kind == "affair" and self.sop is not None:
                self.sop.mark_sync_request_failed(
                    token="",
                    affair_id=task.source_ref.id,
                    sync_id=sync_ref.id,
                    outcome="invalid_result",
                )
            return
        turn = next((item for item in task.context_refs if item.kind == "turn"), None)
        if turn is not None:
            self.conversations.mark_task_outcome(
                turn_id=turn.id,
                task_id=task.task_id,
                task_state=task_state,
            )

    @staticmethod
    def _handoff(
        item: Mapping[str, object],
        *,
        expires_on_source_change: bool,
    ) -> HandoffDraft:
        missing = item.get("missing_fields") if isinstance(item.get("missing_fields"), list) else []
        refs = item.get("subject_refs") if isinstance(item.get("subject_refs"), list) else []
        return HandoffDraft(
            work_item_id=str(item["work_item_id"]),
            intent=str(item["intent"]),
            handling_mode=str(item["handling_mode"]),
            destination_key=str(item["destination_key"]),
            subject_refs=tuple(_mapping_ref(ref) for ref in refs if isinstance(ref, Mapping)),
            draft_ref=OpaqueRef(kind="draft", id=str(item["draft_id"]), revision=str(item["draft_revision"])),
            missing_fields=tuple(f"missing_{index + 1}" for index, _value in enumerate(missing)),
            source_turn_id=str(item["turn_id"]),
            return_destination_key="class_teacher.home",
            return_focus_ref=str(item["work_item_id"]),
            expires_on_source_change=expires_on_source_change,
        )


def _canonicalize_work_item_enums(result: dict[str, Any]) -> None:
    """把模型返回的近义枚举值归一到合同取值，避免一个别名导致整轮作废。

    只映射明确近义的值；仍不认识的值保持原样，由合同校验报错。
    """
    items = result.get("work_items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "").strip()
        if domain and domain not in DOMAINS:
            mapped_domain = _DOMAIN_ALIASES.get(domain)
            if mapped_domain is not None:
                item["domain"] = mapped_domain
        mode = str(item.get("primary_mode") or "").strip()
        if mode and mode not in HANDLING_MODES:
            mapped_mode = _MODE_ALIASES.get(mode)
            if mapped_mode is not None:
                item["primary_mode"] = mapped_mode
        intent = str(item.get("intent") or "").strip()
        if intent and intent not in INTENTS:
            mapped_intent = _INTENT_ALIASES.get(intent)
            if mapped_intent is not None:
                item["intent"] = mapped_intent


def _teacher_explicit_dates(source_text: str) -> list[str]:
    """教师原文中明确写出的月日日期（无年份时按本机参考年），用于核对计划总截止日。"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    dates: list[str] = []
    for match in re.finditer(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)", source_text):
        year = int(match.group(1)) if match.group(1) else now.year
        month, day = int(match.group(2)), int(match.group(3))
        try:
            dates.append(date(year, month, day).isoformat())
        except ValueError:
            continue
    return list(dict.fromkeys(dates))


def _normalize_plan_draft(item: dict[str, Any], *, source_text: str) -> None:
    if str(item.get("primary_mode") or "") != "plan_calendar":
        return
    draft = item.get("draft")
    if not isinstance(draft, dict):
        return
    raw_candidates = draft.get("candidates")
    if raw_candidates is not None:
        cleaned: list[dict[str, object]] = []
        for entry in raw_candidates[:12] if isinstance(raw_candidates, list) else []:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or "").strip()
            if not label or len(label) > 200:
                continue
            cleaned.append({
                "label": label,
                "reason": str(entry.get("reason") or "").strip()[:400],
                "suggested": bool(entry.get("suggested")),
            })
        draft["candidates"] = cleaned
    teacher_dates = _teacher_explicit_dates(source_text)
    if len(teacher_dates) != 1:
        return
    target = teacher_dates[0]
    raw_deadline = str(draft.get("final_deadline") or "").strip()
    if raw_deadline.startswith(target):
        return
    # 教师只明确说了一个日期而模型给了不同的总截止日（或漏给）：
    # 校正回教师的日期并留痕，教师采用前仍可修改。
    draft["final_deadline"] = f"{target}T18:00:00+08:00"
    note = {"text": f"总截止日已按教师明确说出的目标日期 {target} 校正，采用前请核对。"}
    facts = item.get("time_facts")
    if isinstance(facts, list):
        facts.append(note)
    else:
        item["time_facts"] = [note]
    actions = draft.get("actions")
    for action in actions if isinstance(actions, list) else []:
        if not isinstance(action, dict):
            continue
        due = str(action.get("due_at") or "").strip()
        if due and due[:10] > target:
            action["due_at"] = f"{target}T18:00:00+08:00"


def _drop_contentless_dimensions(update: object) -> None:
    if not isinstance(update, dict):
        return
    dims = update.get("dimensions")
    if not isinstance(dims, list):
        return
    kept: list[object] = []
    for dim in dims:
        if not isinstance(dim, dict):
            continue  # 模型有时把维度写成纯 key 字符串，没有内容，直接丢弃
        items = dim.get("items")
        if isinstance(items, list) and not items:
            continue  # 空维度没有内容
        kept.append(dim)
    update["dimensions"] = kept


def _sanitize_profile_update_dimensions(result: dict[str, Any]) -> None:
    """清理模型档案草稿中的无内容维度，避免一个形状瑕疵让整轮作废。"""
    items = result.get("work_items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        draft = item.get("draft")
        if not isinstance(draft, dict):
            continue
        _drop_contentless_dimensions(draft.get("profile_update"))
        updates = draft.get("student_profile_updates")
        for update in updates if isinstance(updates, list) else []:
            if isinstance(update, dict):
                _drop_contentless_dimensions(update.get("profile_update"))


def _continues_previous_draft(
    conversation: Mapping[str, object],
    previous_handoff: Mapping[str, object] | None = None,
) -> bool:
    turns = [
        turn
        for turn in list(conversation.get("turns") or [])
        if isinstance(turn, Mapping)
    ]
    if len(turns) < 2:
        return False
    current_message = str(turns[-1].get("teacher_message") or "")
    if _EXPLICIT_NEW_TOPIC_PATTERN.search(current_message):
        return False
    previous_questions = turns[-2].get("clarification_questions")
    if (
        isinstance(previous_questions, list)
        and any(str(question).strip() for question in previous_questions)
    ) or bool(_FOLLOW_UP_DETAIL_PATTERN.search(current_message)):
        return True
    return (
        isinstance(previous_handoff, Mapping)
        and str(previous_handoff.get("destination_key") or "")
        == "class_teacher.student.record"
    )


def _prompt_candidates(
    candidates: list[dict[str, str]],
    *,
    conversation: Mapping[str, object],
    previous_handoff: Mapping[str, object] | None,
) -> list[dict[str, str]]:
    """Trim the roster candidate list to named or previously referenced students.

    Same-name candidates share one display name, so a single substring match
    keeps every same-name option. When nothing matches (first turn without a
    name), keep the full list as the fallback.
    """
    conversation_text = "\n".join(
        str(turn.get("teacher_message") or "")
        for turn in list(conversation.get("turns") or [])
        if isinstance(turn, Mapping)
    )
    referenced_ids = {
        str(ref.get("id") or "")
        for ref in list((previous_handoff or {}).get("subject_refs") or [])
        if isinstance(ref, Mapping)
    }
    selected = [
        item
        for item in candidates
        if (item["display_name"] and item["display_name"] in conversation_text)
        or item["id"] in referenced_ids
    ]
    return selected or candidates


def _profile_open_questions(item: Mapping[str, object]) -> list[str]:
    draft = item.get("draft")
    if not isinstance(draft, Mapping):
        return []
    update = draft.get("profile_update")
    if not isinstance(update, Mapping):
        return []
    questions = update.get("open_questions")
    if not isinstance(questions, list):
        return []
    return [
        str(question).strip()
        for question in questions
        if isinstance(question, str) and str(question).strip()
    ]


def _conflict_student_profiles(
    contexts: list[dict[str, object]],
) -> list[dict[str, object]]:
    profiles = [
        {
            "display_name": str(context.get("display_name") or "").strip(),
            "class_label": str(context.get("class_label") or "").strip(),
            "profile": deepcopy(context.get("profile")),
        }
        for context in contexts
        if (
            str(context.get("display_name") or "").strip()
            and isinstance(context.get("profile"), Mapping)
        )
    ]
    profiles.sort(key=lambda profile: str(profile["display_name"]))
    return profiles


def _promote_conflict_to_sop(
    item: dict[str, Any],
    *,
    source_text: str,
    previous_handoff: Mapping[str, object] | None,
) -> None:
    current_mode = str(item.get("primary_mode") or "")
    if current_mode != "sop":
        secondary = item.get("secondary_modes")
        alternatives = (
            [str(value) for value in secondary]
            if isinstance(secondary, list)
            else []
        )
        item["primary_mode"] = "sop"
        item["secondary_modes"] = list(dict.fromkeys([
            current_mode,
            *(value for value in alternatives if value != "sop"),
        ]))[:2]
        item.pop("handoff_key", None)
    if item.get("safety_level") == "normal":
        item["safety_level"] = "teacher_review_required"
    draft = item.get("draft")
    if not isinstance(draft, dict):
        raise VaultError(
            "class_teacher_triage_invalid_result",
            "AI 返回的冲突草稿无效",
            status_code=422,
        )
    previous_content = (
        previous_handoff.get("content")
        if isinstance(previous_handoff, Mapping)
        else None
    )
    if isinstance(previous_content, Mapping):
        draft = {**deepcopy(dict(previous_content)), **draft}
    baseline = compose_sensitive_draft(
        source_text=source_text,
        recommended_route="affair",
        resolved_date=None,
        model_payload=draft,
        model_questions=_profile_open_questions(item),
    )
    item["draft"] = {**draft, **baseline}
    _enrich_conflict_steps(item["draft"], source_text=source_text)
    if isinstance(previous_handoff, Mapping):
        item["draft"]["revision_of_draft_id"] = str(
            previous_handoff.get("draft_id") or ""
        )


def _enrich_conflict_steps(draft: dict[str, Any], *, source_text: str) -> None:
    steps = draft.get("steps")
    if not isinstance(steps, list):
        return
    additions: dict[str, list[str]] = {}
    if "座位" in source_text:
        additions.setdefault("fact_check", []).append(
            "核对信息课座位使用规则，并把双方一致事实与座位归属争议分开记录。"
        )
    if "分别陈述" in source_text or "分别" in source_text and "陈述" in source_text:
        additions.setdefault("separate_statements", []).append(
            "分别听取两名学生陈述，不安排当面对质。"
        )
    if "共同修复" in source_text:
        additions.setdefault("ordinary_support", []).append(
            "在规则核对和双方分别表达后，由教师选择适合当下状态的共同修复方式。"
        )
        additions.setdefault("follow_up", []).append(
            "后续分别确认修复是否有效，并记录两名学生各自仍需要的支持。"
        )
    for step in steps:
        if not isinstance(step, dict):
            continue
        extra = additions.get(str(step.get("key") or ""), [])
        if not extra:
            continue
        details = str(step.get("details") or "").strip()
        for sentence in extra:
            if sentence not in details:
                details = f"{details} {sentence}".strip()
        step["details"] = details


def _conflict_clarification_questions(
    *,
    source_text: str,
    existing: object,
    items: list[dict[str, Any]],
) -> list[str]:
    candidates: list[str] = []
    separated = re.search(
        r"(?:已经|已|目前已|先|让|把|将)[^。；]{0,10}分开|不再接触|没有继续接触|分开处理",
        source_text,
    )
    not_separated = re.search(r"(?:没|没有|未|尚未|还未)[^。；]{0,6}分开", source_text)
    if not separated or not_separated:
        candidates.append("双方目前是否已经分开，是否仍有即时冲突风险？")
    injury_status_known = re.search(
        r"无人受伤|没有人受伤|均未受伤|都没受伤|没有受伤|受伤|擦伤|擦破|破皮|流血|肿痛|校医|医务室|就医",
        source_text,
    )
    if not injury_status_known:
        candidates.append("是否有人受伤或需要立即联系校医、学校负责人？")
    if isinstance(existing, list):
        candidates.extend(
            str(question).strip()
            for question in existing
            if isinstance(question, str)
            and str(question).strip()
            and not re.search(r"(?:分开|即时冲突|受伤|校医)", str(question))
        )
    for item in items:
        candidates.extend(_profile_open_questions(item))
    if len(candidates) == 2:
        candidates.append("矛盾的起因、经过、在场人员和已经采取的处理措施分别是什么？")
    return list(dict.fromkeys(candidates))[:3]


def _bounded_clarification_questions(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    questions = [
        str(question).strip()
        for question in value
        if isinstance(question, str)
        and str(question).strip()
        and len(str(question).strip()) <= 400
    ]
    return list(dict.fromkeys(questions))[:3]


def _is_professional_report(source_text: str) -> bool:
    for match in _PROFESSIONAL_REPORT_PATTERN.finditer(source_text):
        window = source_text[max(0, match.start() - _PROFESSIONAL_NEGATION_WINDOW):match.start()]
        if not _PROFESSIONAL_NEGATION_PATTERN.search(window):
            return True
    return False


def _professional_clarification_questions(
    *,
    source_text: str,
    existing: object,
) -> list[str]:
    questions: list[str] = []
    has_source_and_basis = bool(
        _PROFESSIONAL_SOURCE_PATTERN.search(source_text)
        and _PROFESSIONAL_BASIS_PATTERN.search(source_text)
    )
    has_date = bool(_EXPLICIT_DATE_PATTERN.search(source_text))
    has_support = bool(_SCHOOL_SUPPORT_PATTERN.search(source_text))
    if not has_source_and_basis:
        questions.append("专业结论由哪家机构或哪位专业人员出具，是否有可核对的书面材料？")
    if not has_date:
        questions.append("这份专业结论的出具日期是什么时候？")
    if not has_support:
        questions.append("学生当前在校已采用哪些支持方式，哪些有效，哪些做法需要避免？")
    for question in _bounded_clarification_questions(existing):
        if has_source_and_basis and re.search(r"(?:来源|机构|医生|材料|报告|依据)", question):
            continue
        if has_date and re.search(r"(?:日期|时间|什么时候|何时)", question):
            continue
        if has_support and re.search(r"(?:在校|支持|有效|避免|做法)", question):
            continue
        questions.append(question)
    return list(dict.fromkeys(questions))[:3]


def _professional_date_from_text(source_text: str) -> str:
    explicit = re.search(
        r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日",
        source_text,
    )
    if explicit:
        try:
            return datetime(
                int(explicit.group("year")),
                int(explicit.group("month")),
                int(explicit.group("day")),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            ).date().isoformat()
        except ValueError:
            return ""
    iso_date = re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", source_text)
    if iso_date:
        parts = re.split(r"[-/.]", iso_date.group(0))
        try:
            return datetime(
                int(parts[0]), int(parts[1]), int(parts[2]),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            ).date().isoformat()
        except ValueError:
            return ""
    relative = re.search(r"今天|今日|昨天|昨日|前天", source_text)
    if relative:
        offset = 0 if relative.group(0) in {"今天", "今日"} else -1 if relative.group(0) in {"昨天", "昨日"} else -2
        return (datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=offset)).isoformat()
    return ""


def _school_support_from_text(source_text: str) -> str:
    match = re.search(
        r"((?:当前在校|在校|学校|课堂)(?:已)?采用[^。；]{1,240})",
        source_text,
    )
    return str(match.group(1)).strip() if match else ""


def _professional_profile_fallback(
    *,
    current_profile: Mapping[str, object],
    questions: list[str],
) -> dict[str, object]:
    current_summary = str(current_profile.get("summary") or "").strip()
    report_summary = "教师转述学生已有专业诊断信息，具体来源、日期和书面材料仍待核对。"
    summary = (
        f"{current_summary} {report_summary}".strip()
        if current_summary and report_summary not in current_summary
        else current_summary or report_summary
    )
    dimensions = deepcopy(list(current_profile.get("dimensions") or []))
    by_key = {
        str(item.get("key") or ""): index
        for index, item in enumerate(dimensions)
        if isinstance(item, Mapping)
    }
    context_dimension = {
        "key": "professional_support_context",
        "label": "专业支持信息（待核对）",
        "items": ["教师转述已有专业诊断信息；采用前需核对来源、日期和书面材料。"],
    }
    index = by_key.get("professional_support_context")
    if index is None:
        dimensions.append(context_dimension)
    else:
        dimensions[index] = context_dimension
    open_questions = list(dict.fromkeys([
        *[
            str(question).strip()
            for question in list(current_profile.get("open_questions") or [])
            if str(question).strip()
        ],
        *questions,
    ]))
    return {
        "summary": summary[:4000],
        "dimensions": dimensions,
        "open_questions": open_questions,
        "support_focus": deepcopy(list(current_profile.get("support_focus") or [])),
    }


def _validation_issue_codes(result: Mapping[str, Any]) -> list[str]:
    codes: list[str] = []
    work_items = result.get("work_items")
    for item in work_items if isinstance(work_items, list) else []:
        if not isinstance(item, Mapping):
            continue
        draft = item.get("draft")
        if not isinstance(draft, Mapping):
            continue
        raw_codes = draft.get("validation_issue_codes")
        for code in raw_codes if isinstance(raw_codes, list) else []:
            normalized = str(code)
            if (
                normalized in _STUDENT_REFERENCE_ISSUE_CODES
                and normalized not in codes
            ):
                codes.append(normalized)
    return codes


def _normalize_triage_payload_compatibility(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize only the two provider variants captured in diagnostics.

    Text time facts become bounded metadata objects. Provider shorthand becomes
    a review-only draft. Provider subject strings are never trusted or resolved
    automatically; the teacher must choose the student before adoption.
    """
    normalized = deepcopy(dict(payload))
    if normalized.get("contract_version") == "class_teacher_triage.v1":
        # Some JSON-object providers echo schema-description placeholders at
        # the top level. Diagnostics confirmed these fields carry no routing,
        # safety, student, or draft semantics; all contract fields below stay
        # strictly validated. profile_update/profile_base_revision only have
        # meaning inside a work item draft; models sometimes copy the prompt
        # example and echo them at the top level, where they are noise.
        for field in (
            "name",
            "description",
            "additionalProp1",
            "missing_fields",
            "profile_update",
            "profile_base_revision",
        ):
            normalized.pop(field, None)
        # JSON-object providers can echo unused schema branches under their
        # business-looking property names.  Empty branches contain no routing,
        # safety, student, or draft decision, so discard them generically while
        # retaining every non-empty unknown field for strict validation below.
        for field in tuple(set(normalized) - {
            "contract_version",
            "assistant_message",
            "clarification_questions",
            "work_items",
        }):
            if normalized.get(field) in (None, "", [], {}):
                normalized.pop(field, None)
    raw_work_items = normalized.get("work_items")
    if isinstance(raw_work_items, list):
        for raw_item in raw_work_items:
            if not isinstance(raw_item, dict):
                continue
            time_facts = raw_item.get("time_facts")
            if (
                isinstance(time_facts, list)
                and len(time_facts) <= 30
                and time_facts
                and all(
                    isinstance(item, str)
                    and bool(item.strip())
                    and len(item.strip()) <= 400
                    for item in time_facts
                )
            ):
                raw_item["time_facts"] = [
                    {"text": item.strip()} for item in time_facts
                ]
    extra_fields = set(normalized) - {
        "contract_version",
        "assistant_message",
        "clarification_questions",
        "work_items",
    }
    if extra_fields != {"domain", "category"}:
        return normalized
    if normalized.get("contract_version") != "class_teacher_triage.v1":
        return normalized
    domain = str(normalized.get("domain") or "").strip()
    mode = str(normalized.get("category") or "").strip()
    if domain not in {
        "student_growth",
        "student_support",
        "conflict_safety",
        "class_operations",
        "activities_culture",
        "school_coordination",
    } or mode not in {"record", "plan_calendar", "sop"}:
        return normalized
    raw_items = normalized.get("work_items")
    if not isinstance(raw_items, list) or not raw_items:
        return normalized

    compatible_items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items, start=1):
        if (
            not isinstance(raw_item, Mapping)
            or set(raw_item) - {"subject_refs", "content"}
        ):
            return normalized
        content = raw_item.get("content")
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content.strip()) > 2000
        ):
            return normalized
        missing_fields = (
            ["请选择学生"]
            if domain in {"student_growth", "student_support"}
            else []
        )
        compatible_items.append(
            {
                "work_item_id": f"compat_item_{index:03d}",
                "domain": domain,
                "primary_mode": mode,
                "secondary_modes": [],
                "intent": "create",
                "reason_summary": "模型返回了简化草稿，需由教师核对后处理",
                "subject_refs": [],
                "time_facts": [],
                "safety_level": "teacher_review_required",
                "missing_fields": missing_fields,
                "draft": {"summary": content.strip()},
            }
        )
    return {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": normalized.get("assistant_message"),
        "clarification_questions": normalized.get(
            "clarification_questions",
            [],
        ),
        "work_items": compatible_items,
    }


def _parse_model_payload(raw: object) -> dict[str, Any]:
    """Use the same conservative local JSON repair as call diagnostics."""

    if isinstance(raw, str):
        return parse_json_object_locally(raw).payload
    if not isinstance(raw, Mapping):
        raise TypeError("model result is not an object")
    return dict(raw)


def _explicitly_requested_mode(
    conversation: Mapping[str, object],
) -> str | None:
    turns = conversation.get("turns")
    if not isinstance(turns, list) or not turns:
        return None
    latest = turns[-1]
    if not isinstance(latest, Mapping):
        return None
    message = str(latest.get("teacher_message") or "").upper()
    compact = re.sub(r"\s+", "", message)
    if re.search(r"不(?:要|用|按|走|进入).{0,4}SOP", compact):
        return None
    if re.search(
        r"(?:(?:按|走|进入|使用|用|转为|转成).{0,6}SOP|SOP.{0,6}(?:方式|流程|处理|页面))",
        compact,
    ):
        return "sop"
    return None


def _ref_mapping(value: OpaqueRef) -> dict[str, str]:
    return {"kind": value.kind, "id": value.id, "revision": value.revision}


def _mapping_ref(value: Mapping[str, object]) -> OpaqueRef:
    return OpaqueRef(
        kind=str(value.get("kind") or ""),
        id=task_safe_ref_id(str(value.get("id") or "")),
        revision=str(value.get("revision") or ""),
    )


def _adoption_result(receipt: Mapping[str, object]) -> AdoptionResult:
    adoption_id = str(receipt.get("adoption_id") or "")
    object_type = str(receipt.get("formal_object_type") or "object")
    object_id = str(receipt.get("formal_object_id") or "")
    return AdoptionResult(
        adoption_id=adoption_id,
        object_ref=f"class_teacher:{object_type}:{object_id}",
        receipt_revision=str(receipt.get("draft_revision") or "1"),
        target_revision=str(receipt.get("target_revision") or ""),
    )


__all__ = ["ClassTeacherAITaskAdapter", "DomainModelRequest"]
