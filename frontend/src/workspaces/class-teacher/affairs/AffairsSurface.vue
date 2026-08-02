<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { affairR1Api, type AffairDetail, type AffairStep, type AffairSummary } from '../api/r1'

const props = defineProps<{ token: string; targetId?: string | null }>()
const items = ref<AffairSummary[]>([])
const selected = ref<AffairDetail | null>(null)
const draftText = ref('')
const draftKind = ref<'fact' | 'communication'>('fact')
const busy = ref(false)
const error = ref('')
const decisionSummary = ref('')
const selectedDecisionOption = ref('')
const closureSummary = ref('')
const reopenReason = ref('')
const currentSteps = computed(() => selected.value?.current_steps ?? [])
const decisionStep = computed(() => currentSteps.value.find((step) => step.decision_key || step.decision_prompt) ?? null)
const baselines = ['学生安全与紧急处置','欺凌线索核实','家校沟通','纪律与教育支持','缺勤与返校','阶段性关怀']

async function load() {
  busy.value = true; error.value = ''
  try {
    const selectedId = selected.value?.affair_id || props.targetId
    items.value = await affairR1Api.list(props.token)
    const target = items.value.find((item) => item.affair_id === selectedId)
    if (target) await open(target)
  } catch { error.value = '事务列表暂时无法读取。' } finally { busy.value = false }
}
async function open(item: AffairSummary) {
  selected.value = await affairR1Api.read(props.token, item.affair_id)
}
async function saveDraft(step: AffairStep) {
  if (!selected.value || !draftText.value.trim()) return
  const prior = selected.value.drafts?.find((item) => item.step_instance_id === step.step_instance_id && item.draft_kind === draftKind.value)
  await affairR1Api.saveDraft(props.token, selected.value.affair_id, step.step_instance_id, draftKind.value, draftText.value, prior?.revision ?? null)
  draftText.value = ''; selected.value = await affairR1Api.read(props.token, selected.value.affair_id)
}
async function complete(step: AffairStep) {
  if (!selected.value) return
  selected.value = await affairR1Api.command(props.token, selected.value, 'complete_step', {
    step_instance_id: step.step_instance_id, outcome: 'completed', result: '教师确认该步骤已完成',
  })
  await load()
}
async function command(name: 'teacher_decision' | 'close' | 'reopen') {
  if (!selected.value) return
  const input = name === 'teacher_decision'
    ? {
        decision_kind:'teacher',
        summary:decisionSummary.value,
        step_instance_id:decisionStep.value?.step_instance_id,
        decision_key:decisionStep.value?.decision_key,
        selected_option:selectedDecisionOption.value || null,
      }
    : name === 'close' ? { summary:closureSummary.value } : { reason:reopenReason.value }
  selected.value = await affairR1Api.command(props.token, selected.value, name, input)
  decisionSummary.value=''; selectedDecisionOption.value=''; closureSummary.value=''; reopenReason.value=''; await load()
}
onMounted(() => { void load() })
</script>

<template>
  <section class="affairs">
    <header><div><p>受保护的连续事务</p><h2>每件事只沿一条流程推进</h2></div><button type="button" @click="load">刷新</button></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="layout">
      <nav aria-label="事务列表">
        <button v-for="item in items" :key="item.affair_id" type="button" :class="{ active: selected?.affair_id === item.affair_id }" @click="open(item)">
          <i aria-hidden="true"></i><span><strong>{{ item.title }}</strong><small>{{ item.current_step_count }} 个当前步骤 · {{ item.completed_step_count }} 个已完成</small></span><em>{{ item.projection_state === 'applied' ? '已同步' : '待同步' }}</em>
        </button>
        <div v-if="!items.length && !busy" class="baselines"><strong>当前没有事务</strong><p>学校流程基线仍可查看：</p><span v-for="baseline in baselines" :key="baseline">{{ baseline }}</span></div>
      </nav>
      <article v-if="selected" class="detail">
        <p class="eyebrow">{{ selected.template_key }} · 第 {{ selected.occurrence_sequence }} 轮</p>
        <h3>{{ selected.title }}</h3><p class="muted">{{ selected.summary }}</p>
        <div class="rail" aria-label="SOP 步骤轨迹">
          <section v-for="step in [...selected.completed_steps, ...currentSteps, ...selected.preview_steps]" :key="step.step_instance_id" :data-state="step.state">
            <span aria-hidden="true"></span><div><strong>{{ step.title }}</strong><small>{{ step.state }}<template v-if="step.safety_required"> · 安全必做</template></small><p v-if="step.details">{{ step.details }}</p></div>
            <button v-if="currentSteps.some((item) => item.step_instance_id === step.step_instance_id)" type="button" @click="complete(step)">确认完成</button>
          </section>
        </div>
        <section v-if="currentSteps.length" class="draft-box">
          <h4>当前步骤草稿</h4><select v-model="draftKind"><option value="fact">事实草稿</option><option value="communication">沟通草稿</option></select>
          <textarea v-model="draftText" rows="4" maxlength="8000" placeholder="草稿只保存在加密保险箱，7 天后自动过期。"></textarea>
          <button type="button" :disabled="!draftText.trim() || !currentSteps[0]" @click="currentSteps[0] && saveDraft(currentSteps[0])">保存到当前步骤</button>
        </section>
        <section v-if="decisionStep" class="decision-box"><h4>教师决定 · {{ decisionStep.title }}</h4><p>{{ decisionStep.decision_prompt || '请记录教师已经作出的决定。' }}</p><select v-if="decisionStep.decision_options?.length" v-model="selectedDecisionOption"><option value="">请选择</option><option v-for="option in decisionStep.decision_options" :key="option.value" :value="option.value">{{ option.label }}</option></select><textarea v-model="decisionSummary" rows="3" maxlength="8000" placeholder="记录教师或学校已经作出的决定；AI 建议不能驱动高影响分支。"></textarea><button type="button" :disabled="!decisionSummary.trim() || (!!decisionStep.decision_options?.length && !selectedDecisionOption)" @click="command('teacher_decision')">保存教师决定</button></section>
      </article>
      <div v-else class="empty"><span>↗</span><strong>选择一件事务</strong><p>查看当前步骤、依赖关系与教师草稿。</p></div>
      <aside v-if="selected" class="next-panel">
        <p class="eyebrow">后续与安全</p><h3>流程边界</h3>
        <section><strong>后续步骤</strong><p v-if="!selected.preview_steps?.length">当前没有被依赖阻塞的后续步骤。</p><ul><li v-for="step in selected.preview_steps" :key="step.step_instance_id">{{ step.title }} · 等待依赖</li></ul></section>
        <section><strong>并行分支</strong><p>{{ currentSteps.length > 1 ? `当前有 ${currentSteps.length} 个步骤可并行推进。` : '当前没有并行分支。' }}</p></section>
        <section v-if="selected.school_config_gaps?.length" class="warning"><strong>学校配置缺口</strong><ul><li v-for="gap in selected.school_config_gaps" :key="gap">{{ gap }}</li></ul></section>
        <section v-if="selected.emergency_prompt" class="warning"><strong>安全提示</strong><p>{{ selected.emergency_prompt }}</p></section>
        <section v-if="selected.state!=='closed'"><strong>结案</strong><p v-if="currentSteps.length || selected.preview_steps?.length">必做步骤未完成，暂不能结案。</p><textarea v-model="closureSummary" rows="3" placeholder="结案摘要"></textarea><button type="button" :disabled="!closureSummary.trim() || currentSteps.length>0 || selected.preview_steps?.length>0" @click="command('close')">确认结案</button></section>
        <section v-else><strong>重开新一轮</strong><p>重开会创建新的 occurrence，不覆盖上一轮。</p><textarea v-model="reopenReason" rows="3" placeholder="重开理由（必填）"></textarea><button type="button" :disabled="!reopenReason.trim()" @click="command('reopen')">填写理由并重开</button></section>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.affairs{overflow:hidden;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--color-border-subtle)}header p,.eyebrow{margin:0 0 2px;color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}h2{margin:0;font-size:var(--font-size-h2)}button,select,textarea{font:inherit}header button,.rail button,.draft-box button,.decision-box button,.next-panel button{min-height:36px;padding:0 var(--space-3);border:1px solid var(--color-border-strong);border-radius:var(--radius-control);background:var(--color-bg-surface)}.layout{display:grid;grid-template-columns:240px minmax(0,1fr) 300px;min-height:560px}.layout>nav{padding:var(--space-3);border-right:1px solid var(--color-border-default);background:var(--color-bg-subtle)}nav>button{display:grid;grid-template-columns:4px 1fr;gap:var(--space-2);width:100%;padding:var(--space-3);border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left}nav>button.active{background:var(--color-bg-surface)}nav i{background:var(--color-accent);border-radius:3px}nav span{display:grid;gap:3px}nav small{color:var(--color-text-secondary)}nav em{grid-column:2;color:var(--color-text-muted);font-size:var(--font-size-caption);font-style:normal}.baselines{display:grid;gap:var(--space-1);padding:var(--space-3)}.baselines span{padding:var(--space-1);border-bottom:1px solid var(--color-border-subtle);font-size:var(--font-size-dense)}.detail{padding:var(--space-5)}.detail h3{margin:0;font-size:var(--font-size-h2)}.muted{color:var(--color-text-secondary)}.rail{margin-top:var(--space-5);border-left:2px solid var(--color-accent-subtle)}.rail section{display:grid;grid-template-columns:14px minmax(0,1fr) auto;gap:var(--space-3);margin-left:-8px;padding:0 0 var(--space-5)}.rail section>span{width:14px;height:14px;border:3px solid var(--color-bg-surface);border-radius:50%;background:var(--color-accent)}.rail section[data-state="blocked"]>span{background:var(--color-text-muted)}.rail div{display:grid;gap:3px}.rail small{color:var(--color-text-muted)}.rail p{margin:var(--space-1) 0 0;color:var(--color-text-secondary)}.draft-box,.decision-box{display:grid;grid-template-columns:180px 1fr;gap:var(--space-2);padding:var(--space-4);border-top:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.draft-box h4,.decision-box h4{grid-column:1/-1;margin:0}.draft-box textarea,.decision-box textarea{grid-column:1/-1;padding:var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-control)}.draft-box button,.decision-box button{grid-column:2;justify-self:end}.next-panel{padding:var(--space-4);border-left:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.next-panel>section{margin-top:var(--space-4);padding-top:var(--space-3);border-top:1px solid var(--color-border-subtle)}.next-panel p,.next-panel li{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.next-panel textarea{width:100%;box-sizing:border-box;margin-top:var(--space-2);padding:var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-control)}.warning{border-left:3px solid var(--color-warning);padding-left:var(--space-3)!important}.empty{display:grid;place-items:center;align-content:center;text-align:center;color:var(--color-text-secondary)}.empty span{font-size:36px;color:var(--color-accent)}.error{padding:var(--space-3);color:var(--color-danger)}@media(max-width:1050px){.layout{grid-template-columns:220px 1fr}.next-panel{grid-column:1/-1;border-top:1px solid var(--color-border-default);border-left:0}}@media(max-width:750px){.layout{grid-template-columns:1fr}.layout>nav{border-right:0;border-bottom:1px solid var(--color-border-default)}}
</style>
