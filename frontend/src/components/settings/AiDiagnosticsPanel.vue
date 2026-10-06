<script setup lang="ts">
import AppButton from '@/components/design-system/AppButton.vue'
import FeedbackBanner from '@/components/design-system/FeedbackBanner.vue'
import StatePanel from '@/components/design-system/StatePanel.vue'

import { computed, markRaw, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'

import {
  aiDiagnosticsApi,
  type AiDiagnosticDetail,
  type AiDiagnosticOutcome,
  type AiDiagnosticSummary,
} from '../../api/ai-diagnostics'
import { formatBytes } from '../../lib/format'
import '../../styles/model-profiles.css'

type DiagnosticTab = 'request' | 'attachments' | 'response' | 'parsed' | 'error'

const DIAGNOSTIC_PAGE_SIZE = 20

const diagnostics = ref<AiDiagnosticSummary[]>([])
const diagnosticsState = ref<'idle' | 'loading' | 'error'>('idle')
const diagnosticsError = ref('')
const diagnosticsNotice = ref('')
const diagnosticsMatching = ref(0)
const diagnosticsLoadingMore = ref(false)
const diagnosticOutcome = ref<'' | AiDiagnosticOutcome>('')
const diagnosticKind = ref('')
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

const diagnosticKinds = [
  { value: '', label: '全部用途' },
  { value: 'recognition', label: '识别' },
  { value: 'grading', label: '批改' },
  { value: 'config_generation', label: '评分标准' },
  { value: 'tagging', label: '题库标注' },
  { value: 'workspace', label: '工作台' },
] as const
const diagnosticTabs: readonly { value: DiagnosticTab; label: string }[] = [
  { value: 'request', label: '发送内容' },
  { value: 'attachments', label: '附件' },
  { value: 'response', label: '返回内容' },
  { value: 'parsed', label: '解析结果' },
  { value: 'error', label: '错误与重试' },
]

function diagnosticKindLabel(value: string): string {
  return diagnosticKinds.find((item) => item.value === value)?.label ?? value
}

function diagnosticDisplayLabel(call: AiDiagnosticSummary): string {
  return diagnosticKindLabel(call.request_kind)
}

function diagnosticOutcomeLabel(value: AiDiagnosticOutcome): string {
  if (value === 'success') return '成功'
  if (value === 'failure') return '失败'
  return '等待中'
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

const hasMoreDiagnostics = computed(
  () => diagnostics.value.length < diagnosticsMatching.value,
)

async function loadDiagnostics(): Promise<void> {
  diagnosticsController?.abort()
  const controller = new AbortController()
  diagnosticsController = controller
  diagnosticsState.value = 'loading'
  diagnosticsError.value = ''
  try {
    const result = await aiDiagnosticsApi.list({
      limit: DIAGNOSTIC_PAGE_SIZE,
      requestKind: diagnosticKind.value,
      outcome: diagnosticOutcome.value,
      signal: controller.signal,
    })
    diagnostics.value = result.items
    diagnosticsMatching.value = result.matching
    diagnosticsState.value = 'idle'
    const selectedStillVisible = diagnostics.value.some(
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

async function loadMoreDiagnostics(): Promise<void> {
  if (diagnosticsLoadingMore.value || diagnosticsState.value === 'loading') return
  diagnosticsController?.abort()
  const controller = new AbortController()
  diagnosticsController = controller
  diagnosticsLoadingMore.value = true
  diagnosticsError.value = ''
  try {
    const result = await aiDiagnosticsApi.list({
      limit: DIAGNOSTIC_PAGE_SIZE,
      offset: diagnostics.value.length,
      requestKind: diagnosticKind.value,
      outcome: diagnosticOutcome.value,
      signal: controller.signal,
    })
    const seen = new Set(diagnostics.value.map(({ call_id }) => call_id))
    diagnostics.value = [
      ...diagnostics.value,
      ...result.items.filter(({ call_id }) => !seen.has(call_id)),
    ]
    diagnosticsMatching.value = result.matching
  } catch (error) {
    if (controller.signal.aborted) return
    diagnosticsError.value = error instanceof Error
      ? error.message
      : '更早的调用记录没有加载成功。'
  } finally {
    if (diagnosticsController === controller) diagnosticsController = null
    diagnosticsLoadingMore.value = false
  }
}

onMounted(() => {
  void loadDiagnostics()
})

onBeforeUnmount(() => {
  diagnosticsController?.abort()
  diagnosticDetailController?.abort()
})
</script>

<template>
  <section class="ai-diagnostics" aria-label="AI 调用记录">
    <header class="ai-diagnostics__header">
      <div class="ai-diagnostics__filters">
        <label>
          <span>用途</span>
          <select class="app-input" v-model="diagnosticKind" @change="loadDiagnostics">
            <option v-for="kind in diagnosticKinds" :key="kind.value" :value="kind.value">
              {{ kind.label }}
            </option>
          </select>
        </label>
        <label>
          <span>结果</span>
          <select class="app-input" v-model="diagnosticOutcome" @change="loadDiagnostics">
            <option value="">全部结果</option>
            <option value="success">成功</option>
            <option value="failure">失败</option>
            <option value="pending">等待中</option>
          </select>
        </label>
        <AppButton variant="secondary"
          type="button"
          class="model-profiles-button model-profiles-button--secondary"
          :disabled="diagnosticsState === 'loading'"
          @click="loadDiagnostics"
        >
          {{ diagnosticsState === 'loading' ? '正在刷新…' : '刷新记录' }}
        </AppButton>
      </div>
    </header>

    <p v-if="diagnosticsNotice" class="ai-diagnostics__privacy" role="status">
      {{ diagnosticsNotice }}
    </p>

    <details class="ai-diagnostics-disclosure settings-disclosure">
      <summary>了解记录范围</summary>
    <p class="ai-diagnostics__privacy">
      <strong>“成功”只表示请求完成。</strong>
      发送正文只留在本机调用日志里，不会写入任务摘要。
    </p>

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
          <span>{{ diagnostics.length }} / {{ diagnosticsMatching }} 条</span>
        </header>
        <p
          v-if="diagnosticsState === 'loading' && diagnostics.length === 0"
          class="ai-diagnostics-ledger__state"
        >
          正在读取本机记录…
        </p>
        <p
          v-else-if="diagnostics.length === 0"
          class="ai-diagnostics-ledger__state"
        >
          还没有符合当前筛选条件的调用。执行相应功能后再刷新。
        </p>
        <ul v-else>
          <li v-for="call in diagnostics" :key="call.call_id">
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
              <span class="ai-diagnostic-call__meta">
                <time>{{ formatDiagnosticTime(call.started_at_utc) }}</time>
                <span>{{ formatDuration(call.elapsed_ms) }}</span>
                <span v-if="call.image_count">{{ call.image_count }} 张图片</span>
                <span v-if="call.attempt > 1">第 {{ call.attempt }} 次尝试</span>
              </span>
            </button>
          </li>
        </ul>
        <button
          v-if="hasMoreDiagnostics"
          type="button"
          class="ai-diagnostics-ledger__more"
          :disabled="diagnosticsLoadingMore"
          @click="loadMoreDiagnostics"
        >
          {{ diagnosticsLoadingMore ? '正在读取更早的记录…' : `加载更早（还有 ${diagnosticsMatching - diagnostics.length} 条）` }}
        </button>
      </aside>

      <article class="ai-diagnostic-detail" aria-live="polite">
        <StatePanel
          v-if="diagnosticDetailState === 'loading'"
          kind="loading"
          title="正在读取这次调用的完整记录…"
        />
        <StatePanel
          v-else-if="diagnosticDetailState === 'error'"
          kind="error"
          :title="diagnosticsError || '这次调用的详情没有加载成功。'"
        />
        <StatePanel
          v-else-if="selectedDiagnostic === null"
          kind="empty"
          title="选择一条调用记录"
        />
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
            <FeedbackBanner v-if="requestPreview.truncated" tone="info"
              :description="'内容较长，先显示前面 ' + JSON_DISPLAY_LIMIT + ' 个字符，以免页面卡住。后面还有 ' + requestPreview.hiddenChars + ' 个字符。'"
              action-label="显示全部" @action="expandRequestJson = true" />
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
            <FeedbackBanner v-if="rawResponsePreview.truncated" tone="info"
              :description="'返回正文较长，先显示前面 ' + JSON_DISPLAY_LIMIT + ' 个字符。后面还有 ' + rawResponsePreview.hiddenChars + ' 个字符。'"
              action-label="显示全部" @action="expandRawResponse = true" />
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
            <FeedbackBanner v-if="parsedPreview.truncated" tone="info"
              :description="'解析结果较长，先显示前面 ' + JSON_DISPLAY_LIMIT + ' 个字符。后面还有 ' + parsedPreview.hiddenChars + ' 个字符。'"
              action-label="显示全部" @action="expandParsedJson = true" />
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
