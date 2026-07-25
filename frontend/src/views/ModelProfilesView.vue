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

const profilesStore = useModelProfilesStore()
const formElement = ref<HTMLFormElement | null>(null)
const nameInput = ref<HTMLInputElement | null>(null)
const localError = ref('')
const baseline = ref('')

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

onMounted(async () => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  const loaded = await profilesStore.load()
  if (loaded) syncFromSelection()
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
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
              required
            >
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
  </article>
</template>
