<script setup lang="ts">
import { computed, markRaw, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'

import {
  aiDiagnosticsApi,
  type AiDiagnosticDetail,
  type AiDiagnosticOutcome,
  type AiDiagnosticSummary,
} from '../../api/ai-diagnostics'
import type { WorkspaceAITask } from '../../workspaces/shared/ai-tasks/contracts'
import '../../styles/model-profiles.css'

type DiagnosticTab = 'request' | 'attachments' | 'response' | 'parsed' | 'error'

const diagnostics = ref<AiDiagnosticSummary[]>([])
const diagnosticsState = ref<'idle' | 'loading' | 'error'>('idle')
const diagnosticsError = ref('')
const diagnosticsNotice = ref('')
const workspaceTaskRecords = ref<WorkspaceAITask[]>([])
const diagnosticOutcome = ref<'' | AiDiagnosticOutcome>('')
const diagnosticKind = ref('')
const workspaceModuleFilter = ref<'' | WorkspaceAITask['module']>('')
const workspaceTaskKindFilter = ref('')
const selectedDiagnosticId = ref('')
const selectedDiagnostic = shallowRef<AiDiagnosticDetail | null>(null)
const diagnosticDetailState = ref<'idle' | 'loading' | 'error'>('idle')
const diagnosticTab = ref<DiagnosticTab>('request')
const expandRequestJson = ref(false)
const expandParsedJson = ref(false)
const expandRawResponse = ref(false)
const requestJsonText = ref('')
const parsedJsonText = ref('')
const rawResponseText = ref('')

const JSON_DISPLAY_LIMIT = 80_000

let diagnosticsController: AbortController | null = null
let diagnosticDetailController: AbortController | null = null
let workspaceTaskRecordsController: AbortController | null = null

const diagnosticKinds = [
  { value: '', label: '全部用途' },
  { value: 'recognition', label: '识别' },
  { value: 'grading', label: '批改' },
  { value: 'config_generation', label: '评分标准' },
  { value: 'tagging', label: '题库标注' },
  { value: 'workspace', label: '工作台' },
] as const
const workspaceModules = [
  { value: '', label: '全部工作台' },
] as const
const workspaceTaskKinds = [
  { value: '', module: '', label: '全部功能' },
] as const
const availableWorkspaceTaskKinds = computed(() => workspaceTaskKinds.filter(
  ({ module }) => !module || !workspaceModuleFilter.value || module === workspaceModuleFilter.value,
))
const workspaceTasksByOperation = computed(() => new Map(
  workspaceTaskRecords.value.map((task) => [task.operation_id, task]),
))
const visibleDiagnostics = computed(() => diagnostics.value.filter((call) => {
  if (!workspaceModuleFilter.value && !workspaceTaskKindFilter.value) return true
  if (call.request_kind !== 'workspace') return false
  if (call.workspace_module || call.workspace_task_kind) {
    return (
      (!workspaceModuleFilter.value || call.workspace_module === workspaceModuleFilter.value)
      && (!workspaceTaskKindFilter.value || call.workspace_task_kind === workspaceTaskKindFilter.value)
    )
  }
  const task = workspaceTasksByOperation.value.get(call.operation_id)
  if (!task) return false
  return (
    (!workspaceModuleFilter.value || task.module === workspaceModuleFilter.value)
    && (!workspaceTaskKindFilter.value || task.task_kind === workspaceTaskKindFilter.value)
  )
}))
const diagnosticTabs: readonly { value: DiagnosticTab; label: string }[] = [
  { value: 'request', label: '发送内容' },
  { value: 'attachments', label: '附件' },
  { value: 'response', label: '原始返回' },
  { value: 'parsed', label: '解析结果' },
  { value: 'error', label: '错误与重试' },
]

function linkedTask(call: AiDiagnosticSummary): WorkspaceAITask | undefined {
  return workspaceTasksByOperation.value.get(call.operation_id)
}

function diagnosticKindLabel(value: string): string {
  return diagnosticKinds.find((item) => item.value === value)?.label ?? value
}

function workspaceModuleLabel(module: WorkspaceAITask['module']): string {
  return String(module)
}

function workspaceTaskKindLabel(taskKind: string): string {
  return workspaceTaskKinds.find(({ value }) => value === taskKind)?.label ?? '其他功能'
}

function diagnosticDisplayLabel(call: AiDiagnosticSummary): string {
  if (call.request_kind !== 'workspace') return diagnosticKindLabel(call.request_kind)
  if (call.workspace_module) {
    return `${workspaceModuleLabel(call.workspace_module as WorkspaceAITask['module'])} · ${workspaceTaskKindLabel(call.workspace_task_kind)}`
  }
  const task = linkedTask(call)
  if (!task) return '工作台 · 功能未知'
  return `${workspaceModuleLabel(task.module)} · ${workspaceTaskKindLabel(task.task_kind)}`
}

function workspaceTaskStatus(task: WorkspaceAITask): string {
  if (task.status === 'proposal_ready') return '已返回可审核结果'
  if (task.status === 'needs_input') return '等待补充信息'
  if (task.status === 'prepared') return '尚未发送'
  if (task.status === 'queued') return '等待发送'
  if (task.status === 'running') return '处理中'
  if (task.status === 'result_unknown') return '已尝试发送，结果无法确认'
  if (task.status.startsWith('failed') || task.status === 'invalid_result') return '失败'
  if (task.status.includes('cancel') || task.status === 'discarded') return '已取消'
  return task.status
}

function clearHiddenDiagnosticSelection(): void {
  if (
    selectedDiagnosticId.value
    && !visibleDiagnostics.value.some(({ call_id: callId }) => callId === selectedDiagnosticId.value)
  ) {
    clearSelectedDiagnostic()
  }
}

function handleDiagnosticKindChange(): void {
  if (diagnosticKind.value !== 'workspace') {
    workspaceModuleFilter.value = ''
    workspaceTaskKindFilter.value = ''
  }
  void loadDiagnostics()
}

function handleWorkspaceModuleChange(): void {
  const selected = workspaceTaskKinds.find(
    ({ value }) => value === workspaceTaskKindFilter.value,
  )
  if (
    selected?.module
    && workspaceModuleFilter.value
    && selected.module !== workspaceModuleFilter.value
  ) workspaceTaskKindFilter.value = ''
  clearHiddenDiagnosticSelection()
  void loadDiagnostics()
}

function handleWorkspaceTaskKindChange(): void {
  clearHiddenDiagnosticSelection()
  void loadDiagnostics()
}

function diagnosticOutcomeLabel(value: AiDiagnosticOutcome): string {
  if (value === 'success') return '模型已返回'
  if (value === 'failure') return '调用失败'
  return '等待返回'
}

function formatDiagnosticTime(value: string): string {
  if (!value) return '时间未知'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(parsed)
}

function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${milliseconds} 毫秒`
  return `${(milliseconds / 1_000).toFixed(milliseconds < 10_000 ? 1 : 0)} 秒`
}

function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} KB`
  return `${(bytes / 1_048_576).toFixed(1)} MB`
}

function formatDiagnosticJson(value: unknown): string {
  if (value === null || value === undefined) return '暂无内容'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return '内容无法转换为可读文本'
  }
}

function diagnosticTextPreview(
  text: string,
  expanded: boolean,
): { text: string; truncated: boolean; hiddenChars: number } {
  if (!text) return { text: '暂无内容', truncated: false, hiddenChars: 0 }
  if (expanded || text.length <= JSON_DISPLAY_LIMIT) {
    return { text, truncated: false, hiddenChars: 0 }
  }
  return {
    text: text.slice(0, JSON_DISPLAY_LIMIT),
    truncated: true,
    hiddenChars: text.length - JSON_DISPLAY_LIMIT,
  }
}

const requestPreview = computed(() => diagnosticTextPreview(
  requestJsonText.value,
  expandRequestJson.value,
))
const parsedPreview = computed(() => diagnosticTextPreview(
  parsedJsonText.value,
  expandParsedJson.value,
))
const rawResponsePreview = computed(() => diagnosticTextPreview(
  rawResponseText.value,
  expandRawResponse.value,
))

function clearSelectedDiagnostic(): void {
  selectedDiagnosticId.value = ''
  selectedDiagnostic.value = null
  diagnosticDetailState.value = 'idle'
  expandRequestJson.value = false
  expandParsedJson.value = false
  expandRawResponse.value = false
  requestJsonText.value = ''
  parsedJsonText.value = ''
  rawResponseText.value = ''
}

async function loadDiagnosticDetail(callId: string): Promise<void> {
  diagnosticDetailController?.abort()
  const controller = new AbortController()
  diagnosticDetailController = controller
  selectedDiagnosticId.value = callId
  selectedDiagnostic.value = null
  requestJsonText.value = ''
  parsedJsonText.value = ''
  rawResponseText.value = ''
  expandRequestJson.value = false
  expandParsedJson.value = false
  expandRawResponse.value = false
  diagnosticDetailState.value = 'loading'
  diagnosticTab.value = 'request'
  try {
    const detail = markRaw(await aiDiagnosticsApi.detail(callId, controller.signal))
    selectedDiagnostic.value = detail
    requestJsonText.value = formatDiagnosticJson(detail.request)
    parsedJsonText.value = formatDiagnosticJson(detail.parsed_result)
    rawResponseText.value = detail.raw_response || '暂无原始返回'
    diagnosticDetailState.value = 'idle'
  } catch (error) {
    if (controller.signal.aborted) return
    diagnosticDetailState.value = 'error'
    diagnosticsError.value = error instanceof Error
      ? error.message
      : '这次调用的详情没有加载成功。'
  }
}

async function loadDiagnostics(): Promise<void> {
  diagnosticsController?.abort()
  const controller = new AbortController()
  diagnosticsController = controller
  diagnosticsState.value = 'loading'
  diagnosticsError.value = ''
  try {
    const result = await aiDiagnosticsApi.list({
      limit: 100,
      requestKind: diagnosticKind.value,
      outcome: diagnosticOutcome.value,
      workspaceModule: workspaceModuleFilter.value,
      workspaceTaskKind: workspaceTaskKindFilter.value,
      signal: controller.signal,
    })
    diagnostics.value = result.items
    diagnosticsState.value = 'idle'
    const selectedStillVisible = visibleDiagnostics.value.some(
      ({ call_id: callId }) => callId === selectedDiagnosticId.value,
    )
    if (!selectedStillVisible) {
      clearSelectedDiagnostic()
    }
  } catch (error) {
    if (controller.signal.aborted) return
    diagnosticsState.value = 'error'
    diagnosticsError.value = error instanceof Error
      ? error.message
      : 'AI 调用记录没有加载成功。'
  }
}


async function loadWorkspaceTaskRecords(): Promise<void> {
  workspaceTaskRecordsController?.abort()
  const controller = new AbortController()
  workspaceTaskRecordsController = controller
  try {
    const availableTasks: WorkspaceAITask[] = []
    if (controller.signal.aborted) return
    workspaceTaskRecords.value = availableTasks
      .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))
    clearHiddenDiagnosticSelection()
  } catch {
    if (controller.signal.aborted) return
  }
}

onMounted(() => {
  void loadDiagnostics()
  void loadWorkspaceTaskRecords()
})

onBeforeUnmount(() => {
  diagnosticsController?.abort()
  diagnosticDetailController?.abort()
  workspaceTaskRecordsController?.abort()
})
</script>

<template>
  <section class="ai-diagnostics" aria-label="AI 调用记录">
    <header class="ai-diagnostics__header">
      <div class="ai-diagnostics__filters">
        <label>
          <span>来源</span>
          <select v-model="diagnosticKind" @change="handleDiagnosticKindChange">
            <option v-for="kind in diagnosticKinds" :key="kind.value" :value="kind.value">
              {{ kind.label }}
            </option>
          </select>
        </label>
        <label v-if="diagnosticKind === 'workspace'">
          <span>工作台</span>
          <select v-model="workspaceModuleFilter" @change="handleWorkspaceModuleChange">
            <option v-for="module in workspaceModules" :key="module.value" :value="module.value">
              {{ module.label }}
            </option>
          </select>
        </label>
        <label v-if="diagnosticKind === 'workspace'">
          <span>具体功能</span>
          <select v-model="workspaceTaskKindFilter" @change="handleWorkspaceTaskKindChange">
            <option
              v-for="taskKind in availableWorkspaceTaskKinds"
              :key="taskKind.value"
              :value="taskKind.value"
            >
              {{ taskKind.label }}
            </option>
          </select>
        </label>
        <label>
          <span>结果</span>
          <select v-model="diagnosticOutcome" @change="loadDiagnostics">
            <option value="">全部结果</option>
            <option value="success">模型已返回</option>
            <option value="failure">调用失败</option>
            <option value="pending">等待返回</option>
          </select>
        </label>
        <button
          type="button"
          class="model-profiles-button model-profiles-button--secondary"
          :disabled="diagnosticsState === 'loading'"
          @click="loadDiagnostics"
        >
          {{ diagnosticsState === 'loading' ? '正在刷新…' : '刷新记录' }}
        </button>
      </div>
    </header>

    <p v-if="diagnosticsNotice" class="ai-diagnostics__privacy" role="status">
      {{ diagnosticsNotice }}
    </p>

    <p class="ai-diagnostics__privacy">
      <strong>“模型已返回”只表示请求完成。</strong>
      发送正文只留在本机调用日志里，不会写入任务摘要。
    </p>
    <details class="ai-diagnostics-disclosure">
      <summary>了解日志范围</summary>
      <p class="ai-diagnostics__privacy">
        <strong>本机唯一正文日志。</strong>
        文本请求、文本响应与解析／校验原因只写入 <code>logs/llm_diagnostics.jsonl</code>；
        不会复制到终端、访问日志、任务摘要或浏览器存储，也不会进入 Git 或普通备份。
        API 密钥、Authorization、Cookie、密码、访问／刷新令牌，以及附件、图片、音频、
        长 base64 和本机绝对路径不会保留正文。每条最多 1 MB；单个文件约 32 MB 时滚动，
        保留当前文件和最近 3 个旧文件。
      </p>
    </details>

    <p
      v-if="diagnosticsState === 'error'"
      class="model-profiles-feedback model-profiles-feedback--error"
      role="alert"
    >
      {{ diagnosticsError || 'AI 调用记录没有加载成功。' }}
    </p>

    <div class="ai-diagnostics__workspace">
      <aside class="ai-diagnostics-ledger" aria-label="AI 调用列表">
        <header>
          <strong>最近调用</strong>
          <span>{{ visibleDiagnostics.length }} 条</span>
        </header>
        <p
          v-if="diagnosticsState === 'loading' && diagnostics.length === 0"
          class="ai-diagnostics-ledger__state"
        >
          正在读取本机记录…
        </p>
        <p
          v-else-if="visibleDiagnostics.length === 0"
          class="ai-diagnostics-ledger__state"
        >
          还没有符合当前筛选条件的调用。执行相应功能后再刷新。
        </p>
        <ul v-else>
          <li v-for="call in visibleDiagnostics" :key="call.call_id">
            <button
              type="button"
              class="ai-diagnostic-call"
              :class="{ 'ai-diagnostic-call--selected': call.call_id === selectedDiagnosticId }"
              :aria-current="call.call_id === selectedDiagnosticId ? 'true' : undefined"
              @click="loadDiagnosticDetail(call.call_id)"
            >
              <span class="ai-diagnostic-call__heading">
                <strong>{{ diagnosticDisplayLabel(call) }}</strong>
                <span class="ai-diagnostic-status" :class="`ai-diagnostic-status--${call.outcome}`">
                  {{ diagnosticOutcomeLabel(call.outcome) }}
                </span>
              </span>
              <span class="ai-diagnostic-call__model">{{ call.model || '模型未知' }}</span>
              <span v-if="linkedTask(call)" class="ai-diagnostic-call__meta">
                {{ workspaceTaskStatus(linkedTask(call)!) }}
              </span>
              <span class="ai-diagnostic-call__meta">
                <time>{{ formatDiagnosticTime(call.started_at_utc) }}</time>
                <span>{{ formatDuration(call.elapsed_ms) }}</span>
                <span v-if="call.image_count">{{ call.image_count }} 张图片</span>
                <span v-if="call.attempt > 1">第 {{ call.attempt }} 次尝试</span>
              </span>
            </button>
          </li>
        </ul>
      </aside>

      <article class="ai-diagnostic-detail" aria-live="polite">
        <div v-if="diagnosticDetailState === 'loading'" class="ai-diagnostic-detail__state">
          正在读取这次调用的完整记录…
        </div>
        <div
          v-else-if="diagnosticDetailState === 'error'"
          class="ai-diagnostic-detail__state ai-diagnostic-detail__state--error"
        >
          {{ diagnosticsError || '这次调用的详情没有加载成功。' }}
        </div>
        <div v-else-if="selectedDiagnostic === null" class="ai-diagnostic-detail__empty">
          <strong>选择一条调用记录</strong>
          <p>可以查看发送内容、附件、模型返回和解析结果。</p>
        </div>
        <template v-else>
          <header class="ai-diagnostic-detail__header">
            <div>
              <span>
                {{ diagnosticDisplayLabel(selectedDiagnostic) }}
                · {{ formatDiagnosticTime(selectedDiagnostic.started_at_utc) }}
                · {{ formatDuration(selectedDiagnostic.elapsed_ms) }}
                · {{ selectedDiagnostic.protocol }}
              </span>
              <h3>{{ selectedDiagnostic.model || '模型未知' }}</h3>
            </div>
            <span
              class="ai-diagnostic-status"
              :class="`ai-diagnostic-status--${selectedDiagnostic.outcome}`"
            >
              {{ diagnosticOutcomeLabel(selectedDiagnostic.outcome) }}
            </span>
          </header>

          <div class="ai-diagnostic-tabs" role="tablist" aria-label="调用详情">
            <button
              v-for="tab in diagnosticTabs"
              :key="tab.value"
              type="button"
              role="tab"
              class="ai-diagnostic-tab"
              :class="{ 'ai-diagnostic-tab--active': diagnosticTab === tab.value }"
              :aria-selected="diagnosticTab === tab.value"
              @click="diagnosticTab = tab.value"
            >
              {{ tab.label }}
              <template v-if="tab.value === 'attachments'">
                {{ selectedDiagnostic.attachments.length }}
              </template>
            </button>
          </div>

          <section v-if="diagnosticTab === 'request'" class="ai-diagnostic-panel" aria-label="发送内容">
            <p>图片正文已替换为附件编号；其余内容是发送给模型的文本和参数。</p>
            <p v-if="requestPreview.truncated" class="ai-diagnostic-panel__notice">
              内容较长，先显示前面 {{ JSON_DISPLAY_LIMIT }} 个字符，以免页面卡住。后面还有
              {{ requestPreview.hiddenChars }} 个字符。
              <button type="button" class="ai-diagnostic-panel__expand" @click="expandRequestJson = true">
                显示全部
              </button>
            </p>
            <pre>{{ requestPreview.text }}</pre>
          </section>
          <section
            v-else-if="diagnosticTab === 'attachments'"
            class="ai-diagnostic-panel"
            aria-label="附件"
          >
            <p v-if="selectedDiagnostic.attachments.length === 0">这次请求没有发送图片附件。</p>
            <div v-else class="ai-diagnostic-attachments">
              <article v-for="attachment in selectedDiagnostic.attachments" :key="attachment.id">
                <strong>{{ attachment.id }} · {{ attachment.purpose }}</strong>
                <span>{{ attachment.mime_type }} · {{ formatBytes(attachment.bytes) }}</span>
                <code>{{ attachment.sha256 }}</code>
              </article>
            </div>
          </section>
          <section
            v-else-if="diagnosticTab === 'response'"
            class="ai-diagnostic-panel"
            aria-label="原始返回"
          >
            <p>
              {{ selectedDiagnostic.response_chars
                ? `${selectedDiagnostic.response_chars} 个字符`
                : '没有收到可显示的文本返回' }}
            </p>
            <p v-if="rawResponsePreview.truncated" class="ai-diagnostic-panel__notice">
              返回正文较长，先显示前面 {{ JSON_DISPLAY_LIMIT }} 个字符。后面还有
              {{ rawResponsePreview.hiddenChars }} 个字符。
              <button type="button" class="ai-diagnostic-panel__expand" @click="expandRawResponse = true">
                显示全部
              </button>
            </p>
            <pre>{{ rawResponsePreview.text }}</pre>
          </section>
          <section
            v-else-if="diagnosticTab === 'parsed'"
            class="ai-diagnostic-panel"
            aria-label="解析结果"
          >
            <p>
              解析状态：{{ selectedDiagnostic.parse_status }}
              <template v-if="selectedDiagnostic.parse_operations.length">
                · 本地修复：{{ selectedDiagnostic.parse_operations.join('、') }}
              </template>
            </p>
            <p v-if="selectedDiagnostic.parse_error" class="ai-diagnostic-panel__error">
              {{ selectedDiagnostic.parse_error }}
            </p>
            <p
              v-if="selectedDiagnostic.validation_issue_codes.length"
              class="ai-diagnostic-panel__error"
            >
              校验原因码：{{ selectedDiagnostic.validation_issue_codes.join('、') }}
            </p>
            <p v-if="parsedPreview.truncated" class="ai-diagnostic-panel__notice">
              解析结果较长，先显示前面 {{ JSON_DISPLAY_LIMIT }} 个字符。后面还有
              {{ parsedPreview.hiddenChars }} 个字符。
              <button type="button" class="ai-diagnostic-panel__expand" @click="expandParsedJson = true">
                显示全部
              </button>
            </p>
            <pre>{{ parsedPreview.text }}</pre>
          </section>
          <section v-else class="ai-diagnostic-panel" aria-label="错误与重试">
            <dl class="ai-diagnostic-retry">
              <div>
                <dt>当前尝试</dt>
                <dd>{{ selectedDiagnostic.retry_index + 1 }} / {{ selectedDiagnostic.retry_limit + 1 }}</dd>
              </div>
              <div>
                <dt>是否继续重试</dt>
                <dd>{{ selectedDiagnostic.will_retry ? '是' : '否' }}</dd>
              </div>
              <div>
                <dt>重试等待</dt>
                <dd>{{ formatDuration(selectedDiagnostic.retry_delay_ms) }}</dd>
              </div>
            </dl>
            <div v-if="selectedDiagnostic.error" class="ai-diagnostic-error-card">
              <strong>
                {{ selectedDiagnostic.error.category }}
                · {{ selectedDiagnostic.error.exception_type }}
              </strong>
              <span v-if="selectedDiagnostic.error.http_status_code">
                HTTP {{ selectedDiagnostic.error.http_status_code }}
              </span>
              <p>{{ selectedDiagnostic.error.message || '没有安全错误正文。' }}</p>
            </div>
            <p v-else>这次调用没有记录到错误。</p>
          </section>
        </template>
      </article>
    </div>
  </section>
</template>
