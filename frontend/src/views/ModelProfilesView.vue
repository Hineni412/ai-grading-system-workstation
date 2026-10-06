<script setup lang="ts">
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../components/ui/sheet'
import { onBeforeRouteLeave } from 'vue-router'
import FeedbackBanner from '@/components/design-system/FeedbackBanner.vue'
import AppButton from '@/components/design-system/AppButton.vue'
import StatePanel from '@/components/design-system/StatePanel.vue'
import StatusBadge from '@/components/design-system/StatusBadge.vue'

import {
  computed,
  defineAsyncComponent,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
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
import { useConfirm } from '../composables/useConfirm'
import { useModelProfilesStore } from '../stores/model-profiles'
import '../styles/model-profiles.css'

const AiDiagnosticsPanel = defineAsyncComponent(() => import('../components/settings/AiDiagnosticsPanel.vue'))
const props = defineProps<{ scrollToLog?: boolean }>()
const diagnosticsOpen = ref(false)
const diagnosticsDisclosure = ref<HTMLDetailsElement | null>(null)
watch(() => props.scrollToLog, async value => {
  if (!value) return
  diagnosticsOpen.value = true
  await nextTick()
  diagnosticsDisclosure.value?.scrollIntoView({ block: 'start' })
}, { immediate: true })

const drawerOpen = ref(false)
const accountStatuses = ref<Record<string, ModelExecutionStatus>>({})
const accountController = new AbortController()

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
  classTeacherModel: string
  requestSpeedMode: RequestSpeedMode
  maxConcurrentRequests: number
  requestsPerMinute: number
  maxAutoRetries: number | null
  requestTimeoutSeconds: number | null
}

const profilesStore = useModelProfilesStore()
const { confirm } = useConfirm()
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
})
const taskRows = [
  { key: 'content_generation', title: '出题、评分标准与报告', detail: '题库标注、评分标准、组卷、班级与个人分析报告' },
  { key: 'grading', title: '批改试卷', detail: '含姓名补充识别' },
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
  classTeacherModel: '',
  requestSpeedMode: 'automatic',
  maxConcurrentRequests: 20,
  requestsPerMinute: 1000,
  maxAutoRetries: null,
  requestTimeoutSeconds: null,
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
  classTeacherModel: draft.classTeacherModel,
    requestSpeedMode: draft.requestSpeedMode,
    maxConcurrentRequests: draft.maxConcurrentRequests,
    requestsPerMinute: draft.requestsPerMinute,
    maxAutoRetries: draft.maxAutoRetries,
    requestTimeoutSeconds: draft.requestTimeoutSeconds,
  })
}

baseline.value = draftSnapshot()

const isNew = computed(() => draft.sourceName === null)
const isDirty = computed(() => draftSnapshot() !== baseline.value)
const bindingsDirty = computed(() => JSON.stringify(taskBindingsDraft.value) !== JSON.stringify(profilesStore.taskBindings))
const hasUnsavedChanges = computed(() => isDirty.value || bindingsDirty.value)
defineExpose({ hasUnsavedChanges })
onBeforeRouteLeave(async () => !hasUnsavedChanges.value || await confirm({
  title: '离开 AI 服务设置？',
  message: 'AI 服务还有未保存修改。离开后会丢失这些修改。',
  confirmLabel: '离开',
  danger: true,
}))
function hostname(url: string): string { try { return new URL(url).host } catch { return url } }
async function loadAccountStatuses() {
  await Promise.all(profilesStore.profiles.map(async profile => {
    try {
      const status = await modelProfilesApi.getExecutionStatus(profile.name, accountController.signal)
      if (!accountController.signal.aborted) accountStatuses.value[profile.name] = status
    } catch { /* Runtime information is optional; profile editing remains available. */ }
  }))
}
async function closeDrawer(value = false) {
  if (value || isBusy.value) return
  if (!await confirmDiscard({
    title: '关闭编辑？',
    message: '当前表单有未保存修改。关闭会丢弃这些修改。',
    confirmLabel: '关闭',
  })) return
  drawerOpen.value = false
  if (profilesStore.selectedProfile) applyProfile(profilesStore.selectedProfile)
  else applyBlankProfile()
}
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
    classTeacherModel: profile.class_teacher_model,
    requestSpeedMode: profile.request_speed_mode,
    maxConcurrentRequests: profile.max_concurrent_requests,
    requestsPerMinute: profile.requests_per_minute,
    maxAutoRetries: profile.max_auto_retries,
    requestTimeoutSeconds: profile.request_timeout_seconds,
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
    classTeacherModel: '',
    requestSpeedMode: 'automatic',
    maxConcurrentRequests: 20,
    requestsPerMinute: 1000,
    maxAutoRetries: null,
    requestTimeoutSeconds: null,
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
  if (await profilesStore.saveTaskBindings(taskBindingsDraft.value)) {
    taskBindingsDraft.value = copyModelTaskBindings(profilesStore.taskBindings)
  }
}

async function confirmDiscard(options: { title: string; message: string; confirmLabel: string }): Promise<boolean> {
  return !isDirty.value || await confirm({ ...options, danger: true })
}

async function chooseProfile(profile: ModelProfile): Promise<void> {
  if (
    !await confirmDiscard({
      title: '切换配置？',
      message: '当前表单有未保存修改。切换配置会丢弃这些修改。',
      confirmLabel: '切换',
    })
  ) return
  drawerOpen.value = true
  profilesStore.selectProfile(profile.name)
  applyProfile(profile)
  void loadExecutionStatus(profile.name)
}

async function beginNewProfile(): Promise<void> {
  if (!await confirmDiscard({
    title: '新建配置？',
    message: '当前表单有未保存修改。新建配置会丢弃这些修改。',
    confirmLabel: '新建',
  })) {
    return
  }
  drawerOpen.value = true
  profilesStore.selectProfile(null)
  applyBlankProfile()
  resetExecutionStatus()
  await nextTick()
  nameInput.value?.focus()
}

function toUpsertInput(): ModelProfileUpsertInput {
  const rawRetries = draft.maxAutoRetries as number | null | '' | undefined
  const rawTimeout =
    draft.requestTimeoutSeconds as number | null | '' | undefined
  return {
    name: draft.name,
    base_url: draft.baseUrl,
    api_key: draft.apiKey,
    ocr_model: draft.ocrModel,
    grading_model: draft.gradingModel,
    config_base_url: draft.configBaseUrl,
    config_api_key: draft.configApiKey,
    config_model: draft.configModel,
    class_teacher_model: draft.classTeacherModel,
    request_speed_mode: draft.requestSpeedMode,
    max_concurrent_requests: draft.maxConcurrentRequests,
    requests_per_minute: draft.requestsPerMinute,
    max_auto_retries: rawRetries === '' || rawRetries === undefined
      ? null
      : rawRetries,
    request_timeout_seconds: rawTimeout === '' || rawTimeout === undefined
      ? null
      : rawTimeout,
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
    drawerOpen.value = false
    void loadAccountStatuses()
  }
}

async function activateProfile(): Promise<void> {
  if (draft.sourceName === null || !canActivate.value) return
  if (
    isDirty.value
    && !await confirm({
      title: '设为默认服务？',
      message: '当前表单有未保存修改。切换当前配置会使用已经保存的版本，并丢弃这些修改。',
      confirmLabel: '切换',
      danger: true,
    })
  ) return
  if (isDirty.value) {
    const saved = profilesStore.profiles.find(
      ({ name }) => name === draft.sourceName,
    )
    if (saved) applyProfile(saved)
  }
  await profilesStore.activateProfile(draft.sourceName)
  void loadExecutionStatus(draft.sourceName)
  void loadAccountStatuses()
}

async function deleteProfile(profile?: ModelProfile): Promise<void> {
  const name = profile?.name ?? draft.sourceName
  if (name === null || isBusy.value) return
  if (!await confirm({
    title: `永久删除模型配置“${name}”？`,
    message: '使用它的工作模型安排会自动改用剩余的当前配置；如果没有其他配置，对应 AI 功能会暂时不可用。',
    confirmLabel: '彻底删除',
    danger: true,
  })) return
  const deleted = await profilesStore.deleteProfile(name)
  if (!deleted) return
  syncFromSelection()
  drawerOpen.value = false
  void loadAccountStatuses()
}

async function reloadProfiles(): Promise<void> {
  if (
    !await confirmDiscard({
      title: '重新加载？',
      message: '重新加载会丢弃当前未保存修改。',
      confirmLabel: '重新加载',
    })
  ) return
  const loaded = await profilesStore.load()
  if (loaded) {
    syncFromSelection()
    void loadAccountStatuses()
  }
}

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  if (!hasUnsavedChanges.value) return
  event.preventDefault()
  event.returnValue = ''
}


onMounted(async () => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  const loaded = await profilesStore.load()
  if (loaded) {
    syncFromSelection()
    void loadAccountStatuses()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  executionStatusController?.abort()
  accountController.abort()
})
</script>
<template>
  <article class="model-profiles-view">
    <FeedbackBanner v-if="profilesStore.errorMessage || localError" role="alert" tone="error" :description="localError || profilesStore.errorMessage" />
    <StatePanel v-if="profilesStore.loadState === 'loading'" kind="loading" title="正在读取本机 AI 服务…" />
    <StatePanel v-else-if="profilesStore.loadState === 'error'" kind="error" title="服务列表暂时无法读取" retry-label="重新加载" @retry="reloadProfiles" />
    <template v-else>
      <section class="settings-panel" aria-labelledby="model-task-routing-title">
        <header class="settings-panel__heading"><h2 id="model-task-routing-title">用哪个 AI</h2></header>
        <table class="app-table model-task-table"><tbody>
          <tr v-for="row in taskRows" :key="row.key">
            <td><strong>{{ row.title }}</strong><small>{{ row.detail }}</small></td>
            <td><select v-model="taskBindingsDraft[row.key].profile_name" class="app-input" :aria-label="`${row.title}使用的服务账号`" :disabled="isBusy || !profilesStore.profiles.length"><option :value="null" disabled>选择服务账号</option><option v-for="profile in profilesStore.profiles" :key="profile.name" :value="profile.name">{{ profile.name }}</option></select></td>
            <td><input v-model="taskBindingsDraft[row.key].model" class="app-input" :aria-label="`${row.title}使用的模型`" :maxlength="MODEL_PROFILE_LIMITS.model" :disabled="isBusy || !profilesStore.profiles.length" placeholder="填写模型名称"></td>
          </tr>
        </tbody></table>
        <div class="settings-panel__body settings-inline model-task-actions"><AppButton variant="primary" :disabled="isBusy || !bindingsDirty || !profilesStore.profiles.length" @click="saveTaskBindings">保存</AppButton><span class="settings-note settings-saved" :style="{ visibility: profilesStore.noticeMessage ? 'visible' : 'hidden' }" :aria-label="profilesStore.noticeMessage" role="status">已保存</span></div>
      </section>
      <section class="settings-panel" aria-labelledby="model-accounts-title">
        <header class="settings-panel__heading"><h2 id="model-accounts-title">服务账号</h2><AppButton variant="secondary" :disabled="isBusy" @click="beginNewProfile">添加服务</AppButton></header>
        <div class="model-accounts">
          <StatePanel v-if="!profilesStore.profiles.length" kind="empty" compact title="还没有服务账号">
            <template #actions><AppButton variant="ghost" @click="beginNewProfile">添加服务</AppButton></template>
          </StatePanel>
          <div v-for="profile in profilesStore.profiles" :key="profile.name" class="model-account-row">
            <strong>{{ profile.name }}</strong><span class="model-account-host">{{ hostname(profile.base_url) }}</span>
            <StatusBadge :tone="profile.has_api_key ? 'success' : 'warning'" :label="profile.has_api_key ? '密钥已保存' : '未保存密钥'" />
            <StatusBadge v-if="profile.name === profilesStore.activeProfileName" tone="neutral" label="默认" />
            <span v-if="accountStatuses[profile.name] && (accountStatuses[profile.name]!.active + accountStatuses[profile.name]!.queued > 0)" class="model-account-runtime">运行中：{{ accountStatuses[profile.name]!.active }} 个请求，排队 {{ accountStatuses[profile.name]!.queued }} 个</span>
            <div class="model-account-actions"><AppButton variant="ghost" :disabled="isBusy" @click="chooseProfile(profile)">编辑</AppButton><AppButton variant="ghost" class="settings-danger-ghost" :disabled="isBusy" @click="deleteProfile(profile)">删除</AppButton></div>
          </div>
        </div>
      </section>
    </template>
    <details id="ai-call-log" ref="diagnosticsDisclosure" class="settings-panel settings-disclosure settings-log-panel" :open="diagnosticsOpen" @toggle="diagnosticsOpen = ($event.target as HTMLDetailsElement).open">
      <summary>AI 调用记录</summary>
      <div v-if="diagnosticsOpen" class="settings-panel__body">
        <p class="settings-note">记录只保存在本机。</p>
        <AiDiagnosticsPanel />
      </div>
    </details>
    <Sheet :open="drawerOpen" @update:open="closeDrawer">
      <SheetContent class="settings-drawer" :aria-describedby="undefined" @interact-outside="event => { if (isBusy) event.preventDefault() }" @escape-key-down="event => { if (isBusy) event.preventDefault() }">
        <SheetHeader class="settings-drawer__header"><SheetTitle>{{ isNew ? '添加服务' : '编辑服务' }}</SheetTitle></SheetHeader>
        <form ref="formElement" class="settings-drawer__form" @submit.prevent="saveProfile">
          <div class="settings-drawer__body">
            <label class="settings-field"><span>名称</span><input ref="nameInput" v-model="draft.name" class="app-input" name="profile-name" :maxlength="MODEL_PROFILE_LIMITS.name" :readonly="!isNew" :disabled="isBusy" required></label>
            <label class="settings-field"><span>地址</span><input v-model="draft.baseUrl" class="app-input" name="base-url" type="url" placeholder="https://api.example.com/v1" :maxlength="MODEL_PROFILE_LIMITS.url" :disabled="isBusy" required></label>
            <label class="settings-field"><span>密钥</span><input v-model="draft.apiKey" class="app-input" name="api-key" type="password" autocomplete="new-password" :placeholder="draft.hasApiKey ? '已保存，留空不改' : '粘贴 API 密钥'" :maxlength="MODEL_PROFILE_LIMITS.apiKey" :disabled="isBusy" :required="isNew"></label>
            <details class="settings-disclosure"><summary>高级</summary><div class="model-advanced">
              <span class="settings-note">请求速度</span>
              <label v-for="mode in (['automatic', 'conservative', 'custom'] as const)" :key="mode" class="settings-check"><input v-model="draft.requestSpeedMode" type="radio" name="request-speed-mode" :value="mode" :disabled="isBusy">{{ { automatic: '自动（推荐）', conservative: '保守', custom: '自定义' }[mode] }}</label>
              <div v-if="draft.requestSpeedMode === 'custom'" class="settings-field-pair">
                <label class="settings-field"><span>同时请求数</span><input v-model.number="draft.maxConcurrentRequests" class="app-input" name="max-concurrent-requests" type="number" min="1" :max="MODEL_PROFILE_LIMITS.concurrentRequests" step="1" :disabled="isBusy" required></label>
                <label class="settings-field"><span>每分钟请求数</span><input v-model.number="draft.requestsPerMinute" class="app-input" name="requests-per-minute" type="number" min="1" :max="MODEL_PROFILE_LIMITS.requestsPerMinute" step="1" :disabled="isBusy" required></label>
              </div>
              <p class="settings-note">{{ requestSpeedSummary }}</p>
              <label class="settings-field"><span>失败自动重试次数</span><input v-model.number="draft.maxAutoRetries" class="app-input" name="max-auto-retries" type="number" min="0" :max="MODEL_PROFILE_LIMITS.maxAutoRetries" step="1" placeholder="默认" :disabled="isBusy"></label>
              <label class="settings-field"><span>等待超时（秒）</span><input v-model.number="draft.requestTimeoutSeconds" class="app-input" name="request-timeout-seconds" type="number" min="30" :max="MODEL_PROFILE_LIMITS.requestTimeoutSeconds" step="1" placeholder="默认" :disabled="isBusy"><small>30–1200</small></label>
              
              <section v-if="!isNew" aria-label="当前运行状态">
                <div class="settings-inline"><strong>当前运行状态</strong><AppButton variant="ghost" :disabled="executionStatusState === 'loading'" @click="loadExecutionStatus()">刷新</AppButton></div>
                <p v-if="executionStatus" class="settings-note">正在请求 {{ executionStatus.active }} · 排队 {{ executionStatus.queued }} · 当前同时上限 {{ executionStatus.effective_max_in_flight }} / {{ executionStatus.configured_max_in_flight }} · 峰值 {{ executionStatus.peak_active }} · 已发送 {{ executionStatus.physical_request_count }} 次</p>
                <p v-if="executionStatus" class="settings-note">{{ executionLimitingMessage }}</p><p v-else-if="executionStatusError" class="settings-note">{{ executionStatusError }}</p>
              </section>
              <AppButton variant="secondary" :disabled="!canActivate" @click="activateProfile">{{ isCurrent ? '已是默认服务' : '设为默认服务（旧任务使用）' }}</AppButton>
            </div></details>
            <FeedbackBanner v-if="localError || profilesStore.errorMessage" role="alert" tone="error" :description="localError || profilesStore.errorMessage" />
          </div>
          <footer class="settings-drawer__footer"><AppButton variant="secondary" :disabled="isBusy" @click="closeDrawer()">取消</AppButton><AppButton variant="primary" type="submit" :disabled="isBusy || !isDirty">{{ isBusy ? '正在保存…' : '保存' }}</AppButton></footer>
        </form>
      </SheetContent>
    </Sheet>
  </article>
</template>
