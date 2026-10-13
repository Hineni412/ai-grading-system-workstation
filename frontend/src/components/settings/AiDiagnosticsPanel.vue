<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  aiDiagnosticsApi,
  type AiDiagnosticDetail,
  type AiDiagnosticOutcome,
  type AiDiagnosticSummary,
} from '../../api/ai-diagnostics'
import AppButton from '../design-system/AppButton.vue'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import StatePanel from '../design-system/StatePanel.vue'
import StatusBadge from '../design-system/StatusBadge.vue'
import { formatBytes } from '../../lib/format'
import '../../styles/model-profiles.css'

const DIAGNOSTIC_PAGE_SIZE = 100
const PREVIEW_CHAR_LIMIT = 4_000

const kindOptions = [
  { value: '', label: '全部' },
  { value: 'recognition', label: '识别' },
  { value: 'grading', label: '批改与训练判定' },
  { value: 'tagging', label: '题库资料与评分标准' },
  { value: 'config_generation', label: '报告与补答案' },
] as const
const kindLabels: Record<string, string> = {
  recognition: '识别',
  grading: '批改与训练判定',
  tagging: '题库资料与评分标准',
  config_generation: '报告与补答案',
  assembly: 'AI 组卷（旧记录）',
  workspace: '班主任工作台（旧记录）',
}
const outcomeOptions = [
  { value: '', label: '全部' },
  { value: 'failure', label: '失败' },
  { value: 'pending', label: '等待中' },
  { value: 'success', label: '成功' },
] as const
const outcomeMeta = {
  success: { tone: 'success', label: '成功' },
  failure: { tone: 'danger', label: '失败' },
  pending: { tone: 'warning', label: '等待中' },
} as const
const diagnosticTabs = [
  { key: 'request', label: '发送内容' },
  { key: 'attachments', label: '附件' },
  { key: 'response', label: '返回内容' },
  { key: 'parsed', label: '解析结果' },
  { key: 'error', label: '错误与重试' },
] as const

const items = ref<AiDiagnosticSummary[]>([])
const returned = ref(0)
const matching = ref(0)
const loadState = ref<'idle' | 'loading' | 'ready' | 'empty' | 'error'>('idle')
const errorMessage = ref('')
const kind = ref('')
const outcome = ref<'' | AiDiagnosticOutcome>('')
const selectedId = ref('')
const selected = ref<AiDiagnosticDetail | null>(null)
const detailState = ref<'idle' | 'loading' | 'error'>('idle')
const detailError = ref('')
const diagnosticTab = ref<(typeof diagnosticTabs)[number]['key']>('request')
const expandRequestJson = ref(false)
const expandParsedJson = ref(false)
const expandRawResponse = ref(false)
const scopeOpen = ref(false)
const copiedTab = ref('')
let copyTimer: ReturnType<typeof setTimeout> | undefined
let listController: AbortController | null = null
let listGeneration = 0
let detailController: AbortController | null = null
let detailGeneration = 0

const counts = computed(() => {
  const result = { success: 0, failure: 0, pending: 0 }
  for (const item of items.value) result[item.outcome] += 1
  return result
})

interface DayGroup { key: string; label: string; entries: AiDiagnosticSummary[] }
const dayGroups = computed<DayGroup[]>(() => {
  const map = new Map<string, DayGroup>()
  for (const item of items.value) {
    const key = dayKey(item.started_at_utc)
    let group = map.get(key)
    if (!group) {
      group = { key, label: dayLabel(key), entries: [] }
      map.set(key, group)
    }
    group.entries.push(item)
  }
  return [...map.values()]
})

function dayKey(iso: string): string {
  const date = new Date(iso)
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`
}
function dayLabel(key: string): string {
  const [year, month, day] = key.split('-').map(Number)
  const sameDay = (date: Date) => (
    date.getFullYear() === year && date.getMonth() + 1 === month && date.getDate() === day
  )
  const now = new Date()
  if (sameDay(now)) return '今天'
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (sameDay(yesterday)) return '昨天'
  return `${month}月${day}日`
}
function timeOfDay(iso: string): string {
  const date = new Date(iso)
  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map(n => String(n).padStart(2, '0')).join(':')
}
function kindLabel(value: string): string {
  return kindLabels[value] ?? value
}
function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${milliseconds} 毫秒`
  return `${(milliseconds / 1_000).toFixed(milliseconds < 10_000 ? 1 : 0)} 秒`
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
function rowMeta(item: AiDiagnosticSummary): string {
  const parts = [timeOfDay(item.started_at_utc), formatDuration(item.elapsed_ms)]
  if (item.image_count > 0) parts.push(`${item.image_count} 张图片`)
  if (item.attempt > 1) parts.push(`第 ${item.attempt} 次尝试`)
  return parts.join(' · ')
}
const detailMetaCells = computed(() => {
  const detail = selected.value
  if (!detail) return []
  return [
    { label: '服务地址', value: detail.endpoint_host },
    { label: '尝试', value: `${detail.retry_index + 1} / ${detail.retry_limit + 1}` },
    { label: '图片', value: `${detail.image_count} 张` },
    { label: '返回', value: `${detail.response_chars} 字符` },
  ]
})

function formatDiagnosticJson(value: unknown): string {
  if (value === null || value === undefined) return '暂无内容'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return '内容无法转换为可读文本'
  }
}
const requestJson = computed(() => (
  selected.value ? formatDiagnosticJson(selected.value.request) : ''
))
const parsedJson = computed(() => (
  selected.value ? formatDiagnosticJson(selected.value.parsed_result) : ''
))
const rawResponseText = computed(() => selected.value?.raw_response || '暂无原始返回')
function diagnosticTextPreview(
  text: string,
  expanded: boolean,
): { text: string; truncated: boolean; hiddenChars: number } {
  if (!text) return { text: '暂无内容', truncated: false, hiddenChars: 0 }
  if (expanded || text.length <= PREVIEW_CHAR_LIMIT) {
    return { text, truncated: false, hiddenChars: 0 }
  }
  return {
    text: text.slice(0, PREVIEW_CHAR_LIMIT),
    truncated: true,
    hiddenChars: text.length - PREVIEW_CHAR_LIMIT,
  }
}
const requestPreview = computed(() => diagnosticTextPreview(requestJson.value, expandRequestJson.value))
const parsedPreview = computed(() => diagnosticTextPreview(parsedJson.value, expandParsedJson.value))
const rawResponsePreview = computed(() => diagnosticTextPreview(rawResponseText.value, expandRawResponse.value))
function copySource(key: string): string {
  if (key === 'request') return requestJson.value
  if (key === 'parsed') return parsedJson.value
  return rawResponseText.value
}
async function copyTab(key: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(copySource(key))
    copiedTab.value = key
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copiedTab.value = '' }, 1600)
  } catch { /* 剪贴板不可用时静默 */ }
}

async function loadDiagnostics(): Promise<void> {
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  const generation = ++listGeneration
  loadState.value = 'loading'
  errorMessage.value = ''
  try {
    const list = await aiDiagnosticsApi.list({
      limit: DIAGNOSTIC_PAGE_SIZE,
      requestKind: kind.value || undefined,
      outcome: outcome.value,
      signal: controller.signal,
    })
    if (generation !== listGeneration || controller.signal.aborted) return
    items.value = list.items
    returned.value = list.returned
    matching.value = list.matching
    loadState.value = list.items.length === 0 ? 'empty' : 'ready'
    if (!list.items.some(item => item.call_id === selectedId.value)) clearSelected()
  } catch (error) {
    if (generation !== listGeneration || controller.signal.aborted) return
    items.value = []
    returned.value = 0
    matching.value = 0
    loadState.value = 'error'
    errorMessage.value = error instanceof Error ? error.message : '调用记录读取失败'
  } finally {
    if (listController === controller) listController = null
  }
}

function clearSelected(): void {
  detailController?.abort()
  detailGeneration += 1
  selectedId.value = ''
  selected.value = null
  detailState.value = 'idle'
  detailError.value = ''
  expandRequestJson.value = false
  expandParsedJson.value = false
  expandRawResponse.value = false
}

async function selectDiagnostic(callId: string): Promise<void> {
  detailController?.abort()
  const controller = new AbortController()
  detailController = controller
  const generation = ++detailGeneration
  selectedId.value = callId
  selected.value = null
  detailError.value = ''
  expandRequestJson.value = false
  expandParsedJson.value = false
  expandRawResponse.value = false
  detailState.value = 'loading'
  try {
    const detail = await aiDiagnosticsApi.detail(callId, controller.signal)
    if (generation !== detailGeneration || controller.signal.aborted) return
    selected.value = detail
    diagnosticTab.value = detail.outcome === 'failure' ? 'error' : 'request'
    detailState.value = 'idle'
  } catch (error) {
    if (generation !== detailGeneration || controller.signal.aborted) return
    detailError.value = error instanceof Error ? error.message : ''
    detailState.value = 'error'
  } finally {
    if (detailController === controller) detailController = null
  }
}

function showFailures(): void {
  outcome.value = 'failure'
  void loadDiagnostics()
}

onMounted(() => { void loadDiagnostics() })
onBeforeUnmount(() => {
  listController?.abort()
  detailController?.abort()
  clearTimeout(copyTimer)
})
</script>
<template>
  <section class="settings-panel ai-diagnostics" aria-label="AI 调用记录">
    <header class="ai-diagnostics__toolbar">
      <div class="app-segmented" role="group" aria-label="用途筛选">
        <button
          v-for="option in kindOptions"
          :key="option.value"
          type="button"
          class="app-segmented__button"
          :class="{ 'is-active': kind === option.value }"
          :aria-pressed="kind === option.value"
          @click="kind = option.value; void loadDiagnostics()"
        >{{ option.label }}</button>
      </div>
      <div class="ai-diagnostics__toolbar-side">
        <div class="app-segmented" role="group" aria-label="结果筛选">
          <button
            v-for="option in outcomeOptions"
            :key="option.value"
            type="button"
            class="app-segmented__button"
            :class="{ 'is-active': outcome === option.value }"
            :aria-pressed="outcome === option.value"
            @click="outcome = option.value; void loadDiagnostics()"
          >{{ option.label }}</button>
        </div>
        <AppButton variant="secondary" size="small" :disabled="loadState === 'loading'" @click="void loadDiagnostics()">刷新</AppButton>
        <div class="ai-diagnostics__scope">
          <AppButton variant="ghost" size="small" :aria-expanded="scopeOpen" aria-controls="ai-diagnostics-scope" @click="scopeOpen = !scopeOpen">记录范围</AppButton>
          <div v-if="scopeOpen" id="ai-diagnostics-scope" class="ai-diagnostics__scope-pop ai-diagnostics__privacy">
            <p><strong>“成功”只表示请求完成。</strong>发送正文只留在本机调用日志里，不会写入任务摘要。</p>
            <p><strong>本机唯一正文日志。</strong>文本请求、文本响应与解析／校验原因只写入 <code>logs/llm_diagnostics.jsonl</code>；不会复制到终端、访问日志、任务摘要或浏览器存储，也不会进入 Git 或普通备份。API 密钥、Authorization、Cookie、密码、访问／刷新令牌，以及附件、图片、音频、长 base64 和本机绝对路径不会保留正文。每条最多 1 MB。这里只列出最近 100 次调用；更早的记录仍保存在 logs/llm_diagnostics.jsonl 及其滚动文件里。</p>
          </div>
        </div>
      </div>
    </header>
    <p v-if="loadState === 'ready' || loadState === 'empty'" class="ai-diagnostics__summary">列出 {{ returned }} 次 · 成功 {{ counts.success }} · <button v-if="counts.failure" type="button" class="ai-diagnostics__summary-failures" @click="showFailures">失败 {{ counts.failure }}</button><template v-else>失败 0</template> · 等待中 {{ counts.pending }}<template v-if="matching > returned"> · 共匹配 {{ matching }} 次，只列最近 100 次</template></p>
    <div class="ai-diagnostic">
      <div class="ai-diagnostic__sidebar">
        <StatePanel v-if="loadState === 'loading'" kind="loading" compact title="正在读取本机调用记录…" />
        <StatePanel v-else-if="loadState === 'error'" kind="error" compact title="调用记录暂时无法读取" :description="errorMessage" retry-label="重新加载" @retry="void loadDiagnostics()" />
        <StatePanel v-else-if="loadState === 'empty'" kind="empty" compact title="当前没有符合条件的调用记录" />
        <div v-else class="ai-diagnostic__ledger">
          <section v-for="group in dayGroups" :key="group.key" class="ai-diagnostic__day">
            <h3 class="ai-diagnostic__day-label">{{ group.label }}</h3>
            <button
              v-for="item in group.entries"
              :key="item.call_id"
              type="button"
              class="ai-diagnostic__entry"
              :class="{ 'is-active': selectedId === item.call_id, 'ai-diagnostic__entry--failure': item.outcome === 'failure' }"
              :aria-current="selectedId === item.call_id ? 'true' : undefined"
              @click="void selectDiagnostic(item.call_id)"
            >
              <span class="ai-diagnostic__entry-head"><strong>{{ kindLabel(item.request_kind) }}</strong><StatusBadge :tone="outcomeMeta[item.outcome].tone" :label="outcomeMeta[item.outcome].label" /></span>
              <span class="ai-diagnostic__entry-model">{{ item.model || '模型未记录' }}</span>
              <span class="ai-diagnostic__entry-meta">{{ rowMeta(item) }}</span>
            </button>
          </section>
        </div>
      </div>
      <div class="ai-diagnostic__content">
        <StatePanel v-if="detailState === 'loading'" kind="loading" title="正在读取这次调用的完整记录…" />
        <StatePanel v-else-if="detailState === 'error'" kind="error" :title="detailError || '这次调用的详情没有加载成功。'" />
        <StatePanel v-else-if="!selected" kind="empty" title="选择左侧一条调用记录" />
        <template v-else>
          <header class="ai-diagnostic__head">
            <div class="ai-diagnostic__head-text">
              <p class="ai-diagnostic__meta-line">{{ kindLabel(selected.request_kind) }} · {{ formatDiagnosticTime(selected.started_at_utc) }} · {{ formatDuration(selected.elapsed_ms) }} · {{ selected.protocol }}</p>
              <h3>{{ selected.model || '模型未记录' }}</h3>
            </div>
            <StatusBadge :tone="outcomeMeta[selected.outcome].tone" :label="outcomeMeta[selected.outcome].label" />
          </header>
          <div class="ai-diagnostic__meta-grid">
            <div v-for="cell in detailMetaCells" :key="cell.label" class="ai-diagnostic__meta-cell"><span>{{ cell.label }}</span><strong>{{ cell.value }}</strong></div>
          </div>
          <div class="app-segmented ai-diagnostic__tabs" role="tablist" aria-label="调用详情">
            <button
              v-for="tab in diagnosticTabs"
              :key="tab.key"
              type="button"
              role="tab"
              class="app-segmented__button"
              :class="{ 'is-active': diagnosticTab === tab.key }"
              :aria-selected="diagnosticTab === tab.key"
              @click="diagnosticTab = tab.key"
            >{{ tab.key === 'attachments' ? `附件 ${selected.attachments.length}` : tab.label }}</button>
          </div>
          <div v-if="diagnosticTab === 'request'" class="ai-diagnostic__body">
            <p class="ai-diagnostic__note">图片正文已替换为附件编号；其余内容是发送给模型的文本和参数。</p>
            <div class="ai-diagnostic__body-tools"><AppButton variant="ghost" size="small" @click="void copyTab('request')">{{ copiedTab === 'request' ? '已复制' : '复制' }}</AppButton></div>
            <FeedbackBanner v-if="requestPreview.truncated" tone="info" :description="'内容较长，已折叠，只显示前 ' + PREVIEW_CHAR_LIMIT + ' 个字符；后面还有 ' + requestPreview.hiddenChars + ' 个字符。'" action-label="展开全部" @action="expandRequestJson = true" />
            <pre>{{ requestPreview.text }}</pre>
          </div>
          <div v-else-if="diagnosticTab === 'attachments'" class="ai-diagnostic__body">
            <p v-if="selected.attachments.length === 0" class="ai-diagnostic__note">这次请求没有发送图片附件。</p>
            <div v-else class="ai-diagnostic__attachments">
              <article v-for="attachment in selected.attachments" :key="attachment.id" class="ai-diagnostic__attachment">
                <strong>{{ attachment.id }} · {{ attachment.purpose }}</strong>
                <span>{{ attachment.mime_type }} · {{ formatBytes(attachment.bytes) }}</span>
                <code>{{ attachment.sha256 }}</code>
              </article>
            </div>
          </div>
          <div v-else-if="diagnosticTab === 'response'" class="ai-diagnostic__body">
            <p class="ai-diagnostic__note">{{ selected.response_chars ? `${selected.response_chars} 个字符` : '没有收到可显示的文本返回' }}</p>
            <div class="ai-diagnostic__body-tools"><AppButton variant="ghost" size="small" @click="void copyTab('response')">{{ copiedTab === 'response' ? '已复制' : '复制' }}</AppButton></div>
            <FeedbackBanner v-if="rawResponsePreview.truncated" tone="info" :description="'返回正文较长，已折叠，只显示前 ' + PREVIEW_CHAR_LIMIT + ' 个字符；后面还有 ' + rawResponsePreview.hiddenChars + ' 个字符。'" action-label="展开全部" @action="expandRawResponse = true" />
            <pre>{{ rawResponsePreview.text }}</pre>
          </div>
          <div v-else-if="diagnosticTab === 'parsed'" class="ai-diagnostic__body">
            <p class="ai-diagnostic__note">解析状态：{{ selected.parse_status }}<template v-if="selected.parse_operations.length"> · 本地修复：{{ selected.parse_operations.join('、') }}</template></p>
            <p v-if="selected.parse_error" class="ai-diagnostic__error-text">{{ selected.parse_error }}</p>
            <p v-if="selected.validation_issue_codes.length" class="ai-diagnostic__error-text">校验原因码：{{ selected.validation_issue_codes.join('、') }}</p>
            <div class="ai-diagnostic__body-tools"><AppButton variant="ghost" size="small" @click="void copyTab('parsed')">{{ copiedTab === 'parsed' ? '已复制' : '复制' }}</AppButton></div>
            <FeedbackBanner v-if="parsedPreview.truncated" tone="info" :description="'解析结果较长，已折叠，只显示前 ' + PREVIEW_CHAR_LIMIT + ' 个字符；后面还有 ' + parsedPreview.hiddenChars + ' 个字符。'" action-label="展开全部" @action="expandParsedJson = true" />
            <pre>{{ parsedPreview.text }}</pre>
          </div>
          <div v-else class="ai-diagnostic__body">
            <dl class="ai-diagnostic__parse">
              <div><dt>当前尝试</dt><dd>{{ selected.retry_index + 1 }} / {{ selected.retry_limit + 1 }}</dd></div>
              <div><dt>是否继续重试</dt><dd>{{ selected.will_retry ? '是' : '否' }}</dd></div>
              <div><dt>重试等待</dt><dd>{{ formatDuration(selected.retry_delay_ms) }}</dd></div>
            </dl>
            <div v-if="selected.error" class="ai-diagnostic__error-card">
              <strong>{{ selected.error.category }} · {{ selected.error.exception_type }}</strong>
              <span v-if="selected.error.http_status_code">HTTP {{ selected.error.http_status_code }}</span>
              <p>{{ selected.error.message || '没有安全错误正文。' }}</p>
            </div>
            <p v-else class="ai-diagnostic__note">这次调用没有记录到错误。</p>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>
