<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import {
  sopApi,
  type Affair,
  type AffairStep,
  type SopTemplate,
} from '../api/sop'

const props = defineProps<{ sessionToken: string }>()
const emit = defineEmits<{
  activity: []
  confirmed: []
  error: [message: string]
  locked: []
}>()

const loading = ref(true)
const saving = ref(false)
const templates = ref<SopTemplate[]>([])
const affairs = ref<Affair[]>([])
const selectedAffairId = ref('')
const selectedTemplateId = ref('')
const affairTitle = ref('')
const affairSummary = ref('')
const participantRefs = ref('')
const stepResults = ref<Record<string, string>>({})
const decisionValues = ref<Record<string, string>>({})
const closureSummary = ref('')
const reopenReason = ref('')
const localMessage = ref('')

const selectedAffair = computed(() => (
  affairs.value.find((item) => item.affair_id === selectedAffairId.value) ?? affairs.value[0] ?? null
))

const baselineTemplates = computed(() => templates.value.filter((item) => (
  item.template_key.startsWith('baseline.')
)))

const pendingDecisionSteps = computed(() => {
  const affair = selectedAffair.value
  if (!affair) return []
  return affair.completed_steps.filter((step) => (
    step.decision_key
    && step.decision_options.length
    && !affair.decisions.some((decision) => (
      decision.can_drive_high_impact_branch
      && decision.decision_key === step.decision_key
    ))
  ))
})

const participants = computed(() => participantRefs.value
  .split(/\r?\n/)
  .map((value) => value.trim())
  .filter(Boolean))

function report(error: unknown): void {
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit('locked')
    return
  }
  emit(
    'error',
    error instanceof ApiError
      ? error.message
      : '事务工作清单没有完成本次操作，现有步骤没有改变。',
  )
}

async function refresh(): Promise<void> {
  try {
    const [loadedTemplates, loadedAffairs] = await Promise.all([
      sopApi.listTemplates(props.sessionToken),
      sopApi.listAffairs(props.sessionToken),
    ])
    templates.value = loadedTemplates
    affairs.value = loadedAffairs
    if (!selectedTemplateId.value && baselineTemplates.value[0]) {
      selectedTemplateId.value = baselineTemplates.value[0].template_version_id
    }
    if (
      selectedAffairId.value
      && !loadedAffairs.some((item) => item.affair_id === selectedAffairId.value)
    ) selectedAffairId.value = ''
    if (!selectedAffairId.value && loadedAffairs[0]) selectedAffairId.value = loadedAffairs[0].affair_id
  } catch (error) {
    report(error)
  } finally {
    loading.value = false
  }
}

async function ensureBaselines(): Promise<void> {
  saving.value = true
  try {
    templates.value = await sopApi.ensureBaselines(props.sessionToken)
    selectedTemplateId.value = templates.value[0]?.template_version_id ?? ''
    localMessage.value = '六套版本化个人工作清单已就绪；它们不是学校正式流程。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function createAffair(): Promise<void> {
  if (!selectedTemplateId.value || !affairTitle.value.trim() || !participants.value.length) return
  saving.value = true
  try {
    const created = await sopApi.createAffair(props.sessionToken, {
      template_version_id: selectedTemplateId.value,
      title: affairTitle.value,
      summary: affairSummary.value.trim() || null,
      participant_refs: participants.value,
    })
    affairs.value = [created, ...affairs.value]
    selectedAffairId.value = created.affair_id
    affairTitle.value = ''
    affairSummary.value = ''
    participantRefs.value = ''
    localMessage.value = '个人工作清单已建立，当前步骤已进入同一行动账本。'
    emit('activity')
    emit('confirmed')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

function replaceAffair(updated: Affair): void {
  affairs.value = affairs.value.map((item) => (
    item.affair_id === updated.affair_id ? updated : item
  ))
}

async function completeStep(step: AffairStep, outcome: 'completed' | 'waived' = 'completed'): Promise<void> {
  const affair = selectedAffair.value
  const result = stepResults.value[step.step_instance_id]?.trim()
  if (!affair || !result) return
  saving.value = true
  try {
    const updated = await sopApi.completeStep(
      props.sessionToken,
      affair,
      step,
      result,
      outcome,
    )
    replaceAffair(updated)
    delete stepResults.value[step.step_instance_id]
    emit('activity')
    emit('confirmed')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function recordDecision(step: AffairStep): Promise<void> {
  const affair = selectedAffair.value
  const selected = decisionValues.value[step.step_instance_id]
  if (!affair || !selected) return
  saving.value = true
  try {
    const updated = await sopApi.recordDecision(
      props.sessionToken,
      affair,
      step,
      selected,
    )
    replaceAffair(updated)
    delete decisionValues.value[step.step_instance_id]
    localMessage.value = '教师分流决定已记录，后续步骤已按冻结模板更新。'
    emit('activity')
    emit('confirmed')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function closeAffair(): Promise<void> {
  const affair = selectedAffair.value
  if (!affair || !closureSummary.value.trim()) return
  saving.value = true
  try {
    const updated = await sopApi.closeAffair(
      props.sessionToken,
      affair,
      closureSummary.value,
    )
    replaceAffair(updated)
    closureSummary.value = ''
    localMessage.value = '事务已由教师确认结案。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function reopenAffair(): Promise<void> {
  const affair = selectedAffair.value
  if (!affair || !reopenReason.value.trim()) return
  saving.value = true
  try {
    const updated = await sopApi.reopenAffair(
      props.sessionToken,
      affair,
      reopenReason.value,
    )
    replaceAffair(updated)
    reopenReason.value = ''
    localMessage.value = `事务已重开为第 ${updated.occurrence_sequence} 轮。`
    emit('activity')
    emit('confirmed')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

function stepLabel(state: AffairStep['state']): string {
  return {
    blocked: '后续预览',
    ready: '当前需处理',
    in_progress: '处理中',
    waiting: '等待中',
    completed: '已完成',
    waived: '已豁免',
    superseded: '未命中分支',
  }[state]
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <section class="sop-workspace">
    <header class="sop-workspace__heading">
      <div>
        <p class="section-kicker">B06 · 版本化事务工作清单</p>
        <h2>高风险先人工处置，步骤随后跟上</h2>
        <p>系统只整理个人工作步骤；不认定欺凌、不诊断、不决定惩戒，也不自动外发或结案。</p>
      </div>
      <div class="scope-stamp">
        <strong>个人工作清单</strong>
        <span>学校流程待配置</span>
      </div>
    </header>

    <p v-if="localMessage" class="local-message" role="status">{{ localMessage }}</p>
    <p v-if="loading" class="empty-state">正在读取加密事务清单…</p>

    <template v-else>
      <div v-if="!baselineTemplates.length" class="baseline-gate">
        <div>
          <h3>载入六套内置基线</h3>
          <p>模板会以版本 1 冻结保存；未配置学校联系人、时限和审批链时，只能作为个人工作清单。</p>
        </div>
        <button class="button button--primary" type="button" :disabled="saving" @click="ensureBaselines">
          载入个人工作清单
        </button>
      </div>

      <div v-else class="sop-layout">
        <aside class="affair-rail">
          <form class="affair-create" @submit.prevent="createAffair">
            <h3>新建事务清单</h3>
            <label>
              <span>清单类型</span>
              <select v-model="selectedTemplateId">
                <option v-for="item in baselineTemplates" :key="item.template_version_id" :value="item.template_version_id">
                  {{ item.title }}
                </option>
              </select>
            </label>
            <label>
              <span>事务名称</span>
              <input v-model="affairTitle" maxlength="240">
            </label>
            <label>
              <span>匿名参与引用，每行一个</span>
              <textarea v-model="participantRefs" rows="3" />
            </label>
            <label>
              <span>简要说明（可选）</span>
              <textarea v-model="affairSummary" rows="2" maxlength="8000" />
            </label>
            <button
              class="button button--primary"
              type="submit"
              :disabled="saving || !selectedTemplateId || !affairTitle.trim() || !participants.length"
            >
              建立个人工作清单
            </button>
          </form>

          <nav v-if="affairs.length" aria-label="事务列表">
            <button
              v-for="item in affairs"
              :key="item.affair_id"
              type="button"
              :aria-current="selectedAffair?.affair_id === item.affair_id ? 'page' : undefined"
              @click="selectedAffairId = item.affair_id"
            >
              <strong>{{ item.title }}</strong>
              <span>{{ item.state === 'active' ? '处理中' : '已结案' }} · 第 {{ item.occurrence_sequence }} 轮</span>
            </button>
          </nav>
        </aside>

        <main v-if="selectedAffair" class="affair-main">
          <div
            v-if="selectedAffair.emergency_prompt"
            class="emergency-card"
            :data-risk="selectedAffair.risk_level"
            role="alert"
          >
            <span>人工处置优先</span>
            <strong>{{ selectedAffair.emergency_prompt }}</strong>
            <small>无需先填完整表单；外部模型请求为 {{ selectedAffair.physical_request_count }}。</small>
          </div>

          <header class="affair-header">
            <div>
              <p class="section-kicker">{{ selectedAffair.template_key }} · v{{ selectedAffair.template_version }}</p>
              <h3>{{ selectedAffair.title }}</h3>
              <p>{{ selectedAffair.summary || '没有补充说明。' }}</p>
            </div>
            <span :data-state="selectedAffair.state">
              {{ selectedAffair.state === 'active' ? '处理中' : '已结案' }}
            </span>
          </header>

          <details class="school-gaps">
            <summary>为什么这里只是个人工作清单</summary>
            <ul>
              <li v-for="item in selectedAffair.school_config_gaps" :key="item">{{ item }}</li>
            </ul>
          </details>

          <section v-if="selectedAffair.current_steps.length" class="current-steps">
            <h4>当前需处理</h4>
            <article
              v-for="step in selectedAffair.current_steps"
              :key="step.step_instance_id"
              class="step-card"
              :data-safety="step.safety_required"
            >
              <header>
                <div>
                  <span>{{ stepLabel(step.state) }}</span>
                  <h5>{{ step.title }}</h5>
                </div>
                <strong v-if="step.safety_required">安全必做</strong>
              </header>
              <p>{{ step.details }}</p>
              <div
                v-for="draft in step.communication_templates"
                :key="draft.kind"
                class="step-draft"
              >
                <div><span>{{ draft.audience }}</span><strong>尚未发送</strong></div>
                <p>{{ draft.content }}</p>
                <small>依据：{{ draft.basis }}；不确定项：{{ draft.unknowns.join('；') || '无' }}</small>
              </div>
              <label>
                <span>人工处理结果或完成证据</span>
                <textarea v-model="stepResults[step.step_instance_id]" rows="3" maxlength="8000" />
              </label>
              <div class="step-card__actions">
                <button
                  class="button button--primary"
                  type="button"
                  :disabled="saving || !stepResults[step.step_instance_id]?.trim()"
                  @click="completeStep(step)"
                >
                  记录结果并完成步骤
                </button>
                <button
                  v-if="step.waivable && !step.safety_required"
                  class="button button--secondary"
                  type="button"
                  :disabled="saving || !stepResults[step.step_instance_id]?.trim()"
                  @click="completeStep(step, 'waived')"
                >
                  记录理由并豁免
                </button>
              </div>
            </article>
          </section>

          <section v-if="pendingDecisionSteps.length" class="decision-desk">
            <h4>等待教师分流决定</h4>
            <article v-for="step in pendingDecisionSteps" :key="step.step_instance_id">
              <strong>{{ step.decision_prompt }}</strong>
              <label v-for="option in step.decision_options" :key="option.value">
                <input
                  v-model="decisionValues[step.step_instance_id]"
                  type="radio"
                  :name="`decision-${step.step_instance_id}`"
                  :value="option.value"
                >
                <span>{{ option.label }}</span>
              </label>
              <button
                class="button button--primary"
                type="button"
                :disabled="saving || !decisionValues[step.step_instance_id]"
                @click="recordDecision(step)"
              >
                记录教师决定并更新后续步骤
              </button>
            </article>
          </section>

          <details v-if="selectedAffair.completed_steps.length" class="step-history">
            <summary>已完成与未命中分支（{{ selectedAffair.completed_steps.length }}）</summary>
            <ol>
              <li v-for="step in selectedAffair.completed_steps" :key="step.step_instance_id">
                <span>{{ stepLabel(step.state) }}</span>
                <strong>{{ step.title }}</strong>
                <p>{{ step.result || '没有结果正文。' }}</p>
              </li>
            </ol>
          </details>

          <section v-if="selectedAffair.preview_steps.length" class="preview-steps">
            <h4>后续预览</h4>
            <ol>
              <li v-for="step in selectedAffair.preview_steps" :key="step.step_instance_id">
                <span>{{ step.safety_required ? '安全必做' : '等待前置步骤' }}</span>
                <strong>{{ step.title }}</strong>
              </li>
            </ol>
          </section>

          <form v-if="selectedAffair.state === 'active'" class="affair-close" @submit.prevent="closeAffair">
            <label>
              <span>教师结案说明</span>
              <textarea v-model="closureSummary" rows="3" maxlength="8000" />
            </label>
            <button class="button button--secondary" type="submit" :disabled="saving || !closureSummary.trim()">
              教师确认结案
            </button>
          </form>
          <form v-else class="affair-close" @submit.prevent="reopenAffair">
            <label>
              <span>出现新信息后的重开原因</span>
              <textarea v-model="reopenReason" rows="3" maxlength="4000" />
            </label>
            <button class="button button--secondary" type="submit" :disabled="saving || !reopenReason.trim()">
              以新一轮重开
            </button>
          </form>
        </main>

        <div v-else class="empty-state affair-empty">
          选择或建立一个事务清单后，这里会展开当前步骤。
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.sop-workspace {
  display: grid;
  gap: var(--space-6);
  margin-bottom: var(--space-8);
  padding: var(--space-7);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.sop-workspace__heading,
.baseline-gate,
.affair-header,
.step-card header,
.step-draft > div {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
}

.sop-workspace__heading h2,
.affair-header h3 {
  margin: var(--space-1) 0 var(--space-2);
}

.sop-workspace__heading p:last-child {
  max-width: 760px;
  margin: 0;
  color: var(--color-text-secondary);
}

.scope-stamp {
  display: grid;
  align-content: center;
  align-self: flex-start;
  min-width: 150px;
  padding: var(--space-3);
  border: 2px solid var(--color-warning);
  color: var(--color-warning);
  text-align: center;
  transform: rotate(-1deg);
}

.scope-stamp span {
  font-size: var(--font-size-caption);
}

.local-message {
  margin: 0;
  color: var(--color-success);
}

.baseline-gate {
  align-items: center;
  padding: var(--space-5);
  border: var(--border-width) dashed var(--color-border-strong);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.baseline-gate h3,
.baseline-gate p {
  margin: 0;
}

.baseline-gate p {
  margin-top: var(--space-2);
  color: var(--color-text-secondary);
}

.sop-layout {
  display: grid;
  grid-template-columns: minmax(260px, 0.3fr) minmax(0, 1fr);
  gap: var(--space-5);
}

.affair-rail {
  display: grid;
  align-content: start;
  gap: var(--space-4);
}

.affair-create {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-4);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.affair-create h3 {
  margin: 0;
}

.affair-rail nav {
  display: grid;
  gap: var(--space-2);
}

.affair-rail nav button {
  display: grid;
  gap: var(--space-1);
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  text-align: left;
  cursor: pointer;
}

.affair-rail nav button[aria-current="page"] {
  border-color: var(--color-accent);
  box-shadow: inset 4px 0 0 var(--color-accent);
}

.affair-rail nav span {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.affair-main {
  display: grid;
  align-content: start;
  gap: var(--space-5);
}

.emergency-card {
  display: grid;
  gap: var(--space-2);
  padding: var(--space-5);
  border: 2px solid var(--color-warning);
  border-radius: var(--radius-control);
  background: var(--color-warning-subtle);
}

.emergency-card[data-risk="emergency"] {
  border-color: var(--color-danger);
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.emergency-card span {
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
  letter-spacing: 0.08em;
}

.emergency-card small {
  color: var(--color-text-secondary);
}

.affair-header {
  align-items: flex-start;
  padding-bottom: var(--space-4);
  border-bottom: var(--border-width) solid var(--color-border-default);
}

.affair-header p {
  margin: 0;
}

.affair-header > span {
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-tag);
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.affair-header > span[data-state="closed"] {
  background: var(--color-success-subtle);
  color: var(--color-success);
}

.school-gaps {
  padding: var(--space-3);
  border-inline-start: 4px solid var(--color-warning);
  background: var(--color-warning-subtle);
}

.school-gaps summary {
  cursor: pointer;
  font-weight: var(--font-weight-medium);
}

.current-steps,
.decision-desk,
.preview-steps {
  display: grid;
  gap: var(--space-3);
}

.current-steps h4,
.decision-desk h4,
.preview-steps h4 {
  margin: 0;
}

.step-card {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-5);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  box-shadow: var(--shadow-card);
}

.step-card[data-safety="true"] {
  border-inline-start: 5px solid var(--color-danger);
}

.step-card header span {
  color: var(--color-accent);
  font-size: var(--font-size-caption);
}

.step-card header h5 {
  margin: var(--space-1) 0 0;
  font-size: var(--font-size-h3);
}

.step-card header > strong {
  color: var(--color-danger);
}

.step-card > p {
  margin: 0;
  color: var(--color-text-secondary);
}

.step-draft {
  padding: var(--space-4);
  border: var(--border-width) dashed var(--color-border-strong);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.step-draft strong {
  color: var(--color-warning);
}

.step-draft small {
  color: var(--color-text-muted);
}

.step-card__actions {
  display: flex;
  gap: var(--space-2);
}

.decision-desk article {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-5);
  border: 2px solid var(--color-accent);
  border-radius: var(--radius-panel);
  background: var(--color-accent-subtle);
}

.decision-desk label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.decision-desk input {
  width: auto;
  min-height: auto;
}

.step-history,
.preview-steps {
  padding: var(--space-4);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
}

.step-history summary {
  cursor: pointer;
}

.step-history ol,
.preview-steps ol {
  display: grid;
  gap: var(--space-2);
  margin: var(--space-3) 0 0;
  padding: 0;
  list-style: none;
}

.step-history li,
.preview-steps li {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  border-top: var(--border-width) solid var(--color-border-subtle);
}

.step-history li span,
.preview-steps li span {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.step-history li p {
  grid-column: 2;
  margin: 0;
  color: var(--color-text-secondary);
}

.affair-close {
  display: grid;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: var(--border-width) solid var(--color-border-default);
}

.affair-close .button {
  justify-self: start;
}

.affair-empty,
.empty-state {
  color: var(--color-text-secondary);
}

.affair-empty {
  display: grid;
  min-height: 300px;
  place-items: center;
  border: var(--border-width) dashed var(--color-border-strong);
}

@media (max-width: 980px) {
  .sop-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 820px) {
  .sop-workspace {
    padding: var(--space-5);
  }

  .sop-workspace__heading,
  .baseline-gate,
  .affair-header,
  .step-card__actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .scope-stamp {
    transform: none;
  }
}
</style>
