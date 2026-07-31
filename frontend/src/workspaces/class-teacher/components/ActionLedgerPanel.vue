<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import {
  actionApi,
  type ActionDashboard,
  type ActionItem,
  type ActionStatus,
  type SchoolCalendar,
  type WorkPlan,
} from '../api/actions'

const props = defineProps<{ sessionToken: string }>()
const emit = defineEmits<{
  activity: []
  changed: []
  plansChanged: [plans: WorkPlan[]]
  error: [message: string]
  locked: [reason?: string]
}>()

const loading = ref(true)
const saving = ref(false)
const plans = ref<WorkPlan[]>([])
const actions = ref<ActionItem[]>([])
const dashboard = ref<ActionDashboard | null>(null)
const schoolCalendar = ref<SchoolCalendar | null>(null)
const viewMode = ref<'day' | 'week' | 'timeline' | 'list'>('day')
const selectedActionId = ref('')

const planTitle = ref('')
const planDescription = ref('')
const planDeadline = ref('')
const editingPlanId = ref('')
const actionPlanId = ref('')
const actionTitle = ref('')
const actionDetails = ref('')
const actionDueAt = ref('')
const actionDependencies = ref<string[]>([])

const editStatus = ref<ActionStatus>('pending')
const editDueAt = ref('')
const editWaitingFor = ref('')
const editReviewAt = ref('')
const editResult = ref('')
const editReason = ref('')
const calendarEnd = ref('')
const calendarLockedDates = ref('')
const calendarWeekdays = ref<number[]>([1, 2, 3, 4, 5])

const selectedAction = computed(() => (
  actions.value.find((item) => item.action_id === selectedActionId.value) ?? null
))

const visibleActions = computed(() => {
  if (viewMode.value === 'timeline' || viewMode.value === 'list') {
    return [...actions.value].sort((left, right) => {
      if (!left.due_at) return 1
      if (!right.due_at) return -1
      return left.due_at.localeCompare(right.due_at)
    })
  }
  const source = viewMode.value === 'day'
    ? [...(dashboard.value?.overdue ?? []), ...(dashboard.value?.today ?? [])]
    : [...(dashboard.value?.today ?? []), ...(dashboard.value?.upcoming ?? [])]
  return [...new Map(source.map((item) => [item.action_id, item])).values()]
})

const selectedNeedsReason = computed(() => {
  const previous = selectedAction.value?.status
  return (
    editStatus.value === 'pending'
    && previous !== undefined
    && ['completed', 'cancelled', 'superseded'].includes(previous)
  )
})

const canSaveAction = computed(() => {
  if (!selectedAction.value) return false
  if (editStatus.value === 'waiting') {
    return Boolean(editWaitingFor.value && editReviewAt.value)
  }
  if (['completed', 'partially_completed'].includes(editStatus.value)) {
    return Boolean(editResult.value.trim())
  }
  if (selectedNeedsReason.value) return Boolean(editReason.value.trim())
  return true
})

const STATUS_LABELS: Record<ActionStatus, string> = {
  pending: '待开始',
  in_progress: '进行中',
  waiting: '等待中',
  partially_completed: '部分完成',
  completed: '已完成',
  cancelled: '已取消',
  superseded: '已替代',
}

function report(error: unknown): void {
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit('locked')
    return
  }
  emit(
    'error',
    error instanceof ApiError
      ? error.message
      : '行动账本没有完成本次操作，已保存内容没有改变。',
  )
}

function reportPostSaveRefresh(error: unknown): void {
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit('locked')
    return
  }
  emit(
    'error',
    '行动已经保存，但行动账本暂时无法重新读取最新内容，请稍后重试。',
  )
}

function reportPostPlanSaveRefresh(error: unknown): void {
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit(
      'locked',
      '工作目标已经保存，但行动账本暂时无法重新读取最新内容；保险箱已锁定，请重新解锁后核对。',
    )
    return
  }
  emit(
    'error',
    '工作目标已经保存，但行动账本暂时无法重新读取最新内容，请稍后重试。',
  )
}

function toApiDate(value: string): string | null {
  return value ? new Date(value).toISOString() : null
}

function toLocalInput(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function formatDate(value: string | null): string {
  if (!value) return '未设置'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function planName(planId: string): string {
  return plans.value.find((item) => item.plan_id === planId)?.title ?? '未命名目标'
}

async function refresh(
  failureContext: 'normal' | 'action_saved' | 'plan_saved' = 'normal',
): Promise<boolean> {
  try {
    const [loadedPlans, loadedActions, loadedDashboard, loadedCalendar] = await Promise.all([
      actionApi.listPlans(props.sessionToken),
      actionApi.listActions(props.sessionToken),
      actionApi.dashboard(props.sessionToken),
      actionApi.getCalendar(props.sessionToken),
    ])
    plans.value = loadedPlans
    actions.value = loadedActions
    dashboard.value = loadedDashboard
    schoolCalendar.value = loadedCalendar
    if (!actionPlanId.value && loadedPlans[0]) actionPlanId.value = loadedPlans[0].plan_id
    calendarEnd.value = loadedCalendar.school_day_end ?? ''
    calendarLockedDates.value = loadedCalendar.locked_dates.join('\n')
    calendarWeekdays.value = loadedCalendar.working_weekdays ?? [1, 2, 3, 4, 5]
    return true
  } catch (error) {
    if (failureContext === 'action_saved') reportPostSaveRefresh(error)
    else if (failureContext === 'plan_saved') reportPostPlanSaveRefresh(error)
    else report(error)
    return false
  } finally {
    loading.value = false
  }
}

async function createPlan(): Promise<void> {
  if (!planTitle.value.trim()) return
  saving.value = true
  try {
    const created = await actionApi.createPlan(props.sessionToken, {
      title: planTitle.value,
      description: planDescription.value,
      final_deadline: toApiDate(planDeadline.value),
    })
    planTitle.value = ''
    planDescription.value = ''
    planDeadline.value = ''
    actionPlanId.value = created.plan_id
    emit('activity')
    const refreshed = await refresh('plan_saved')
    if (!refreshed) return
    emit('plansChanged', plans.value.map((item) => ({ ...item })))
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

function editPlan(plan: WorkPlan): void {
  editingPlanId.value = plan.plan_id
  planTitle.value = plan.title
  planDescription.value = plan.description ?? ''
  planDeadline.value = toLocalInput(plan.final_deadline)
}

function resetPlanForm(): void {
  editingPlanId.value = ''
  planTitle.value = ''
  planDescription.value = ''
  planDeadline.value = ''
}

async function savePlan(): Promise<void> {
  const current = plans.value.find((item) => item.plan_id === editingPlanId.value)
  if (!current || !planTitle.value.trim()) return
  saving.value = true
  try {
    await actionApi.updatePlan(props.sessionToken, current.plan_id, {
      revision: current.revision,
      title: planTitle.value,
      description: planDescription.value,
      final_deadline: toApiDate(planDeadline.value),
    })
    resetPlanForm()
    emit('activity')
    const refreshed = await refresh('plan_saved')
    if (!refreshed) return
    emit('plansChanged', plans.value.map((item) => ({ ...item })))
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function createAction(): Promise<void> {
  if (!actionPlanId.value || !actionTitle.value.trim()) return
  saving.value = true
  try {
    await actionApi.createAction(props.sessionToken, {
      plan_id: actionPlanId.value,
      title: actionTitle.value,
      details: actionDetails.value,
      due_at: toApiDate(actionDueAt.value),
      depends_on_action_ids: actionDependencies.value,
    })
    actionTitle.value = ''
    actionDetails.value = ''
    actionDueAt.value = ''
    actionDependencies.value = []
    emit('activity')
    await refresh()
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

function selectAction(item: ActionItem): void {
  selectedActionId.value = item.action_id
  editStatus.value = item.status
  editDueAt.value = toLocalInput(item.due_at)
  editWaitingFor.value = item.waiting_for_kind ?? ''
  editReviewAt.value = toLocalInput(item.review_at)
  editResult.value = item.completion_result ?? ''
  editReason.value = ''
}

async function saveAction(): Promise<void> {
  const item = selectedAction.value
  if (!item || !canSaveAction.value) return
  saving.value = true
  try {
    await actionApi.updateAction(props.sessionToken, item.action_id, {
      revision: item.revision,
      status: editStatus.value,
      due_at: toApiDate(editDueAt.value),
      waiting_for_kind: editWaitingFor.value || null,
      review_at: toApiDate(editReviewAt.value),
      completion_result: editResult.value || null,
      reason: editReason.value || null,
    })
    emit('activity')
    const refreshed = await refresh('action_saved')
    if (!refreshed) return
    const updated = actions.value.find((action) => action.action_id === item.action_id)
    if (updated) selectAction(updated)
    emit('changed')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function saveCalendar(): Promise<void> {
  if (!schoolCalendar.value) return
  saving.value = true
  try {
    schoolCalendar.value = await actionApi.saveCalendar(props.sessionToken, {
      ...schoolCalendar.value,
      school_day_end: calendarEnd.value || null,
      locked_dates: calendarLockedDates.value
        .split(/\s+/)
        .map((value) => value.trim())
        .filter(Boolean),
      working_weekdays: [...calendarWeekdays.value],
    })
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <section class="action-ledger">
    <header class="action-ledger__heading">
      <div>
        <p class="section-kicker">B02 · 无 AI 行动账本</p>
        <h2>今天要推进什么</h2>
        <p>所有视图读取同一份正式行动；页面里的新输入在保存前都不是正式记录。</p>
      </div>
      <div class="view-switch" aria-label="行动视图">
        <button
          v-for="mode in ([
            ['day', '今天'],
            ['week', '本周'],
            ['timeline', '时间线'],
            ['list', '全部'],
          ] as const)"
          :key="mode[0]"
          type="button"
          :aria-pressed="viewMode === mode[0]"
          @click="viewMode = mode[0]"
        >
          {{ mode[1] }}
        </button>
      </div>
    </header>

    <p v-if="loading" class="empty-line">正在读取加密行动账本…</p>

    <div v-else class="ledger-layout">
      <div class="ledger-main">
        <div class="ledger-summary">
          <span><strong>{{ dashboard?.today.length ?? 0 }}</strong> 今天</span>
          <span><strong>{{ dashboard?.overdue.length ?? 0 }}</strong> 已逾期</span>
          <span><strong>{{ dashboard?.waiting.length ?? 0 }}</strong> 等待复查</span>
          <span><strong>{{ dashboard?.unscheduled.length ?? 0 }}</strong> 未排期</span>
        </div>

        <div v-if="!visibleActions.length" class="empty-line">
          当前视图没有行动。可在下方先创建工作目标，再添加第一项行动。
        </div>
        <ol v-else class="action-list">
          <li v-for="item in visibleActions" :key="item.action_id">
            <button type="button" @click="selectAction(item)">
              <span class="action-list__time">{{ formatDate(item.due_at) }}</span>
              <span class="action-list__body">
                <strong>{{ item.title }}</strong>
                <small>{{ planName(item.plan_id) }}</small>
              </span>
              <span class="action-status" :data-status="item.status">
                {{ STATUS_LABELS[item.status] }}
              </span>
            </button>
          </li>
        </ol>

        <form class="creation-row" @submit.prevent="createAction">
          <h3>添加正式行动</h3>
          <label>
            <span>所属目标</span>
            <select v-model="actionPlanId" :disabled="!plans.length">
              <option value="">请先创建目标</option>
              <option v-for="plan in plans" :key="plan.plan_id" :value="plan.plan_id">
                {{ plan.title }}
              </option>
            </select>
          </label>
          <label>
            <span>行动名称</span>
            <input v-model="actionTitle" maxlength="240">
          </label>
          <label>
            <span>截止时间（可暂不设置）</span>
            <input v-model="actionDueAt" type="datetime-local">
          </label>
          <label class="creation-row__wide">
            <span>具体说明（可选）</span>
            <textarea v-model="actionDetails" rows="2" maxlength="8000" />
          </label>
          <fieldset v-if="actions.length" class="dependency-picker creation-row__wide">
            <legend>前置行动（可选）</legend>
            <label v-for="item in actions" :key="item.action_id">
              <input v-model="actionDependencies" type="checkbox" :value="item.action_id">
              <span>{{ item.title }}</span>
            </label>
          </fieldset>
          <button
            class="button button--primary"
            type="submit"
            :disabled="saving || !actionPlanId || !actionTitle.trim()"
          >
            保存正式行动
          </button>
        </form>
      </div>

      <aside class="ledger-inspector">
        <template v-if="selectedAction">
          <p class="section-kicker">行动处理</p>
          <h3>{{ selectedAction.title }}</h3>
          <p>{{ selectedAction.details || '没有补充说明。' }}</p>
          <form @submit.prevent="saveAction">
            <label>
              <span>当前状态</span>
              <select v-model="editStatus">
                <option
                  v-for="(label, status) in STATUS_LABELS"
                  :key="status"
                  :value="status"
                >
                  {{ label }}
                </option>
              </select>
            </label>
            <label>
              <span>截止时间</span>
              <input v-model="editDueAt" type="datetime-local">
            </label>
            <template v-if="editStatus === 'waiting'">
              <label>
                <span>正在等待谁或什么</span>
                <input v-model="editWaitingFor" placeholder="例如：家长回复">
              </label>
              <label>
                <span>复查时间</span>
                <input v-model="editReviewAt" type="datetime-local">
              </label>
            </template>
            <label v-if="['completed', 'partially_completed'].includes(editStatus)">
              <span>处理结果</span>
              <textarea v-model="editResult" rows="3" />
            </label>
            <label v-if="selectedNeedsReason">
              <span>重开原因</span>
              <textarea v-model="editReason" rows="2" />
            </label>
            <button
              class="button button--secondary"
              type="submit"
              :disabled="saving || !canSaveAction"
            >
              保存行动变化
            </button>
          </form>
        </template>
        <p v-else class="empty-line">选择一项行动后，可在这里改期、等待、完成或重开。</p>
      </aside>
    </div>

    <details class="ledger-settings">
      <summary>目标与学校日历设置</summary>
      <div class="settings-grid">
        <form @submit.prevent="editingPlanId ? savePlan() : createPlan()">
          <h3>{{ editingPlanId ? '修改工作目标' : '新建工作目标' }}</h3>
          <div v-if="plans.length" class="plan-list">
            <button
              v-for="plan in plans"
              :key="plan.plan_id"
              type="button"
              @click="editPlan(plan)"
            >
              <span>{{ plan.title }}</span>
              <small>{{ plan.final_deadline ? formatDate(plan.final_deadline) : '最终截止时间未知' }}</small>
            </button>
          </div>
          <label>
            <span>目标名称</span>
            <input v-model="planTitle" maxlength="240">
          </label>
          <label>
            <span>最终截止时间（可暂不设置）</span>
            <input v-model="planDeadline" type="datetime-local">
          </label>
          <label>
            <span>说明（可选）</span>
            <textarea v-model="planDescription" rows="3" />
          </label>
          <button
            class="button button--secondary"
            type="submit"
            :disabled="saving || !planTitle.trim()"
          >
            {{ editingPlanId ? '保存目标并调整未完成行动' : '保存工作目标' }}
          </button>
          <button
            v-if="editingPlanId"
            class="button button--text"
            type="button"
            @click="resetPlanForm"
          >
            取消修改
          </button>
        </form>

        <form v-if="schoolCalendar" @submit.prevent="saveCalendar">
          <h3>学校日历口径</h3>
          <p>没有提供的信息保持“未知”，系统不会猜测。</p>
          <label>
            <span>通常放学时间（可留空）</span>
            <input v-model="calendarEnd" type="time">
          </label>
          <fieldset>
            <legend>通常工作日</legend>
            <label
              v-for="(label, index) in ['一', '二', '三', '四', '五', '六', '日']"
              :key="label"
            >
              <input v-model="calendarWeekdays" type="checkbox" :value="index + 1">
              周{{ label }}
            </label>
          </fieldset>
          <label>
            <span>锁定日期（每行一个 YYYY-MM-DD）</span>
            <textarea v-model="calendarLockedDates" rows="4" />
          </label>
          <button class="button button--secondary" type="submit" :disabled="saving">
            保存学校日历
          </button>
        </form>
      </div>
    </details>
  </section>
</template>

<style scoped>
.action-ledger {
  margin-bottom: var(--space-7);
  padding-bottom: var(--space-7);
  border-bottom: var(--border-width) solid var(--color-border-default);
}

.action-ledger__heading {
  display: flex;
  justify-content: space-between;
  gap: var(--space-5);
  margin-bottom: var(--space-5);
}

.action-ledger h2,
.action-ledger h3 {
  margin: 0;
  color: var(--color-text-primary);
}

.action-ledger h2 {
  font-size: var(--font-size-h2);
}

.action-ledger p {
  color: var(--color-text-secondary);
}

.section-kicker {
  margin: 0 0 var(--space-1);
  color: var(--color-accent-active);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  letter-spacing: 0.08em;
}

.view-switch {
  display: flex;
  align-self: flex-start;
  padding: var(--space-1);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.view-switch button {
  min-height: var(--control-height-small);
  padding: 0 var(--space-3);
  border: 0;
  border-radius: var(--radius-tag);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.view-switch button[aria-pressed="true"] {
  background: var(--color-bg-surface);
  color: var(--color-accent-active);
  font-weight: var(--font-weight-semibold);
}

.ledger-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.ledger-main {
  min-width: 0;
  padding: var(--space-5);
}

.ledger-inspector {
  padding: var(--space-5);
  border-inline-start: var(--border-width) solid var(--color-border-default);
  background: var(--color-bg-subtle);
}

.ledger-inspector form,
.settings-grid form {
  display: grid;
  gap: var(--space-4);
  margin-top: var(--space-4);
}

.ledger-summary {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-5);
  padding-bottom: var(--space-4);
  border-bottom: var(--border-width) solid var(--color-border-subtle);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.ledger-summary strong {
  margin-inline-end: var(--space-1);
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.action-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.action-list li + li {
  border-top: var(--border-width) solid var(--color-border-subtle);
}

.action-list button {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr) auto;
  width: 100%;
  gap: var(--space-3);
  align-items: center;
  padding: var(--space-3) 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: start;
  cursor: pointer;
}

.action-list button:hover,
.action-list button:focus-visible {
  background: var(--color-bg-subtle);
  outline: none;
}

.action-list__time {
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.action-list__body {
  display: grid;
  gap: var(--space-1);
  min-width: 0;
}

.action-list__body strong {
  overflow: hidden;
  color: var(--color-text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.action-list__body small {
  color: var(--color-text-muted);
}

.action-status {
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-tag);
  background: var(--color-info-subtle);
  color: var(--color-info);
  font-size: var(--font-size-caption);
}

.action-status[data-status="waiting"] {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.action-status[data-status="completed"] {
  background: var(--color-success-subtle);
  color: var(--color-success);
}

.creation-row {
  display: grid;
  grid-template-columns: 1fr 1.2fr 1fr;
  gap: var(--space-3);
  margin-top: var(--space-5);
  padding-top: var(--space-5);
  border-top: var(--border-width) solid var(--color-border-default);
}

.creation-row h3,
.creation-row__wide {
  grid-column: 1 / -1;
}

.creation-row .button {
  align-self: end;
}

label {
  display: grid;
  gap: var(--space-2);
  color: var(--color-text-primary);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-medium);
}

input,
select,
textarea {
  width: 100%;
}

textarea {
  resize: vertical;
}

.button {
  min-height: var(--control-height-large);
  padding: 0 var(--space-4);
  border: var(--border-width) solid transparent;
  border-radius: var(--radius-control);
  font: inherit;
  font-weight: var(--font-weight-medium);
  cursor: pointer;
}

.button:disabled {
  cursor: not-allowed;
  opacity: var(--opacity-disabled);
}

.button--primary {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: var(--color-bg-surface);
}

.button--secondary {
  border-color: var(--color-border-strong);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.empty-line {
  padding: var(--space-6) 0;
  color: var(--color-text-muted);
}

.ledger-settings {
  margin-top: var(--space-4);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.ledger-settings summary {
  padding: var(--space-3) var(--space-4);
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
  cursor: pointer;
}

.settings-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-6);
  padding: 0 var(--space-5) var(--space-5);
  border-top: var(--border-width) solid var(--color-border-subtle);
}

.settings-grid fieldset {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
}

.dependency-picker {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin: 0;
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
}

.dependency-picker label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: var(--font-weight-regular);
}

.dependency-picker input {
  width: auto;
}

.plan-list {
  display: grid;
  gap: var(--space-1);
  padding-bottom: var(--space-3);
  border-bottom: var(--border-width) solid var(--color-border-subtle);
}

.plan-list button {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2);
  border: 0;
  border-radius: var(--radius-tag);
  background: transparent;
  color: var(--color-text-primary);
  text-align: start;
  cursor: pointer;
}

.plan-list button:hover {
  background: var(--color-bg-subtle);
}

.plan-list small {
  color: var(--color-text-muted);
}

.button--text {
  border-color: transparent;
  background: transparent;
  color: var(--color-accent);
}

.settings-grid fieldset label {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.settings-grid fieldset input {
  width: auto;
}

@media (max-width: 960px) {
  .ledger-layout,
  .settings-grid {
    grid-template-columns: 1fr;
  }

  .ledger-inspector {
    border-top: var(--border-width) solid var(--color-border-default);
    border-inline-start: 0;
  }

  .creation-row {
    grid-template-columns: 1fr;
  }

  .creation-row h3,
  .creation-row__wide {
    grid-column: auto;
  }
}
</style>
