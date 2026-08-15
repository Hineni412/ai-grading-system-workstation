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
  copyModelTaskBindings,
  MODEL_PROFILE_LIMITS,
  modelProfilesApi,
  type ModelExecutionStatus,
  type ModelProfile,
  type ModelProfileUpsertInput,
  type ModelTaskBindings,
  type RequestSpeedMode,
} from '../api/model-profiles'
import { useModelProfilesStore } from '../stores/model-profiles'
import '../styles/model-profiles.css'

const props = withDefaults(defineProps<{ compact?: boolean }>(), { compact: false })

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
  teachingPrepModel: string
  classTeacherModel: string
  requestSpeedMode: RequestSpeedMode
  maxConcurrentRequests: number
  requestsPerMinute: number
}

const profilesStore = useModelProfilesStore()
const formElement = ref<HTMLFormElement | null>(null)
const nameInput = ref<HTMLInputElement | null>(null)
const localError = ref('')
const baseline = ref('')
const executionStatus = ref<ModelExecutionStatus | null>(null)
const executionStatusState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const executionStatusError = ref('')
const taskBindingsDraft = ref<ModelTaskBindings>({
  content_generation: { profile_name: null, model: '' },
  grading: { profile_name: null, model: '' },
  teaching_prep: { profile_name: null, model: '' },
  class_teacher: { profile_name: null, model: '' },
})
const taskRows = [
  { key: 'content_generation', title: '题库与评分标准生成', detail: '题库标注、试题和评分标准使用同一个模型' },
  { key: 'grading', title: '识别姓名与批改试卷', detail: '姓名识别和批改共用同一个模型与站点' },
  { key: 'teaching_prep', title: '备课工作台', detail: '备课对话、资料整理与生成' },
  { key: 'class_teacher', title: '班主任工作台', detail: '班主任对话与草稿整理' },
] as const
let executionStatusController: AbortController | null = null

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
  teachingPrepModel: '',
  classTeacherModel: '',
  requestSpeedMode: 'automatic',
  maxConcurrentRequests: 20,
  requestsPerMinute: 1000,
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
    teachingPrepModel: draft.teachingPrepModel,
    classTeacherModel: draft.classTeacherModel,
    requestSpeedMode: draft.requestSpeedMode,
    maxConcurrentRequests: draft.maxConcurrentRequests,
    requestsPerMinute: draft.requestsPerMinute,
  })
}

baseline.value = draftSnapshot()

const isNew = computed(() => draft.sourceName === null)
const isDirty = computed(() => draftSnapshot() !== baseline.value)
defineExpose({ hasUnsavedChanges: isDirty })
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
const requestSpeedSummary = computed(() => {
  if (draft.requestSpeedMode === 'conservative') {
    return '同时处理 1 个请求，每分钟最多启动 60 个请求。'
  }
  if (draft.requestSpeedMode === 'custom') {
    return `同时最多 ${draft.maxConcurrentRequests} 个请求，`
      + `每分钟最多启动 ${draft.requestsPerMinute} 个请求。`
  }
  return '从 6 个同时请求起步，稳定后逐步提高，最多 20 个；每分钟最多启动 1000 个请求。'
})
const executionLimitingMessage = computed(() => {
  if (executionStatus.value?.limiting_reason === 'provider_overload') {
    return '模型服务刚才返回了拥堵或限流，系统已自动降低同时请求数。'
  }
  if (executionStatus.value?.limiting_reason === 'recovering') {
    return '模型服务正在恢复稳定，系统会逐步提高同时请求数。'
  }
  return '当前按照已保存的请求速度方案运行。'
})

function resetExecutionStatus(): void {
  executionStatusController?.abort()
  executionStatusController = null
  executionStatus.value = null
  executionStatusState.value = 'idle'
  executionStatusError.value = ''
}

async function loadExecutionStatus(
  profileName: string | null = draft.sourceName,
): Promise<void> {
  executionStatusController?.abort()
  if (profileName === null) {
    resetExecutionStatus()
    return
  }
  const controller = new AbortController()
  executionStatusController = controller
  executionStatusState.value = 'loading'
  executionStatusError.value = ''
  try {
    const status = await modelProfilesApi.getExecutionStatus(
      profileName,
      controller.signal,
    )
    if (controller.signal.aborted || draft.sourceName !== profileName) return
    executionStatus.value = status
    executionStatusState.value = 'ready'
  } catch (error) {
    if (controller.signal.aborted || draft.sourceName !== profileName) return
    executionStatus.value = null
    executionStatusState.value = 'error'
    executionStatusError.value = error instanceof Error
      ? error.message
      : '当前运行状态没有读取成功。'
  } finally {
    if (executionStatusController === controller) {
      executionStatusController = null
    }
  }
}

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
    teachingPrepModel: profile.teaching_prep_model,
    classTeacherModel: profile.class_teacher_model,
    requestSpeedMode: profile.request_speed_mode,
    maxConcurrentRequests: profile.max_concurrent_requests,
    requestsPerMinute: profile.requests_per_minute,
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
    teachingPrepModel: '',
    classTeacherModel: '',
    requestSpeedMode: 'automatic',
    maxConcurrentRequests: 20,
    requestsPerMinute: 1000,
  } satisfies ModelProfileDraft)
  localError.value = ''
  baseline.value = draftSnapshot()
}

function syncFromSelection(): void {
  taskBindingsDraft.value = copyModelTaskBindings(profilesStore.taskBindings)
  if (profilesStore.selectedProfile) {
    applyProfile(profilesStore.selectedProfile)
  } else {
    applyBlankProfile()
  }
}

async function saveTaskBindings(): Promise<void> {
  localError.value = ''
  await profilesStore.saveTaskBindings(taskBindingsDraft.value)
  taskBindingsDraft.value = copyModelTaskBindings(profilesStore.taskBindings)
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
  void loadExecutionStatus(profile.name)
}

async function beginNewProfile(): Promise<void> {
  if (!confirmDiscard('当前表单有未保存修改。新建配置会丢弃这些修改，是否继续？')) {
    return
  }
  profilesStore.selectProfile(null)
  applyBlankProfile()
  resetExecutionStatus()
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
    teaching_prep_model: draft.teachingPrepModel,
    class_teacher_model: draft.classTeacherModel,
    request_speed_mode: draft.requestSpeedMode,
    max_concurrent_requests: draft.maxConcurrentRequests,
    requests_per_minute: draft.requestsPerMinute,
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
  if (saved) {
    applyProfile(saved)
    void loadExecutionStatus(saved.name)
  }
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
  void loadExecutionStatus(draft.sourceName)
}

async function deleteProfile(): Promise<void> {
  if (draft.sourceName === null || isBusy.value) return
  const name = draft.sourceName
  if (!window.confirm(
    `确定永久删除模型配置“${name}”吗？\n\n使用它的工作模型安排会自动改用剩余的当前配置；如果没有其他配置，对应 AI 功能会暂时不可用。`,
  )) return
  const deleted = await profilesStore.deleteProfile(name)
  if (!deleted) return
  syncFromSelection()
  void loadExecutionStatus()
}

async function reloadProfiles(): Promise<void> {
  if (
    !confirmDiscard('重新加载会丢弃当前未保存修改，是否继续？')
  ) return
  const loaded = await profilesStore.load()
  if (loaded) {
    syncFromSelection()
    void loadExecutionStatus()
  }
}

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  if (!isDirty.value) return
  event.preventDefault()
  event.returnValue = ''
}


onMounted(async () => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  const loaded = await profilesStore.load()
  if (loaded) {
    syncFromSelection()
    void loadExecutionStatus()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  executionStatusController?.abort()
})
</script>

<template>
  <article class="model-profiles-view">
    <header v-if="!props.compact" class="model-profiles-view__header">
      <div>
        <p class="model-profiles-view__eyebrow">LOCAL MODEL ROUTING</p>
        <h1 tabindex="-1">API 站点与工作模型</h1>
        <p>先保存可用的 API 站点，再为四类工作分别指定站点和模型。</p>
      </div>
      <div class="model-profiles-view__current" aria-live="polite">
        <span class="model-profiles-view__beacon" aria-hidden="true" />
        <span>
          <small>兼容旧任务的默认站点</small>
          <strong>{{ currentProfileLabel }}</strong>
        </span>
      </div>
    </header>

    <section v-if="!props.compact" class="model-profiles-notice" aria-label="费用与密钥说明">
      <p>
        <strong>保存配置不会调用模型，不会产生费用。</strong>
        只有之后真正执行识别、批改或标注任务时，才可能产生模型费用。
      </p>
      <p>
        <strong>密钥只保存在本机且页面无法读回。</strong>
        已保存的密钥只显示“已保存”，不会以明文返回页面。
      </p>
    </section>

    <section
      v-if="profilesStore.loadState === 'ready' || profilesStore.loadState === 'empty'"
      class="model-task-routing"
      aria-labelledby="model-task-routing-title"
    >
      <header>
        <div>
          <p class="model-profiles-view__eyebrow">工作模型</p>
          <h2 id="model-task-routing-title">四类工作，各自选择站点和模型</h2>
          <p>一个站点就是一组 API 地址和密钥；不同工作可以使用不同站点。</p>
        </div>
        <button
          type="button"
          class="model-profiles-button model-profiles-button--primary"
          :disabled="isBusy || profilesStore.profiles.length === 0"
          @click="saveTaskBindings"
        >保存工作模型</button>
      </header>
      <p v-if="profilesStore.profiles.length === 0" class="model-task-routing__empty">
        请先在下方新增一个 API 站点，再安排工作模型。
      </p>
      <div class="model-task-routing__grid">
        <label v-for="row in taskRows" :key="row.key" class="model-task-row">
          <span class="model-task-row__title"><strong>{{ row.title }}</strong><small>{{ row.detail }}</small></span>
          <select
            v-model="taskBindingsDraft[row.key].profile_name"
            :aria-label="`${row.title}使用的 API 站点`"
            :disabled="profilesStore.profiles.length === 0 || isBusy"
          >
            <option :value="null" disabled>选择 API 站点</option>
            <option v-for="profile in profilesStore.profiles" :key="profile.name" :value="profile.name">{{ profile.name }}</option>
          </select>
          <input
            v-model="taskBindingsDraft[row.key].model"
            type="text"
            :maxlength="MODEL_PROFILE_LIMITS.model"
            :aria-label="`${row.title}使用的模型`"
            :disabled="profilesStore.profiles.length === 0 || isBusy"
            placeholder="填写模型名称"
          >
        </label>
      </div>
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

    <details v-else class="model-profiles-advanced-shell" :open="!props.compact">
      <summary>高级设置：API 站点、密钥与请求速度</summary>
      <div class="model-profiles-workspace">
      <aside class="model-profiles-index" aria-label="API 站点列表">
        <header>
          <div>
            <p>API 站点</p>
            <span>{{ profilesStore.profiles.length }} 个</span>
          </div>
          <button
            type="button"
            class="model-profiles-button model-profiles-button--quiet"
            :disabled="isBusy"
            @click="beginNewProfile"
          >
            新增站点
          </button>
        </header>

        <p
          v-if="profilesStore.profiles.length === 0"
          class="model-profiles-index__empty"
        >
          还没有 API 站点。先在右侧填写第一个站点。
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
            <p>{{ isNew ? 'NEW API SITE' : 'API SITE' }}</p>
            <h2>{{ isNew ? '新增 API 站点' : draft.sourceName }}</h2>
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
            <span>站点名称</span>
            <input
              ref="nameInput"
              v-model="draft.name"
              name="profile-name"
              type="text"
              autocomplete="off"
              :maxlength="MODEL_PROFILE_LIMITS.name"
              placeholder="例如：校内模型站点"
              :disabled="isBusy"
              :readonly="!isNew"
              required
            >
            <small v-if="!isNew">已保存配置的名称固定；需要新名称时请新建一套配置。</small>
            <small>用于区分不同服务商或校内代理。</small>
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

        </div>

        <fieldset class="model-profile-execution">
          <legend>AI 请求速度</legend>
          <p class="model-profile-execution__intro">
            姓名识别、试卷批改、评分配置和题库标注共用这套限制。
            学生可以一次全部进入队列，程序只按这里允许的数量同时请求模型。
          </p>
          <div class="model-profile-execution__modes">
            <label
              class="model-profile-speed-option"
              :class="{ 'model-profile-speed-option--selected': draft.requestSpeedMode === 'automatic' }"
            >
              <input
                v-model="draft.requestSpeedMode"
                type="radio"
                name="request-speed-mode"
                value="automatic"
                :disabled="isBusy"
              >
              <span>
                <strong>自动（推荐）</strong>
                <small>遇到限流自动降低，同时请求稳定后再缓慢恢复。</small>
              </span>
            </label>
            <label
              class="model-profile-speed-option"
              :class="{ 'model-profile-speed-option--selected': draft.requestSpeedMode === 'conservative' }"
            >
              <input
                v-model="draft.requestSpeedMode"
                type="radio"
                name="request-speed-mode"
                value="conservative"
                :disabled="isBusy"
              >
              <span>
                <strong>保守</strong>
                <small>一次只发送一个请求，适合接口不稳定时临时使用。</small>
              </span>
            </label>
            <label
              class="model-profile-speed-option"
              :class="{ 'model-profile-speed-option--selected': draft.requestSpeedMode === 'custom' }"
            >
              <input
                v-model="draft.requestSpeedMode"
                type="radio"
                name="request-speed-mode"
                value="custom"
                :disabled="isBusy"
              >
              <span>
                <strong>自定义</strong>
                <small>设置允许的上限；供应商限流时仍会自动减速。</small>
              </span>
            </label>
          </div>
          <div
            v-if="draft.requestSpeedMode === 'custom'"
            class="model-profile-execution__custom"
          >
            <label class="model-profile-field">
              <span>最多同时请求数</span>
              <input
                v-model.number="draft.maxConcurrentRequests"
                name="max-concurrent-requests"
                type="number"
                min="1"
                :max="MODEL_PROFILE_LIMITS.concurrentRequests"
                step="1"
                :disabled="isBusy"
                required
              >
              <small>这是 worker 的实际含义；填 100 不代表供应商一定接受 100 个并发。</small>
            </label>
            <label class="model-profile-field">
              <span>每分钟请求数（RPM）</span>
              <input
                v-model.number="draft.requestsPerMinute"
                name="requests-per-minute"
                type="number"
                min="1"
                :max="MODEL_PROFILE_LIMITS.requestsPerMinute"
                step="1"
                :disabled="isBusy"
                required
              >
              <small>RPM 只限制启动频率，不会自动增加同时处理数量。</small>
            </label>
          </div>
          <p class="model-profile-execution__summary" aria-live="polite">
            <strong>当前计划：</strong>{{ requestSpeedSummary }}
          </p>
          <section
            v-if="!isNew"
            class="model-profile-runtime"
            aria-label="AI 请求运行状态"
          >
            <header>
              <div>
                <strong>当前运行状态</strong>
                <small>本次程序启动以来，四类 AI 请求共用</small>
              </div>
              <button
                type="button"
                class="model-profiles-button model-profiles-button--quiet"
                :disabled="executionStatusState === 'loading'"
                @click="loadExecutionStatus()"
              >
                {{ executionStatusState === 'loading' ? '正在刷新…' : '刷新状态' }}
              </button>
            </header>
            <p
              v-if="executionStatusState === 'loading' && executionStatus === null"
              class="model-profile-runtime__state"
              role="status"
            >
              正在读取本机运行状态…
            </p>
            <p
              v-else-if="executionStatusState === 'error'"
              class="model-profile-runtime__state model-profile-runtime__state--error"
              role="alert"
            >
              {{ executionStatusError || '当前运行状态没有读取成功。' }}
            </p>
            <template v-else-if="executionStatus !== null">
              <dl class="model-profile-runtime__metrics">
                <div>
                  <dt>正在请求</dt>
                  <dd>{{ executionStatus.active }}</dd>
                </div>
                <div>
                  <dt>排队等待</dt>
                  <dd>{{ executionStatus.queued }}</dd>
                </div>
                <div>
                  <dt>当前同时上限</dt>
                  <dd>
                    {{ executionStatus.effective_max_in_flight }}
                    <small>/ 计划 {{ executionStatus.configured_max_in_flight }}</small>
                  </dd>
                </div>
                <div>
                  <dt>启动后峰值</dt>
                  <dd>{{ executionStatus.peak_active }}</dd>
                </div>
              </dl>
              <p class="model-profile-runtime__reason">
                {{ executionLimitingMessage }}
                本次启动已向模型实际发送
                {{ executionStatus.physical_request_count }} 个请求。
              </p>
            </template>
          </section>
          <p class="model-profile-execution__note">
            保存不会测试接口或产生费用；新设置从下一次任务启动时生效，
            已经运行的任务继续使用启动时的方案。
          </p>
        </fieldset>

        <footer class="model-profile-editor__actions">
          <div>
            <strong>{{ editStatus }}</strong>
            <span>保存和切换都只修改本机设置，不会测试连接。</span>
          </div>
          <div class="model-profile-editor__buttons">
            <button
              v-if="!isNew"
              type="button"
              class="model-profiles-button model-profiles-button--danger"
              :disabled="isBusy"
              @click="deleteProfile"
            >
              {{ profilesStore.operationState === 'deleting'
                ? '正在删除…'
                : '删除配置' }}
            </button>
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
    </details>

    <p class="model-profiles-trace-link">
      <router-link to="/settings?section=ai-trace">查看 AI 调用记录</router-link>
    </p>
  </article>
</template>
