<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'
import StatusBadge from '@/components/design-system/StatusBadge.vue'

import { affairR1Api, type AffairDetail, type AffairStep } from '../api/r1'

const props = defineProps<{
  affair: AffairDetail
  step: AffairStep
}>()
const emit = defineEmits<{
  changed: [affair: AffairDetail]
  reload: []
  close: []
}>()

const draftText = ref('')
const draftKind = ref<'fact' | 'communication'>('fact')
const decisionSummary = ref('')
const selectedOption = ref('')
const revisionChecks = ref<Record<string, boolean>>({})
const busy = ref(false)
const message = ref('')
const error = ref('')

const isCurrent = computed(() => props.affair.current_steps.some((item) => item.step_instance_id === props.step.step_instance_id))
const canComplete = computed(() => isCurrent.value && ['ready', 'in_progress', 'waiting'].includes(props.step.state))
const isDecision = computed(() => Boolean(props.step.decision_key))
const recordedDecision = computed(() => (props.affair.decisions ?? []).find((item) => item.decision_key && item.decision_key === props.step.decision_key) ?? null)
const pendingRevision = computed(() => {
  const list = (props.affair.flow_revisions ?? []).filter((item) => item.state === 'pending_review')
  return list.length ? list[list.length - 1]! : null
})

const stateLabel: Record<string, string> = {
  blocked: '未解锁', ready: '待处理', in_progress: '进行中', waiting: '待处理',
  completed: '已完成 ✓', waived: '已免除', superseded: '不再执行',
}
const stateTone = (state: string): 'success' | 'warning' | 'neutral' | 'info' => {
  if (state === 'completed' || state === 'waived') return 'success'
  if (state === 'in_progress') return 'info'
  if (state === 'blocked') return 'neutral'
  if (state === 'superseded') return 'neutral'
  return 'warning'
}

function kindLabel(step: AffairStep): string {
  if (step.safety_required) return '安全必做'
  if (step.decision_key) return '判断分支'
  if (step.activation) return '分支步骤'
  if (step.communication_templates?.length) return '沟通'
  return '步骤'
}

watch(() => props.step.step_instance_id, () => {
  error.value = ''; message.value = ''
  decisionSummary.value = ''; selectedOption.value = ''
  const prior = props.affair.drafts?.find((item) => item.step_instance_id === props.step.step_instance_id)
  draftText.value = prior?.text ?? ''
}, { immediate: true })

watch(pendingRevision, (revision) => {
  revisionChecks.value = Object.fromEntries((revision?.items ?? []).map((item) => [item.item_id, true]))
}, { immediate: true })

async function saveDraft(): Promise<void> {
  if (!draftText.value.trim() || busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const prior = props.affair.drafts?.find((item) => item.step_instance_id === props.step.step_instance_id && item.draft_kind === draftKind.value)
    await affairR1Api.saveDraft(props.affair.affair_id, props.step.step_instance_id, draftKind.value, draftText.value, prior?.revision ?? null)
    message.value = '处理记录已保存。'
    emit('reload')
  } catch {
    error.value = '处理记录没有保存；请重试。'
  } finally { busy.value = false }
}

async function complete(): Promise<void> {
  if (!canComplete.value || busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const updated = await affairR1Api.command(props.affair, 'complete_step', {
      step_instance_id: props.step.step_instance_id,
      outcome: 'completed',
      result: draftText.value.trim() || '教师确认该步骤已完成',
    })
    emit('changed', updated)
  } catch {
    error.value = '完成状态没有保存；可能已有其他改动，请刷新后重试。'
    emit('reload')
  } finally { busy.value = false }
}

async function decide(): Promise<void> {
  if (!isDecision.value || busy.value || !decisionSummary.value.trim()) return
  if (props.step.decision_options?.length && !selectedOption.value) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const updated = await affairR1Api.command(props.affair, 'teacher_decision', {
      decision_kind: 'teacher',
      summary: decisionSummary.value,
      step_instance_id: props.step.step_instance_id,
      decision_key: props.step.decision_key,
      selected_option: selectedOption.value || null,
    })
    decisionSummary.value = ''
    emit('changed', updated)
  } catch {
    error.value = '教师决定没有保存；请刷新后重试。'
    emit('reload')
  } finally { busy.value = false }
}

async function decideRevision(accept: boolean): Promise<void> {
  const revision = pendingRevision.value
  if (!revision || busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const accepted = accept
      ? revision.items.filter((item) => revisionChecks.value[item.item_id] !== false).map((item) => item.item_id)
      : []
    const updated = await affairR1Api.decideFlowRevision(props.affair, revision.revision_id, accepted)
    message.value = accept ? '已接受所选调整。' : '已拒绝本次全部修订建议；流程保持原样。'
    emit('changed', updated)
  } catch {
    error.value = '决定没有保存；请刷新后重试。'
  } finally { busy.value = false }
}
</script>

<template>
  <aside class="inspector" aria-label="步骤检查器">
    <header class="inspector__bar">
      <h3>步骤检查器</h3>
      <button type="button" class="inspector__close" aria-label="关闭" @click="emit('close')">×</button>
    </header>
    <div class="inspector__tags">
      <span class="kind" :data-kind="step.safety_required ? 'safety' : step.decision_key ? 'branch' : 'plain'">{{ kindLabel(step) }}</span>
      <StatusBadge :tone="stateTone(step.state)" :label="stateLabel[step.state] ?? step.state" />
    </div>
    <h4 class="inspector__title">{{ step.title }}</h4>
    <p v-if="step.details" class="inspector__details">{{ step.details }}</p>
    <p v-if="step.result" class="inspector__result">完成记录：{{ step.result }}</p>
    <p v-if="step.state === 'blocked'" class="muted">前置步骤未完成，暂不能处理。</p>
    <p v-else-if="step.state === 'superseded'" class="muted">该步骤不再执行。</p>
    <p v-if="message" class="status" role="status">{{ message }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <template v-if="isDecision">
      <section v-if="recordedDecision" class="decision-done">
        <h5>教师决定</h5>
        <p>
          <template v-if="recordedDecision.selected_option">
            已选：{{ step.decision_options?.find((option) => option.value === recordedDecision?.selected_option)?.label ?? recordedDecision.selected_option }} —
          </template>
          {{ recordedDecision.summary }}
        </p>
      </section>
      <section v-else class="decision-box">
        <h5>{{ step.decision_prompt || '请记录教师已经作出的决定。' }}</h5>
        <div v-if="step.decision_options?.length" class="branch-options">
          <AppButton
            v-for="option in step.decision_options"
            :key="option.value"
            :variant="selectedOption === option.value ? 'primary' : 'secondary'"
            :disabled="!isCurrent"
            @click="selectedOption = option.value"
          >
            {{ option.label }}
          </AppButton>
        </div>
        <textarea v-model="decisionSummary" rows="3" maxlength="8000" placeholder="记录教师或学校已经作出的决定；AI 不能替你做这个决定。"></textarea>
        <AppButton variant="primary" :disabled="busy || !isCurrent || !decisionSummary.trim() || (!!step.decision_options?.length && !selectedOption)" @click="decide">保存教师决定</AppButton>
      </section>
    </template>

    <template v-if="step.state !== 'superseded' && affair.state === 'active'">
      <label class="record-input">
        <span>处理记录（{{ draftKind === 'fact' ? '事实草稿' : '沟通草稿' }}）</span>
        <select v-model="draftKind">
          <option value="fact">事实草稿</option>
          <option value="communication">沟通草稿</option>
        </select>
        <textarea v-model="draftText" rows="4" maxlength="8000" placeholder="记录这一步实际做了什么、学生怎么回应；草稿只保存在班主任工作台。"></textarea>
      </label>
      <div class="inspector__actions">
        <AppButton variant="secondary" :disabled="busy || !draftText.trim()" @click="saveDraft">保存记录</AppButton>
        <AppButton v-if="!isDecision" variant="primary" :disabled="busy || !canComplete" @click="complete">标记完成</AppButton>
      </div>
    </template>

    <section v-if="pendingRevision" class="revision-box">
      <h5>AI 流程修订建议</h5>
      <p class="muted">基于你同步的新情况：“{{ pendingRevision.source_text }}”</p>
      <p class="assistant">{{ pendingRevision.assistant_message }}</p>
      <label v-for="item in pendingRevision.items" :key="item.item_id" class="revision-item">
        <input v-model="revisionChecks[item.item_id]" type="checkbox">
        <span>
          <em>{{ item.kind === 'add_step' ? '新增步骤' : item.kind === 'revise_step' ? '修改步骤' : '核对建议' }}</em>
          <strong>{{ item.kind === 'note' ? item.text : item.title }}</strong>
          <small v-if="item.kind !== 'note' && item.details">{{ item.details }}</small>
          <small v-if="item.kind === 'add_step' && item.depends_on?.length">前置：{{ item.depends_on.join('、') }}</small>
          <small v-if="item.reason">理由：{{ item.reason }}</small>
        </span>
      </label>
      <div v-if="pendingRevision.dropped_items?.length" class="dropped">
        <strong>系统未采纳的建议</strong>
        <ul><li v-for="dropped in pendingRevision.dropped_items" :key="dropped.item_id">{{ dropped.reason }}</li></ul>
      </div>
      <div class="inspector__actions">
        <AppButton variant="primary" :disabled="busy" @click="decideRevision(true)">接受所选调整</AppButton>
        <AppButton variant="ghost" :disabled="busy" @click="decideRevision(false)">全部拒绝</AppButton>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.inspector{display:grid;gap:12px;justify-items:start;padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.inspector__bar{display:flex;width:100%;justify-content:space-between;align-items:center}
.inspector__bar h3{margin:0;font-size:15px}
.inspector__close{width:26px;height:26px;border:1px solid var(--border);border-radius:50%;background:var(--muted);font-size:14px;line-height:1;cursor:pointer;color:var(--color-text-secondary)}
.inspector__tags{display:flex;gap:8px;align-items:center}
.inspector__title{margin:0;font-size:16px}
.inspector__details,.inspector__result{margin:0;font-size:13px;color:var(--color-text-secondary)}
.kind{padding:1px 8px;border-radius:999px;font-size:11px;background:var(--muted);color:var(--color-text-secondary)}
.kind[data-kind="safety"]{background:var(--color-danger-subtle);color:var(--destructive)}
.kind[data-kind="branch"]{background:var(--color-warning-subtle);color:var(--color-warning)}
.muted{margin:0;font-size:12px;color:var(--muted-foreground)}
.status{margin:0;color:var(--color-info);font-size:12px}
.error{margin:0;color:var(--destructive);font-size:12px}
.decision-box,.decision-done{display:grid;gap:8px;width:100%;padding:12px;border:1px solid var(--color-warning);border-radius:var(--radius);background:var(--color-warning-subtle)}
.decision-box h5,.decision-done h5{margin:0;font-size:13px}
.decision-done p{margin:0;font-size:13px}
.branch-options{display:grid;gap:8px}
.decision-box textarea{padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;font-size:13px;resize:vertical}
.record-input{display:grid;gap:6px;width:100%;font-size:13px;font-weight:600}
.record-input select{padding:6px 8px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;font-size:13px}
.record-input textarea{padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;line-height:1.5;resize:vertical}
.record-input textarea:focus-visible,.decision-box textarea:focus-visible{border-color:var(--ring);box-shadow:var(--focus-ring);outline:0}
.inspector__actions{display:flex;gap:8px;flex-wrap:wrap}
.revision-box{display:grid;gap:8px;width:100%;padding:12px;border:1px solid var(--color-ai);border-radius:var(--radius);background:var(--muted)}
.revision-box h5{margin:0;font-size:13px}
.revision-box .assistant{margin:0;font-size:13px}
.revision-item{display:flex;align-items:flex-start;gap:8px;padding:10px;border:1px solid var(--color-border-subtle);border-radius:var(--radius);background:var(--card);font-size:13px}
.revision-item input{margin-top:3px}
.revision-item span{display:grid;gap:3px}
.revision-item em{color:var(--color-ai);font-size:11px;font-style:normal;font-weight:700}
.revision-item small{color:var(--color-text-secondary)}
.dropped{padding:8px;border:1px dashed var(--border);border-radius:var(--radius);font-size:12px}
.dropped ul{margin:4px 0 0;padding-left:18px}
</style>
