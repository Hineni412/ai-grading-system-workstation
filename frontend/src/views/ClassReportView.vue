<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  classAnalysisApi,
  type ClassAnalysisResponse,
} from '../api/class-analysis'
import { jobApi, TERMINAL_JOB_STATUSES } from '../api/jobs'
import {
  modelProfilesApi,
  type ModelTaskBinding,
} from '../api/model-profiles'
import { formatTime } from '../lib/format'
import BackButton from '../components/design-system/BackButton.vue'
import PageHeader from '../components/design-system/PageHeader.vue'
import StatePanel from '../components/design-system/StatePanel.vue'
import { useJobStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const jobStore = useJobStore()

const analysis = ref<ClassAnalysisResponse | null>(null)
const selectedClass = ref('')
const loadState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const errorMessage = ref('')
const contentBinding = ref<ModelTaskBinding | null>(null)
let loadGeneration = 0
let loadController: AbortController | null = null

function stringQuery(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function positiveIntegerQuery(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d+$/.test(value)) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null
}

const activeJobId = computed(() => analysis.value?.active_job_id ?? null)
const activeJob = computed(() => (
  activeJobId.value === null ? null : jobStore.jobs[activeJobId.value] ?? null
))
const generating = computed(() => {
  if (analysis.value?.status === 'generating') return true
  return activeJob.value !== null
    && !TERMINAL_JOB_STATUSES.has(activeJob.value.status)
})
const contentUnconfigured = computed(() => (
  contentBinding.value !== null && !contentBinding.value.profile_name
))
const classNames = computed(() => analysis.value?.class_names ?? [])
const narrative = computed(() => analysis.value?.narrative ?? null)
const narrativeFailed = computed(() => analysis.value?.narrative_failed ?? false)
const stale = computed(() => analysis.value?.stale ?? false)

const bannerText = computed(() => {
  if (generating.value) return 'AI 正在整理，完成后自动更新。'
  if (narrative.value === null) {
    if (contentUnconfigured.value) return '未配置内容生成模型，请前往 设置→模型配置 绑定后，在成绩中心点「AI 整理」。'
    if (narrativeFailed.value) return 'AI 整理未成功，可回到成绩中心点「AI 整理」重试。'
    return 'AI 讲评部分尚未整理：复核完成后自动整理，或回到成绩中心点「AI 整理」。'
  }
  if (stale.value) return '成绩已更新或统计口径已调整，AI 分析可能与当前成绩不一致；回到成绩中心点「AI 整理」更新。'
  return ''
})

const reportUrl = computed(() => {
  const sessionId = sessionStore.selectedSessionId
  // narrative 视图不返回 data；能否渲染只看场次是否有成绩数据。
  if (sessionId === null || !analysis.value || analysis.value.status === 'no_data') return ''
  const query = new URLSearchParams()
  if (selectedClass.value) query.set('class_name', selectedClass.value)
  // generated_at 作为版本参数：重新生成后强制 iframe 重新读取。
  if (analysis.value?.generated_at) query.set('v', analysis.value.generated_at)
  return `/api/sessions/${sessionId}/class-analysis/report?${query.toString()}`
})

watch(
  [
    () => route.query.session,
    () => sessionStore.loadState,
    () => sessionStore.sessions.map((session) => session.id).join(','),
  ],
  ([querySession, sessionsLoadState]) => {
    if (sessionsLoadState !== 'ready') return
    const requestedSessionId = positiveIntegerQuery(querySession)
    if (
      requestedSessionId === null
      || requestedSessionId === sessionStore.selectedSessionId
      || !sessionStore.sessions.some((session) => session.id === requestedSessionId)
    ) return
    sessionStore.selectSession(requestedSessionId)
  },
  { immediate: true },
)

watch(
  () => sessionStore.selectedSessionId,
  () => {
    selectedClass.value = stringQuery(route.query.class) ?? ''
    analysis.value = null
    void load()
  },
  { immediate: true },
)

watch(
  () => activeJob.value?.status,
  (status) => {
    if (status !== undefined && TERMINAL_JOB_STATUSES.has(status)) void load()
  },
)

async function load(): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  const generation = ++loadGeneration
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  if (sessionId === null) {
    analysis.value = null
    loadState.value = 'idle'
    return
  }
  loadState.value = 'loading'
  errorMessage.value = ''
  try {
    const next = await classAnalysisApi.getClassAnalysis(
      sessionId,
      controller.signal,
      selectedClass.value || undefined,
      'narrative',
    )
    if (generation !== loadGeneration || sessionStore.selectedSessionId !== sessionId) return
    analysis.value = next
    try {
      const modelState = await modelProfilesApi.getState()
      if (generation !== loadGeneration || sessionStore.selectedSessionId !== sessionId) return
      contentBinding.value = modelState.task_bindings.content_generation
    } catch {
      // 配置读取失败时按已配置处理，不显示未配置提示。
      contentBinding.value = null
    }
    if (selectedClass.value !== (next.selected_class ?? '')) {
      selectedClass.value = next.selected_class ?? next.class_names?.[0] ?? ''
    }
    loadState.value = 'ready'
    void ensureTracked(next.active_job_id)
  } catch {
    if (generation !== loadGeneration || sessionStore.selectedSessionId !== sessionId) return
    loadState.value = 'error'
    errorMessage.value = '班级报告状态暂时无法读取，请稍后重试。'
  }
}

async function ensureTracked(id: number | null): Promise<void> {
  if (id === null) return
  const existing = jobStore.jobs[id]
  if (existing !== undefined && !TERMINAL_JOB_STATUSES.has(existing.status)) return
  try {
    const job = await jobApi.getJob(id)
    if (jobStore.jobs[id] !== undefined) return
    if (!TERMINAL_JOB_STATUSES.has(job.status)) jobStore.track(job)
  } catch {
    // 任务可能已结束或被清理；下次刷新时按最新状态展示。
  }
}

function selectClass(name: string): void {
  if (name === selectedClass.value) return
  selectedClass.value = name
  void router.replace({
    query: { ...route.query, class: name || undefined },
  })
  void load()
}

function backToResults(): void {
  void router.push({
    path: '/results',
    query: {
      tab: 'overview',
      ...(sessionStore.selectedSessionId === null
        ? {}
        : { session: String(sessionStore.selectedSessionId) }),
      ...(stringQuery(route.query.class) === null
        ? {}
        : { class: stringQuery(route.query.class) }),
    },
  })
}



onBeforeUnmount(() => {
  loadGeneration += 1
  loadController?.abort()
})
</script>

<template>
  <section class="class-report" aria-labelledby="class-report-title">
    <PageHeader :title="sessionStore.currentSession?.name ?? '班级报告'" title-id="class-report-title">
      <template #back><BackButton label="成绩中心" @click="backToResults" /></template>
    </PageHeader>

    <StatePanel
      v-if="sessionStore.selectedSessionId === null"
      kind="empty"
      title="请先选择考试"
      description="在左侧栏“当前考试”中选择。"
    />

    <StatePanel
      v-else-if="loadState === 'loading' && !analysis"
      kind="loading"
      title="正在读取班级报告状态"
    />

    <StatePanel
      v-else-if="loadState === 'error'"
      kind="error"
      title="班级报告暂时无法读取"
      :description="errorMessage"
      retry-label="重新加载"
      @retry="load"
    />

    <template v-else-if="analysis">
      <div class="class-report__toolbar">
        <label v-if="classNames.length > 1" class="class-analysis__toggle">
          <span>报告班级</span>
          <select class="app-input"
            :value="selectedClass"
            aria-label="报告班级"
            @change="selectClass(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="name in classNames" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
        <span v-else-if="selectedClass">{{ selectedClass }}</span>
      </div>

      <StatePanel
        v-if="analysis.status === 'no_data' || !reportUrl"
        kind="empty"
        title="当前考试还没有可分析的成绩"
      />

      <template v-else>
        <div v-if="bannerText" class="class-analysis__banner" role="status" data-testid="class-report-banner">
          {{ bannerText }}
        </div>
        <p v-if="narrative" class="class-report__meta">
          <span class="class-analysis__ai-badge">AI 分析 · 仅供参考</span>
          生成于 {{ formatTime(analysis.generated_at) }} · {{ selectedClass }}
        </p>
        <iframe
          :key="reportUrl"
          class="class-report__frame"
          :src="reportUrl"
          :title="`${selectedClass} 班级报告`"
          data-testid="class-report-frame"
        ></iframe>
      </template>
    </template>
  </section>
</template>
