<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'

import { AnimatedCircularProgressBar } from '@/components/ui/animated-circular-progress-bar'
import { NumberTicker } from '@/components/ui/number-ticker'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useSessionStore } from '../stores/session'
import { useWorkbenchStore } from '../stores/workbench'
import '../styles/workbench.css'

const router = useRouter()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const workbenchStore = useWorkbenchStore()

interface WorkbenchFocusItem {
  id: 'exam'
  context: string
  title: string
  detail: string
  action: string
  path: string
}

const todayLabel = new Intl.DateTimeFormat('zh-CN', {
  month: 'long',
  day: 'numeric',
  weekday: 'long',
}).format(new Date())

const greeting = (() => {
  const hour = new Date().getHours()
  if (hour < 11) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})()

const currentTermLabel = computed(
  () => curriculumScope.selectedVolume?.label ?? '未限定教学学期',
)

const currentExamName = computed(
  () => workbenchStore.overview?.current_session?.name
    ?? sessionStore.currentSession?.name
    ?? '尚未选择考试',
)

const progressPercent = computed(
  () => workbenchStore.overview?.progress?.progress_percent ?? null,
)

const workbenchFocusItems = computed<WorkbenchFocusItem[]>(() => {
  const sessionId = sessionStore.selectedSessionId
  const progress = workbenchStore.overview?.progress
  const review = workbenchStore.overview?.review
  let examItem: WorkbenchFocusItem

  if (sessionId === null) {
    examItem = {
      id: 'exam',
      context: '考试与阅卷',
      title: '先选择或创建一场考试',
      detail: '选定考试后，这里会显示批改、复核和异常处理中最优先的一项。',
      action: '配置考试',
      path: '/sessions',
    }
  } else if (progress !== null && progress !== undefined && progress.progress_percent < 100) {
    examItem = {
      id: 'exam',
      context: `考试批改 · ${currentExamName.value}`,
      title: `继续完成 ${progress.graded_papers} / ${progress.total_papers} 份批改`,
      detail: review?.item_count
        ? `还有 ${review.item_count} 项需要教师复核，完成后再进入本次学情。`
        : '先完成剩余答卷，系统会持续汇总本次考试学情。',
      action: '继续批改',
      path: `/sessions/${sessionId}/grading-run`,
    }
  } else if ((review?.item_count ?? 0) > 0) {
    examItem = {
      id: 'exam',
      context: `教师复核 · ${currentExamName.value}`,
      title: `处理 ${review?.item_count ?? 0} 项待复核内容`,
      detail: '核对低置信度评分与异常结果，教师确认后再作为正式成绩。',
      action: '开始复核',
      path: '/grading',
    }
  } else {
    examItem = {
      id: 'exam',
      context: `考试学情 · ${currentExamName.value}`,
      title: '查看本次考试的知识与能力表现',
      detail: '批改已完成，可以从知识证据继续安排训练或下一节课。',
      action: '查看学情',
      path: '/knowledge-graph',
    }
  }

  return [examItem]
})

const workflowSteps = computed(() => [{
  label: '考试',
  status: sessionStore.selectedSessionId === null ? '待选择' : currentExamName.value,
  path: '/sessions',
}, {
  label: '批改',
  status: progressPercent.value === null ? '等待考试' : `${progressPercent.value}%`,
  path: sessionStore.selectedSessionId === null
    ? '/grading'
    : `/sessions/${sessionStore.selectedSessionId}/grading-run`,
}, {
  label: '学情',
  status: progressPercent.value === 100 ? '可查看' : '随批改更新',
  path: '/knowledge-graph',
}, {
  label: '训练',
  status: curriculumScope.selectedVolumeId ? '按本学期筛选' : '显示全部',
  path: '/question-assembly',
}])

const anomalyCount = computed(() => {
  const value = workbenchStore.overview?.anomalies
  return value === null || value === undefined
    ? null
    : value.unmatched_papers + value.scan_issue_students + value.failed_papers
})

const gradedPaperCount = computed(() => workbenchStore.overview?.progress?.graded_papers ?? null)
const reviewItemCount = computed(() => workbenchStore.overview?.review?.item_count ?? null)

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => {
    workbenchStore.resetForSession(sessionId)
    void workbenchStore.loadOverview(sessionId)
  },
  { immediate: true },
)

function formatTime(value: string | null): string {
  return value?.replace('T', ' ').replace('Z', '') ?? '时间暂不可用'
}

function openPath(path: string): void {
  void router.push(path)
}

function retryOverview(): void {
  void workbenchStore.loadOverview(sessionStore.selectedSessionId)
}
</script>

<template>
  <section class="workbench-view" aria-labelledby="workbench-title">
    <header class="workbench-home-hero">
      <div class="workbench-home-hero__copy">
        <p class="workbench-home-kicker">{{ todayLabel }}</p>
        <h1 id="workbench-title" tabindex="-1">{{ greeting }}，今天先完成这两件事</h1>
        <p>{{ currentTermLabel }}{{ curriculumScope.selectedVolumeId ? '已作为当前教学学期' : ' · 当前显示全部学期内容' }}</p>
      </div>
      <div class="workbench-home-hero__summary" aria-label="今日工作概况">
        <strong>2</strong>
        <span>项优先工作</span>
        <small>按教学影响排序</small>
      </div>
    </header>

    <div v-if="workbenchStore.overviewState === 'stale-error'" class="workbench-stale" role="alert">
      <span>考试数据可能不是最新 · 上次更新 {{ formatTime(workbenchStore.overviewUpdatedAt) }}</span>
      <button type="button" class="workbench-link-button" @click="retryOverview">重新加载考试概况</button>
    </div>
    <div v-else-if="workbenchStore.overviewState === 'error'" class="workbench-inline-error" role="alert">
      <p>考试概况暂时无法读取；班务入口仍可使用。</p>
      <button type="button" class="workbench-secondary-button" @click="retryOverview">重新加载考试概况</button>
    </div>

    <div class="workbench-home-dashboard">
      <section class="workbench-focus-board" aria-labelledby="workbench-focus-title">
        <header class="workbench-home-section-heading">
          <div>
            <p class="workbench-home-kicker">TEACHING FOCUS</p>
            <h2 id="workbench-focus-title">今日焦点</h2>
          </div>
          <span>按影响排序</span>
        </header>
        <ol class="workbench-focus-list">
          <li
            v-for="(item, index) in workbenchFocusItems"
            :key="item.id"
            :class="{ 'is-primary': index === 0 }"
          >
            <span class="workbench-focus-list__number">{{ String(index + 1).padStart(2, '0') }}</span>
            <div>
              <small>{{ item.context }}</small>
              <h3>{{ item.title }}</h3>
              <p>{{ item.detail }}</p>
            </div>
            <button type="button" class="workbench-focus-list__action" @click="openPath(item.path)">
              {{ item.action }} <span aria-hidden="true">→</span>
            </button>
          </li>
        </ol>
      </section>

      <aside class="workbench-pulse" aria-labelledby="workbench-pulse-title">
        <header class="workbench-home-section-heading">
          <div>
            <p class="workbench-home-kicker">CURRENT EXAM</p>
            <h2 id="workbench-pulse-title">教学脉搏</h2>
          </div>
        </header>
        <p class="workbench-pulse__exam">{{ currentExamName }}</p>
        <div class="workbench-pulse__score">
          <AnimatedCircularProgressBar
            v-if="progressPercent !== null"
            class="workbench-pulse__ring size-28 text-4xl"
            :value="progressPercent"
            gauge-primary-color="var(--color-accent)"
            gauge-secondary-color="var(--color-border-strong)"
            :circle-stroke-width="8"
          />
          <strong v-else>—</strong>
          <span>{{ progressPercent === null ? '尚无批改进度' : '% 已批改' }}</span>
        </div>
        <div class="workbench-pulse__track" aria-hidden="true">
          <span :style="{ width: `${progressPercent ?? 0}%` }" />
        </div>
        <dl class="workbench-pulse__stats">
          <div>
            <dt>已批改</dt>
            <dd><NumberTicker v-if="gradedPaperCount !== null" :value="gradedPaperCount" /><template v-else>—</template><small>份</small></dd>
          </div>
          <div>
            <dt>待复核</dt>
            <dd><NumberTicker v-if="reviewItemCount !== null" :value="reviewItemCount" /><template v-else>—</template><small>项</small></dd>
          </div>
          <div>
            <dt>异常</dt>
            <dd><NumberTicker v-if="anomalyCount !== null" :value="anomalyCount" /><template v-else>—</template><small>条</small></dd>
          </div>
        </dl>
        <button type="button" class="workbench-pulse__link" @click="openPath('/knowledge-graph')">
          查看完整学情证据 →
        </button>
      </aside>
    </div>

    <section class="workbench-workflow" aria-labelledby="workbench-workflow-title">
      <div class="workbench-workflow__intro">
        <p class="workbench-home-kicker">ONE CONTINUOUS LOOP</p>
        <h2 id="workbench-workflow-title">从一次考试，走到下一堂课</h2>
      </div>
      <ol class="workbench-workflow__steps">
        <li v-for="(step, index) in workflowSteps" :key="step.label">
          <button type="button" @click="openPath(step.path)">
            <span>{{ String(index + 1).padStart(2, '0') }}</span>
            <strong>{{ step.label }}</strong>
            <small>{{ step.status }}</small>
          </button>
        </li>
      </ol>
    </section>
  </section>
</template>
