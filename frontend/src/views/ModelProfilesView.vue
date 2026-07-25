<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
} from 'vue'

import {
  aiDiagnosticsApi,
  type AiDiagnosticDetail,
  type AiDiagnosticOutcome,
  type AiDiagnosticSummary,
} from '../api/ai-diagnostics'
import {
  MODEL_PROFILE_LIMITS,
  type ModelProfile,
  type ModelProfileUpsertInput,
} from '../api/model-profiles'
import { useModelProfilesStore } from '../stores/model-profiles'
import '../styles/model-profiles.css'

interface ModelProfileDraft {
  sourceName: string | null
  name: string
  baseUrl: string
  apiKey: string
  hasApiKey: boolean
  ocrModel: string
  gradingModel: string
  configBaseUrl: string
  configApiKey: string
  hasConfigApiKey: boolean
  configModel: string
}

type DiagnosticTab = 'request' | 'attachments' | 'response' | 'parsed' | 'error'

const profilesStore = useModelProfilesStore()
const formElement = ref<HTMLFormElement | null>(null)
const nameInput = ref<HTMLInputElement | null>(null)
const localError = ref('')
const baseline = ref('')
const diagnostics = ref<AiDiagnosticSummary[]>([])
const diagnosticsState = ref<'idle' | 'loading' | 'error'>('idle')
const diagnosticsError = ref('')
const diagnosticOutcome = ref<'' | AiDiagnosticOutcome>('')
const diagnosticKind = ref('')
const selectedDiagnosticId = ref('')
const selectedDiagnostic = ref<AiDiagnosticDetail | null>(null)
const diagnosticDetailState = ref<'idle' | 'loading' | 'error'>('idle')
const diagnosticTab = ref<DiagnosticTab>('request')
let diagnosticsController: AbortController | null = null
let diagnosticDetailController: AbortController | null = null

const draft = reactive<ModelProfileDraft>({
  sourceName: null,
  name: '',
  baseUrl: '',
  apiKey: '',
  hasApiKey: false,
  ocrModel: '',
  gradingModel: '',
  configBaseUrl: '',
  configApiKey: '',
  hasConfigApiKey: false,
  configModel: '',
})

function draftSnapshot(): string {
  return JSON.stringify({
    sourceName: draft.sourceName,
    name: draft.name,
    baseUrl: draft.baseUrl,
    apiKey: draft.apiKey,
    ocrModel: draft.ocrModel,
    gradingModel: draft.gradingModel,
    configBaseUrl: draft.configBaseUrl,
    configApiKey: draft.configApiKey,
    configModel: draft.configModel,
  })
}

baseline.value = draftSnapshot()

const isNew = computed(() => draft.sourceName === null)
const isDirty = computed(() => draftSnapshot() !== baseline.value)
const isBusy = computed(() => profilesStore.operationState !== 'idle')
const isCurrent = computed(() => (
  draft.sourceName !== null
  && draft.sourceName === profilesStore.activeProfileName
))
const canActivate = computed(() => (
  draft.sourceName !== null
  && !isCurrent.value
  && !isBusy.value
))
const currentProfileLabel = computed(() => (
  profilesStore.activeProfile?.name ?? '尚未选择'
))
const editStatus = computed(() => {
  if (isNew.value) {
    return isDirty.value ? '新配置尚未保存' : '填写后保存为新配置'
  }
  return isDirty.value ? '有未保存修改' : '已与本机保存内容同步'
})
const hasAdvancedConfiguration = computed(() => (
  draft.configBaseUrl !== ''
  || draft.configModel !== ''
  || draft.hasConfigApiKey
))

function applyProfile(profile: ModelProfile): void {
  Object.assign(draft, {
    sourceName: profile.name,
    name: profile.name,
    baseUrl: profile.base_url,
    apiKey: '',
    hasApiKey: profile.has_api_key,
    ocrModel: profile.ocr_model,
    gradingModel: profile.grading_model,
    configBaseUrl: profile.config_base_url,
    configApiKey: '',
    hasConfigApiKey: profile.has_config_api_key,
    configModel: profile.config_model,
  } satisfies ModelProfileDraft)
  localError.value = ''
  baseline.value = draftSnapshot()
}

function applyBlankProfile(): void {
  Object.assign(draft, {
    sourceName: null,
    name: '',
    baseUrl: '',
    apiKey: '',
    hasApiKey: false,
    ocrModel: '',
    gradingModel: '',
    configBaseUrl: '',
    configApiKey: '',
    hasConfigApiKey: false,
    configModel: '',
  } satisfies ModelProfileDraft)
  localError.value = ''
  baseline.value = draftSnapshot()
}

function syncFromSelection(): void {
  if (profilesStore.selectedProfile) {
    applyProfile(profilesStore.selectedProfile)
  } else {
    applyBlankProfile()
  }
}

function confirmDiscard(message: string): boolean {
  return !isDirty.value || window.confirm(message)
}

function chooseProfile(profile: ModelProfile): void {
  if (
    profile.name === draft.sourceName
    || !confirmDiscard('当前表单有未保存修改。切换配置会丢弃这些修改，是否继续？')
  ) return
  profilesStore.selectProfile(profile.name)
  applyProfile(profile)
}

async function beginNewProfile(): Promise<void> {
  if (!confirmDiscard('当前表单有未保存修改。新建配置会丢弃这些修改，是否继续？')) {
    return
  }
  profilesStore.selectProfile(null)
  applyBlankProfile()
  await nextTick()
  nameInput.value?.focus()
}

function toUpsertInput(): ModelProfileUpsertInput {
  return {
    name: draft.name,
    base_url: draft.baseUrl,
    api_key: draft.apiKey,
    ocr_model: draft.ocrModel,
    grading_model: draft.gradingModel,
    config_base_url: draft.configBaseUrl,
    config_api_key: draft.configApiKey,
    config_model: draft.configModel,
  }
}

async function saveProfile(): Promise<void> {
  localError.value = ''
  profilesStore.clearMessages()
  if (!formElement.value?.reportValidity()) return
  if (isNew.value && draft.apiKey.trim() === '') {
    localError.value = '新配置需要填写 API 密钥。'
    return
  }
  const saved = await profilesStore.saveProfile(
    draft.sourceName,
    toUpsertInput(),
  )
  if (saved) applyProfile(saved)
}

async function activateProfile(): Promise<void> {
  if (draft.sourceName === null || !canActivate.value) return
  if (
    isDirty.value
    && !window.confirm(
      '当前表单有未保存修改。切换当前配置会使用已经保存的版本，并丢弃这些修改，是否继续？',
    )
  ) return
  if (isDirty.value) {
    const saved = profilesStore.profiles.find(
      ({ name }) => name === draft.sourceName,
    )
    if (saved) applyProfile(saved)
  }
  await profilesStore.activateProfile(draft.sourceName)
}

async function reloadProfiles(): Promise<void> {
  if (
    !confirmDiscard('重新加载会丢弃当前未保存修改，是否继续？')
  ) return
  const loaded = await profilesStore.load()
  if (loaded) syncFromSelection()
}

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  if (!isDirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

const diagnosticKinds = [
  { value: '', label: '全部用途' },
  { value: 'recognition', label: '识别' },
  { value: 'grading', label: '批改' },
  { value: 'config_generation', label: '评分标准' },
  { value: 'tagging', label: '题库标注' },
] as const
const diagnosticTabs: readonly {
  value: DiagnosticTab
  label: string
}[] = [
  { value: 'request', label: '发送内容' },
  { value: 'attachments', label: '附件' },
  { value: 'response', label: '原始返回' },
  { value: 'parsed', label: '解析结果' },
  { value: 'error', label: '错误与重试' },
]

function selectDiagnosticTab(tab: DiagnosticTab): void {
  diagnosticTab.value = tab
}

function diagnosticKindLabel(value: string): string {
  return diagnosticKinds.find((item) => item.value === value)?.label ?? value
}

function diagnosticOutcomeLabel(value: AiDiagnosticOutcome): string {
  if (value === 'success') return '已返回'
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

async function loadDiagnosticDetail(callId: string): Promise<void> {
  diagnosticDetailController?.abort()
  const controller = new AbortController()
  diagnosticDetailController = controller
  selectedDiagnosticId.value = callId
  selectedDiagnostic.value = null
  diagnosticDetailState.value = 'loading'
  diagnosticTab.value = 'request'
  try {
    selectedDiagnostic.value = await aiDiagnosticsApi.detail(
      callId,
      controller.signal,
    )
    diagnosticDetailState.value = 'idle'
  } catch (error) {
    if (controller.signal.aborted) return
    diagnosticDetailState.value = 'error'
    diagnosticsError.value = error instanceof Error
      ? error.message
      : 'AI 调用详情没有加载成功。'
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
      limit: 60,
      requestKind: diagnosticKind.value,
      outcome: diagnosticOutcome.value,
      signal: controller.signal,
    })
    diagnostics.value = result.items
    diagnosticsState.value = 'idle'
    const selectedStillVisible = result.items.some(
      ({ call_id: callId }) => callId === selectedDiagnosticId.value,
    )
    if (!selectedStillVisible) {
      selectedDiagnosticId.value = ''
      selectedDiagnostic.value = null
      diagnosticDetailState.value = 'idle'
    }
  } catch (error) {
    if (controller.signal.aborted) return
    diagnosticsState.value = 'error'
    diagnosticsError.value = error instanceof Error
      ? error.message
      : 'AI 调用记录没有加载成功。'
  }
}

onMounted(async () => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  const loaded = await profilesStore.load()
  if (loaded) syncFromSelection()
  void loadDiagnostics()
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  diagnosticsController?.abort()
  diagnosticDetailController?.abort()
})
</script>

<template>
  <article class="model-profiles-view">
    <header class="model-profiles-view__header">
      <div>
        <p class="model-profiles-view__eyebrow">LOCAL MODEL ROUTING</p>
        <h1 tabindex="-1">大模型 API 配置</h1>
        <p>按用途保存多套本机配置，需要时切换当前使用的模型服务。</p>
      </div>
      <div class="model-profiles-view__current" aria-live="polite">
        <span class="model-profiles-view__beacon" aria-hidden="true" />
        <span>
          <small>当前配置</small>
          <strong>{{ currentProfileLabel }}</strong>
        </span>
      </div>
    </header>

    <section class="model-profiles-notice" aria-label="费用与密钥说明">
      <p>
        <strong>保存配置不会调用模型，不会产生费用。</strong>
        只有之后真正执行识别、批改或标注任务时，才可能产生模型费用。
      </p>
      <p>
        <strong>密钥只保存在本机且页面无法读回。</strong>
        已保存的密钥只显示“已保存”，不会以明文返回页面。
      </p>
    </section>

    <p
      v-if="profilesStore.errorMessage || localError"
      class="model-profiles-feedback model-profiles-feedback--error"
      role="alert"
    >
      {{ localError || profilesStore.errorMessage }}
    </p>
    <p
      v-else-if="profilesStore.noticeMessage"
      class="model-profiles-feedback model-profiles-feedback--success"
      role="status"
    >
      {{ profilesStore.noticeMessage }}
    </p>

    <section
      v-if="profilesStore.loadState === 'loading'"
      class="model-profiles-state"
      role="status"
    >
      正在读取本机模型配置…
    </section>
    <section
      v-else-if="profilesStore.loadState === 'error'"
      class="model-profiles-state model-profiles-state--error"
    >
      <p>配置列表没有加载成功，页面没有改变任何配置。</p>
      <button
        type="button"
        class="model-profiles-button model-profiles-button--secondary"
        @click="reloadProfiles"
      >
        重新加载
      </button>
    </section>

    <div v-else class="model-profiles-workspace">
      <aside class="model-profiles-index" aria-label="模型配置列表">
        <header>
          <div>
            <p>配置列表</p>
            <span>{{ profilesStore.profiles.length }} 套</span>
          </div>
          <button
            type="button"
            class="model-profiles-button model-profiles-button--quiet"
            :disabled="isBusy"
            @click="beginNewProfile"
          >
            新增配置
          </button>
        </header>

        <p
          v-if="profilesStore.profiles.length === 0"
          class="model-profiles-index__empty"
        >
          还没有模型配置。先在右侧填写第一套配置。
        </p>
        <ul v-else class="model-profiles-index__list">
          <li v-for="profile in profilesStore.profiles" :key="profile.name">
            <button
              type="button"
              class="model-profile-item"
              :class="{
                'model-profile-item--selected': profile.name === draft.sourceName,
              }"
              :aria-current="profile.name === draft.sourceName ? 'true' : undefined"
              :disabled="isBusy"
              @click="chooseProfile(profile)"
            >
              <span class="model-profile-item__heading">
                <strong>{{ profile.name }}</strong>
                <span
                  v-if="profile.name === profilesStore.activeProfileName"
                  class="model-profile-item__active"
                >
                  <i aria-hidden="true" />
                  当前
                </span>
              </span>
              <small>{{ profile.ocr_model }} · {{ profile.grading_model }}</small>
              <span class="model-profile-item__key-state">
                {{ profile.has_api_key ? '主密钥已保存' : '主密钥未保存' }}
              </span>
            </button>
          </li>
        </ul>
      </aside>

      <form
        ref="formElement"
        class="model-profile-editor"
        :aria-busy="isBusy"
        @submit.prevent="saveProfile"
      >
        <header class="model-profile-editor__header">
          <div>
            <p>{{ isNew ? 'NEW LOCAL PROFILE' : 'LOCAL PROFILE' }}</p>
            <h2>{{ isNew ? '新增模型配置' : draft.sourceName }}</h2>
            <span>{{ editStatus }}</span>
          </div>
          <span
            v-if="isCurrent"
            class="model-profile-editor__active"
          >
            当前正在使用
          </span>
        </header>

        <div class="model-profile-fields">
          <label class="model-profile-field model-profile-field--wide">
            <span>配置名称</span>
            <input
              ref="nameInput"
              v-model="draft.name"
              name="profile-name"
              type="text"
              autocomplete="off"
              :maxlength="MODEL_PROFILE_LIMITS.name"
              placeholder="例如：校内批改模型"
              :disabled="isBusy"
              :readonly="!isNew"
              required
            >
            <small v-if="!isNew">已保存配置的名称固定；需要新名称时请新建一套配置。</small>
            <small>用于区分不同服务商、校内代理或模型组合。</small>
          </label>

          <label class="model-profile-field model-profile-field--wide">
            <span>API 地址</span>
            <input
              v-model="draft.baseUrl"
              name="base-url"
              type="url"
              inputmode="url"
              autocomplete="off"
              :maxlength="MODEL_PROFILE_LIMITS.url"
              placeholder="https://api.example.com/v1"
              :disabled="isBusy"
              required
            >
            <small>支持 http:// 或 https://，地址中不要写账号或密码。</small>
          </label>

          <label class="model-profile-field model-profile-field--wide">
            <span>API 密钥</span>
            <input
              v-model="draft.apiKey"
              name="api-key"
              type="password"
              autocomplete="new-password"
              :maxlength="MODEL_PROFILE_LIMITS.apiKey"
              :placeholder="draft.hasApiKey ? '已保存；留空保持不变' : '粘贴 API 密钥'"
              :disabled="isBusy"
              :required="isNew"
            >
            <small>
              {{ draft.hasApiKey
                ? '密钥已保存在本机。页面无法读回；留空不会覆盖。'
                : '尚未保存主密钥。新配置保存前必须填写。' }}
            </small>
          </label>

          <label class="model-profile-field">
            <span>OCR 模型</span>
            <input
              v-model="draft.ocrModel"
              name="ocr-model"
              type="text"
              autocomplete="off"
              :maxlength="MODEL_PROFILE_LIMITS.model"
              placeholder="用于答卷识别的模型"
              :disabled="isBusy"
              required
            >
          </label>

          <label class="model-profile-field">
            <span>批改模型</span>
            <input
              v-model="draft.gradingModel"
              name="grading-model"
              type="text"
              autocomplete="off"
              :maxlength="MODEL_PROFILE_LIMITS.model"
              placeholder="用于评分与反馈的模型"
              :disabled="isBusy"
              required
            >
          </label>
        </div>

        <details
          class="model-profile-advanced"
          :open="hasAdvancedConfiguration"
        >
          <summary>
            <span>
              <strong>考试配置与题库标注</strong>
              <small>高级配置，可与主批改服务分开</small>
            </span>
          </summary>
          <div class="model-profile-fields model-profile-advanced__fields">
            <label class="model-profile-field model-profile-field--wide">
              <span>API 地址</span>
              <input
                v-model="draft.configBaseUrl"
                name="config-base-url"
                type="url"
                inputmode="url"
                autocomplete="off"
                :maxlength="MODEL_PROFILE_LIMITS.url"
                placeholder="留空时使用系统默认安排"
                :disabled="isBusy"
              >
            </label>

            <label class="model-profile-field model-profile-field--wide">
              <span>API 密钥</span>
              <input
                v-model="draft.configApiKey"
                name="config-api-key"
                type="password"
                autocomplete="new-password"
                :maxlength="MODEL_PROFILE_LIMITS.apiKey"
                :placeholder="draft.hasConfigApiKey ? '已保存；留空保持不变' : '可选'"
                :disabled="isBusy"
              >
              <small>
                {{ draft.hasConfigApiKey
                  ? '高级配置密钥已保存；页面无法读回。'
                  : '如不填写，将不新增或替换高级配置密钥。' }}
              </small>
            </label>

            <label class="model-profile-field model-profile-field--wide">
              <span>模型</span>
              <input
                v-model="draft.configModel"
                name="config-model"
                type="text"
                autocomplete="off"
                :maxlength="MODEL_PROFILE_LIMITS.model"
                placeholder="用于考试配置与题库标注的模型"
                :disabled="isBusy"
              >
            </label>
          </div>
        </details>

        <footer class="model-profile-editor__actions">
          <div>
            <strong>{{ editStatus }}</strong>
            <span>保存和切换都只修改本机设置，不会测试连接。</span>
          </div>
          <div class="model-profile-editor__buttons">
            <button
              type="button"
              class="model-profiles-button model-profiles-button--secondary"
              :disabled="!canActivate"
              @click="activateProfile"
            >
              {{ profilesStore.operationState === 'activating'
                ? '正在切换…'
                : isCurrent ? '当前配置' : '设为当前配置' }}
            </button>
            <button
              type="submit"
              class="model-profiles-button model-profiles-button--primary"
              :disabled="isBusy || !isDirty"
            >
              {{ profilesStore.operationState === 'saving'
                ? '正在保存…'
                : '保存配置' }}
            </button>
          </div>
        </footer>
      </form>
    </div>

    <section class="ai-diagnostics" aria-labelledby="ai-diagnostics-title">
      <header class="ai-diagnostics__header">
        <div>
          <p class="model-profiles-view__eyebrow">LOCAL AI TRACE</p>
          <h2 id="ai-diagnostics-title">AI 调用记录</h2>
          <p>还原每次发送、等待、返回和解析过程，便于定位识别与评分问题。</p>
        </div>
        <div class="ai-diagnostics__filters">
          <label>
            <span>用途</span>
            <select v-model="diagnosticKind" @change="loadDiagnostics">
              <option
                v-for="kind in diagnosticKinds"
                :key="kind.value"
                :value="kind.value"
              >
                {{ kind.label }}
              </option>
            </select>
          </label>
          <label>
            <span>结果</span>
            <select v-model="diagnosticOutcome" @change="loadDiagnostics">
              <option value="">全部结果</option>
              <option value="success">已返回</option>
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

      <p class="ai-diagnostics__privacy">
        <strong>本机敏感记录。</strong>
        日志包含实际文本请求和模型原始响应；只有图片正文不落盘，仅记录用途、
        大小和指纹。记录只保存在本机 <code>logs/</code>，该目录已被 Git 忽略，
        不会纳入代码提交；单个文件约 32 MB 时滚动，保留当前文件和最近 3 个旧文件。
        密钥和本机文件路径不会写入。
      </p>

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
            <span>{{ diagnostics.length }} 条</span>
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
            还没有符合当前筛选条件的调用。执行一次识别、批改或标注后再刷新。
          </p>
          <ul v-else>
            <li v-for="call in diagnostics" :key="call.call_id">
              <button
                type="button"
                class="ai-diagnostic-call"
                :class="{
                  'ai-diagnostic-call--selected':
                    call.call_id === selectedDiagnosticId,
                }"
                :aria-current="
                  call.call_id === selectedDiagnosticId ? 'true' : undefined
                "
                @click="loadDiagnosticDetail(call.call_id)"
              >
                <span class="ai-diagnostic-call__heading">
                  <strong>{{ diagnosticKindLabel(call.request_kind) }}</strong>
                  <span
                    class="ai-diagnostic-status"
                    :class="`ai-diagnostic-status--${call.outcome}`"
                  >
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
        </aside>

        <article class="ai-diagnostic-detail" aria-live="polite">
          <div
            v-if="diagnosticDetailState === 'loading'"
            class="ai-diagnostic-detail__state"
          >
            正在读取这次调用的完整记录…
          </div>
          <div
            v-else-if="diagnosticDetailState === 'error'"
            class="ai-diagnostic-detail__state ai-diagnostic-detail__state--error"
          >
            {{ diagnosticsError || '这次调用的详情没有加载成功。' }}
          </div>
          <div
            v-else-if="selectedDiagnostic === null"
            class="ai-diagnostic-detail__empty"
          >
            <strong>选择一条调用记录</strong>
            <p>可以逐项查看实际发送文本、附件指纹、模型原始返回和解析结果。</p>
          </div>
          <template v-else>
            <header class="ai-diagnostic-detail__header">
              <div>
                <span>
                  {{ diagnosticKindLabel(selectedDiagnostic.request_kind) }}
                  · 第 {{ selectedDiagnostic.attempt }} 次尝试
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

            <dl class="ai-diagnostic-facts">
              <div>
                <dt>开始时间</dt>
                <dd>{{ formatDiagnosticTime(selectedDiagnostic.started_at_utc) }}</dd>
              </div>
              <div>
                <dt>模型等待</dt>
                <dd>{{ formatDuration(selectedDiagnostic.elapsed_ms) }}</dd>
              </div>
              <div>
                <dt>协议</dt>
                <dd>{{ selectedDiagnostic.protocol }}</dd>
              </div>
              <div>
                <dt>服务地址</dt>
                <dd>{{ selectedDiagnostic.endpoint_host || '未记录' }}</dd>
              </div>
            </dl>

            <div class="ai-diagnostic-tabs" role="tablist" aria-label="调用详情">
              <button
                v-for="tab in diagnosticTabs"
                :key="tab.value"
                type="button"
                role="tab"
                class="ai-diagnostic-tab"
                :class="{ 'ai-diagnostic-tab--active': diagnosticTab === tab.value }"
                :aria-selected="diagnosticTab === tab.value"
                @click="selectDiagnosticTab(tab.value)"
              >
                {{ tab.label }}
                <template v-if="tab.value === 'attachments'">
                  {{ selectedDiagnostic.attachments.length }}
                </template>
              </button>
            </div>

            <section
              v-if="diagnosticTab === 'request'"
              class="ai-diagnostic-panel"
              aria-label="发送内容"
            >
              <p>图片正文已替换为附件编号；其余内容是发送给模型的文本和参数。</p>
              <pre>{{ formatDiagnosticJson(selectedDiagnostic.request) }}</pre>
            </section>

            <section
              v-else-if="diagnosticTab === 'attachments'"
              class="ai-diagnostic-panel"
              aria-label="附件"
            >
              <p v-if="selectedDiagnostic.attachments.length === 0">
                这次请求没有发送图片附件。
              </p>
              <div v-else class="ai-diagnostic-attachments">
                <article
                  v-for="attachment in selectedDiagnostic.attachments"
                  :key="attachment.id"
                >
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
              <pre>{{ selectedDiagnostic.raw_response || '暂无原始返回' }}</pre>
            </section>

            <section
              v-else-if="diagnosticTab === 'parsed'"
              class="ai-diagnostic-panel"
              aria-label="解析结果"
            >
              <p>
                解析状态：{{ selectedDiagnostic.parse_status }}
                <template v-if="selectedDiagnostic.parse_operations.length">
                  · 本地修复：
                  {{ selectedDiagnostic.parse_operations.join('、') }}
                </template>
              </p>
              <p
                v-if="selectedDiagnostic.parse_error"
                class="ai-diagnostic-panel__error"
              >
                {{ selectedDiagnostic.parse_error }}
              </p>
              <pre>{{ formatDiagnosticJson(selectedDiagnostic.parsed_result) }}</pre>
            </section>

            <section
              v-else
              class="ai-diagnostic-panel"
              aria-label="错误与重试"
            >
              <dl class="ai-diagnostic-retry">
                <div>
                  <dt>当前尝试</dt>
                  <dd>
                    {{ selectedDiagnostic.retry_index + 1 }}
                    / {{ selectedDiagnostic.retry_limit + 1 }}
                  </dd>
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
              <div
                v-if="selectedDiagnostic.error"
                class="ai-diagnostic-error-card"
              >
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
  </article>
</template>
