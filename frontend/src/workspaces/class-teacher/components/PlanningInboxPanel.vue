<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import {
  planningApi,
  type PlanningDraft,
} from '../api/planning'

const props = defineProps<{ sessionToken: string }>()
const emit = defineEmits<{
  activity: []
  confirmed: []
  error: [message: string]
  locked: []
}>()

const loading = ref(true)
const saving = ref(false)
const rawInput = ref('')
const exactDeadline = ref('')
const activeDraft = ref<PlanningDraft | null>(null)
const localMessage = ref('')

const canConfirm = computed(() => {
  const draft = activeDraft.value
  return Boolean(
    draft
    && draft.status === 'draft'
    && draft.final_deadline
    && draft.sensitive_findings.length === 0
    && draft.actions.length
    && draft.actions.every((item) => item.title.trim() && item.due_at),
  )
})

function report(error: unknown): void {
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit('locked')
    return
  }
  emit(
    'error',
    error instanceof ApiError
      ? error.message
      : '本地规划草稿没有完成本次操作，正式行动没有改变。',
  )
}

function toIso(value: string | null): string | null {
  return value ? new Date(value).toISOString() : null
}

function toLocalInput(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function formatDate(value: string | null): string {
  if (!value) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

async function refresh(): Promise<void> {
  try {
    const drafts = await planningApi.list(props.sessionToken)
    activeDraft.value = drafts.find((item) => item.status === 'draft') ?? null
  } catch (error) {
    report(error)
  } finally {
    loading.value = false
  }
}

async function createDraft(): Promise<void> {
  if (!rawInput.value.trim()) return
  saving.value = true
  localMessage.value = ''
  try {
    activeDraft.value = await planningApi.create(
      props.sessionToken,
      rawInput.value,
      toIso(exactDeadline.value),
    )
    rawInput.value = ''
    exactDeadline.value = ''
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function cancelDraft(): Promise<void> {
  if (!activeDraft.value) return
  saving.value = true
  try {
    await planningApi.cancel(props.sessionToken, activeDraft.value)
    activeDraft.value = null
    localMessage.value = '草稿已取消，没有生成正式行动，也没有发送任何内容。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function confirmDraft(): Promise<void> {
  if (!activeDraft.value || !canConfirm.value) return
  saving.value = true
  try {
    await planningApi.confirm(props.sessionToken, {
      ...activeDraft.value,
      actions: activeDraft.value.actions.map((item) => ({
        ...item,
        due_at: toIso(item.due_at),
      })),
    })
    activeDraft.value = null
    localMessage.value = '草稿已正式入账。行动账本已刷新；沟通文案仍未发送。'
    emit('activity')
    emit('confirmed')
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
  <section class="planning-inbox">
    <header class="planning-inbox__heading">
      <div>
        <p class="section-kicker">B03 · 本地任务便笺</p>
        <h2>先拆成草稿，再决定是否入账</h2>
        <p>这里只使用本地规则。草稿不会出现在正式日历，文案也不会自动发送。</p>
      </div>
      <div class="zero-request-seal" aria-label="外部请求状态">
        <strong>0</strong>
        <span>外部请求</span>
      </div>
    </header>

    <p v-if="localMessage" class="planning-note" role="status">{{ localMessage }}</p>
    <p v-if="loading" class="planning-empty">正在读取加密草稿…</p>

    <form
      v-else-if="!activeDraft"
      class="planning-capture"
      @submit.prevent="createDraft"
    >
      <label class="planning-capture__task">
        <span>写下一件要安排的事</span>
        <textarea
          v-model="rawInput"
          rows="3"
          maxlength="4000"
          placeholder="例如：下周五收齐防溺水回执并报年级"
        />
      </label>
      <label>
        <span>精确截止时间（可选）</span>
        <input v-model="exactDeadline" type="datetime-local">
        <small>若句子里的日期不完整，可在这里直接确定。</small>
      </label>
      <button
        class="button button--primary"
        type="submit"
        :disabled="saving || !rawInput.trim()"
      >
        {{ saving ? '正在本地拆解…' : '生成本地草稿' }}
      </button>
    </form>

    <div v-else class="draft-sheet">
      <div class="draft-sheet__status">
        <span>未正式入账</span>
        <strong>尚未发送</strong>
      </div>

      <div class="draft-sheet__summary">
        <label>
          <span>工作目标</span>
          <input v-model="activeDraft.plan_title" maxlength="240">
        </label>
        <div>
          <span>最终截止</span>
          <strong>{{ formatDate(activeDraft.final_deadline) }}</strong>
        </div>
        <div>
          <span>本地检查</span>
          <strong>{{ activeDraft.is_late ? '已有步骤晚于建议时间' : '未发现时间倒挂' }}</strong>
        </div>
      </div>

      <div
        v-if="activeDraft.sensitive_findings.length"
        class="draft-warning draft-warning--danger"
        role="alert"
      >
        <strong>此内容不能按普通任务确认</strong>
        <p>命中：{{ activeDraft.sensitive_findings.join('、') }}。请改用人工记录或后续 SOP 流程。</p>
      </div>
      <div v-else-if="activeDraft.unknowns.length" class="draft-warning">
        <strong>还不能正式入账</strong>
        <ul>
          <li v-for="item in activeDraft.unknowns" :key="item">{{ item }}</li>
        </ul>
        <p>请先在下方“学校日历设置”补齐口径，再重新生成草稿。</p>
      </div>

      <ol class="draft-actions">
        <li v-for="(item, index) in activeDraft.actions" :key="item.draft_action_id">
          <span class="draft-actions__number">{{ String(index + 1).padStart(2, '0') }}</span>
          <div>
            <input v-model="item.title" maxlength="240" aria-label="行动名称">
            <p>{{ item.details }}</p>
          </div>
          <label>
            <span>最晚完成</span>
            <input
              :value="toLocalInput(item.due_at)"
              type="datetime-local"
              @input="item.due_at = ($event.target as HTMLInputElement).value"
            >
          </label>
        </li>
      </ol>

      <aside
        v-for="message in activeDraft.communication_drafts"
        :key="message.content"
        class="communication-proof"
      >
        <header>
          <div>
            <span>沟通草稿 · {{ message.audience }}</span>
            <strong>尚未发送</strong>
          </div>
          <small>{{ activeDraft.send_preview.summary }}</small>
        </header>
        <p>{{ message.content }}</p>
        <dl>
          <div><dt>依据</dt><dd>{{ message.basis }}</dd></div>
          <div>
            <dt>不确定项</dt>
            <dd>{{ message.unknowns.length ? message.unknowns.join('；') : '无' }}</dd>
          </div>
        </dl>
      </aside>

      <footer class="draft-sheet__actions">
        <button
          class="button button--primary"
          type="button"
          :disabled="saving || !canConfirm"
          @click="confirmDraft"
        >
          确认并写入正式行动
        </button>
        <button
          class="button button--secondary"
          type="button"
          :disabled="saving"
          @click="cancelDraft"
        >
          取消草稿
        </button>
        <span>确认也不会发送沟通文案。</span>
      </footer>
    </div>
  </section>
</template>

<style scoped>
.planning-inbox {
  margin-bottom: var(--space-8);
  padding: var(--space-7);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background:
    linear-gradient(90deg, var(--color-accent-subtle) 0 7px, transparent 7px),
    var(--color-bg-surface);
}

.planning-inbox__heading {
  display: flex;
  justify-content: space-between;
  gap: var(--space-6);
  padding-inline-start: var(--space-3);
}

.planning-inbox__heading h2 {
  margin: var(--space-1) 0 var(--space-2);
}

.planning-inbox__heading p:last-child {
  max-width: 720px;
  margin: 0;
  color: var(--color-text-secondary);
}

.zero-request-seal {
  display: grid;
  place-items: center;
  align-self: flex-start;
  width: 88px;
  height: 88px;
  flex: 0 0 88px;
  border: 2px solid var(--color-success);
  border-radius: 50%;
  color: var(--color-success);
  text-align: center;
  transform: rotate(3deg);
}

.zero-request-seal strong {
  font: 700 2rem/0.8 ui-monospace, "Cascadia Mono", Consolas, monospace;
}

.zero-request-seal span {
  font-size: var(--font-size-caption);
  letter-spacing: 0.08em;
}

.planning-capture {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(240px, 0.36fr);
  align-items: end;
  gap: var(--space-4);
  margin-top: var(--space-6);
  padding: var(--space-5);
  border-top: var(--border-width) solid var(--color-border-default);
}

.planning-capture__task {
  grid-row: span 2;
}

.planning-capture textarea {
  min-height: 116px;
  font-size: var(--font-size-h3);
  line-height: var(--line-height-relaxed);
}

.planning-capture small,
.draft-sheet__actions span {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.planning-note,
.planning-empty {
  margin: var(--space-5) 0 0 var(--space-3);
  color: var(--color-success);
}

.draft-sheet {
  margin-top: var(--space-6);
  border-top: var(--border-width) solid var(--color-border-default);
}

.draft-sheet__status {
  display: flex;
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
  background: var(--color-warning-subtle);
  color: var(--color-warning);
  font-size: var(--font-size-dense);
}

.draft-sheet__summary {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) repeat(2, minmax(160px, 0.4fr));
  gap: var(--space-4);
  padding: var(--space-5) var(--space-4);
}

.draft-sheet__summary > div {
  display: grid;
  align-content: center;
  gap: var(--space-1);
}

.draft-sheet__summary span,
.draft-actions label span {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.draft-warning {
  margin: 0 var(--space-4) var(--space-5);
  padding: var(--space-4);
  border-inline-start: 4px solid var(--color-warning);
  background: var(--color-warning-subtle);
}

.draft-warning--danger {
  border-color: var(--color-danger);
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.draft-warning p,
.draft-warning ul {
  margin-bottom: 0;
}

.draft-actions {
  margin: 0;
  padding: 0 var(--space-4);
  list-style: none;
}

.draft-actions li {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr) minmax(210px, 0.4fr);
  align-items: start;
  gap: var(--space-3);
  padding: var(--space-4) 0;
  border-top: var(--border-width) solid var(--color-border-subtle);
}

.draft-actions__number {
  color: var(--color-accent);
  font: 600 var(--font-size-dense)/var(--control-height) ui-monospace, "Cascadia Mono", Consolas, monospace;
}

.draft-actions p {
  margin: var(--space-1) 0 0;
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.communication-proof {
  margin: var(--space-5) var(--space-4);
  padding: var(--space-5);
  border: var(--border-width) dashed var(--color-border-strong);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.communication-proof header {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
}

.communication-proof header div {
  display: flex;
  gap: var(--space-3);
}

.communication-proof header strong {
  color: var(--color-warning);
}

.communication-proof small {
  color: var(--color-success);
}

.communication-proof dl {
  display: grid;
  gap: var(--space-2);
  margin-bottom: 0;
}

.communication-proof dl div {
  display: grid;
  grid-template-columns: 76px 1fr;
}

.communication-proof dt {
  color: var(--color-text-muted);
}

.communication-proof dd {
  margin: 0;
}

.draft-sheet__actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-4) 0;
  border-top: var(--border-width) solid var(--color-border-default);
}

@media (max-width: 820px) {
  .planning-inbox {
    padding: var(--space-5);
  }

  .planning-inbox__heading,
  .communication-proof header,
  .draft-sheet__actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .planning-capture,
  .draft-sheet__summary,
  .draft-actions li {
    grid-template-columns: 1fr;
  }

  .zero-request-seal {
    width: 72px;
    height: 72px;
    flex-basis: 72px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .zero-request-seal {
    transform: none;
  }
}
</style>
