<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  classAnalysisApi,
  type ClassAnalysisResponse,
} from '../../api/class-analysis'
import type { ResultsCenterResponse } from '../../api/results-center'
import { Skeleton } from '../ui/skeleton'
import {
  OVERVIEW_BANDS,
  attentionStudents,
  blankCountsByStudent,
  buildFindings,
  classDisplayLabel,
  classKeysOf,
  formatRate,
  formatScore,
  questionRatesFor,
  summarizeStudents,
  topCauseOf,
  type ClassSummary,
  type OverviewBandId,
} from './results-overview'

const props = defineProps<{
  results: ResultsCenterResponse
  sessionId: number
  scope?: string | null
}>()

const emit = defineEmits<{
  'open-student': [studentId: number]
  'open-question': [questionId: string, className: string | null]
  'open-filter': [filter: 'attention']
  'update:scope': [scope: string | null]
}>()

const classKeys = computed(() => classKeysOf(props.results.students))

const scope = computed<string | null>({
  get: () => (props.scope !== null && props.scope !== undefined
    && classKeys.value.includes(props.scope)
    ? props.scope
    : null),
  set: (value) => emit('update:scope', value),
})

const sortBy = ref<'rate' | 'number'>('rate')
const analysis = ref<ClassAnalysisResponse | null>(null)
const analysisState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const mergedAnalysis = ref<ClassAnalysisResponse | null>(null)
const attentionSection = ref<HTMLElement | null>(null)

const analysisCache = new Map<string, ClassAnalysisResponse>()
let loadGeneration = 0
let scopeController: AbortController | null = null
let mergedController: AbortController | null = null

watch(
  [scope, () => props.sessionId],
  ([scopeKey, sessionId], previous) => {
    if (previous?.[1] !== sessionId) {
      mergedController?.abort()
      mergedController = null
      mergedAnalysis.value = analysisCache.get(`${sessionId}::`) ?? null
    }
    void loadScope(scopeKey, sessionId)
    if (scopeKey !== null) void ensureMerged(sessionId)
  },
  { immediate: true },
)

async function loadScope(
  scopeKey: string | null,
  sessionId: number,
): Promise<void> {
  // '' 的 class_name 在后端表示合并全部班级；范围 null 与未分班都落到同一请求。
  const apiScope = scopeKey ?? ''
  const cacheKey = `${sessionId}::${apiScope}`
  const generation = ++loadGeneration
  scopeController?.abort()
  const cached = analysisCache.get(cacheKey)
  if (cached) {
    analysis.value = cached
    analysisState.value = 'ready'
    if (apiScope === '') mergedAnalysis.value = cached
    return
  }
  analysis.value = null
  analysisState.value = 'loading'
  const controller = new AbortController()
  scopeController = controller
  try {
    const result = await classAnalysisApi.getClassAnalysis(
      sessionId,
      controller.signal,
      apiScope,
      'summary',
    )
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    analysisCache.set(cacheKey, result)
    analysis.value = result
    analysisState.value = 'ready'
    if (apiScope === '') mergedAnalysis.value = result
  } catch {
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    analysisState.value = 'error'
  }
}

async function ensureMerged(sessionId: number): Promise<void> {
  if (mergedAnalysis.value || mergedController) return
  const cacheKey = `${sessionId}::`
  const cached = analysisCache.get(cacheKey)
  if (cached) {
    mergedAnalysis.value = cached
    return
  }
  const controller = new AbortController()
  mergedController = controller
  try {
    const result = await classAnalysisApi.getClassAnalysis(
      sessionId,
      controller.signal,
      '',
      'summary',
    )
    analysisCache.set(cacheKey, result)
    if (!controller.signal.aborted && props.sessionId === sessionId) {
      mergedAnalysis.value = result
    }
  } catch {
    // 缺考列保持「—」，不阻塞其余内容。
  } finally {
    if (mergedController === controller) mergedController = null
  }
}

onBeforeUnmount(() => {
  loadGeneration += 1
  scopeController?.abort()
  mergedController?.abort()
})

const studentsInScope = computed(() => (scope.value === null
  ? props.results.students
  : props.results.students.filter(
    (student) => (student.class_name ?? '') === scope.value,
  )))

const absentCounts = computed(() => {
  const counts = new Map<string, number>()
  for (const item of mergedAnalysis.value?.data?.skipped ?? []) {
    if (item.reason !== '缺考') continue
    counts.set(item.class_name, (counts.get(item.class_name) ?? 0) + 1)
  }
  return counts
})

const classSummaries = computed<ClassSummary[]>(() => classKeys.value.map(
  (key) => summarizeStudents(
    props.results.students.filter(
      (student) => (student.class_name ?? '') === key,
    ),
    key,
    mergedAnalysis.value ? (absentCounts.value.get(key) ?? 0) : null,
  ),
))

const classRows = computed<ClassSummary[]>(() => {
  const rows = [...classSummaries.value]
  if (rows.length >= 2) {
    rows.push(summarizeStudents(
      props.results.students,
      null,
      mergedAnalysis.value
        ? (mergedAnalysis.value.data?.skipped ?? [])
          .filter((item) => item.reason === '缺考').length
        : null,
    ))
  }
  return rows
})

const questionIds = computed(() => props.results.questions.map(
  (question) => question.question_id,
))

const scopeQuestionRates = computed(() => questionRatesFor(
  studentsInScope.value,
  questionIds.value,
))

const classQuestionRates = computed(() => new Map(
  classKeys.value.map((key) => [
    key,
    questionRatesFor(
      props.results.students.filter(
        (student) => (student.class_name ?? '') === key,
      ),
      questionIds.value,
    ),
  ]),
))

const scopeAnalysisQuestions = computed(() => (
  analysisState.value === 'ready'
    ? analysis.value?.data?.questions ?? []
    : null
))

const causeIssue = computed<'missing' | 'legacy' | null>(() => {
  if (analysisState.value === 'error') return 'missing'
  if (analysisState.value !== 'ready') return null
  const questions = analysis.value?.data?.questions ?? []
  const hasStructured = questions.some(
    (question) => (question.causes ?? []).some((cause) => cause.kind !== undefined),
  )
  if (hasStructured) return null
  return questions.some((question) => (question.causes ?? []).length > 0)
    ? 'legacy'
    : 'missing'
})

const findings = computed(() => buildFindings({
  scopeKey: scope.value,
  scopeStudents: studentsInScope.value,
  classes: classSummaries.value,
  questionRates: scopeQuestionRates.value,
  classQuestionRates: scope.value === null ? classQuestionRates.value : undefined,
  analysisQuestions: scopeAnalysisQuestions.value,
}))

interface QuestionRow {
  questionId: string
  maxScore: number
  rate: number | null
  stem: string | null
  causeLabel: string | null
  causeCount: number | null
  causeLegacy: boolean
  categories: { category: string; count: number }[]
  classRates: { key: string; label: string; rate: number | null; isLow: boolean }[]
}

const CLASS_RATE_GAP = 0.15

const questionRows = computed<QuestionRow[]>(() => {
  const analysisById = new Map(
    (scopeAnalysisQuestions.value ?? []).map(
      (question) => [question.question_id, question] as const,
    ),
  )
  const perClass = scope.value === null && classKeys.value.length >= 2
    ? classKeys.value.map((key) => ({
      key,
      label: classDisplayLabel(key),
      rates: classQuestionRates.value.get(key)!,
    }))
    : []
  const rows = props.results.questions.map((question) => {
    const analysisQuestion = analysisById.get(question.question_id)
    const topCause = analysisQuestion ? topCauseOf(analysisQuestion) : null
    const classRates = perClass.map((entry) => ({
      key: entry.key,
      label: entry.label,
      rate: entry.rates.get(question.question_id) ?? null,
      isLow: false,
    }))
    const validRates = classRates
      .map((entry) => entry.rate)
      .filter((rate): rate is number => rate !== null)
    const minRate = validRates.length ? Math.min(...validRates) : null
    const maxRate = validRates.length ? Math.max(...validRates) : null
    if (minRate !== null && maxRate !== null && maxRate - minRate >= CLASS_RATE_GAP) {
      for (const entry of classRates) entry.isLow = entry.rate === minRate
    }
    return {
      questionId: question.question_id,
      maxScore: question.max_score,
      rate: scopeQuestionRates.value.get(question.question_id) ?? null,
      stem: analysisQuestion?.stem_summary ?? null,
      causeLabel: topCause !== null && topCause !== 'legacy' ? topCause.label : null,
      causeCount: topCause !== null && topCause !== 'legacy' ? topCause.count : null,
      causeLegacy: topCause === 'legacy',
      categories: (analysisQuestion?.cause_category_counts ?? []).slice(0, 2),
      classRates,
    }
  })
  if (sortBy.value === 'rate') {
    rows.sort((left, right) => (
      left.rate === null ? 1 : right.rate === null ? -1 : left.rate - right.rate
    ))
  }
  return rows
})

const attention = computed(() => attentionStudents(studentsInScope.value))

const blanks = computed(() => (
  analysisState.value === 'ready' && analysis.value?.data
    ? blankCountsByStudent(analysis.value.data.questions)
    : null
))

const pendingParts = computed(() => {
  const summary = props.results.summary
  const parts: string[] = []
  if (summary.needs_review_item_count > 0) {
    parts.push(`${summary.needs_review_item_count} 题待复核`)
  }
  if (summary.ungraded_item_count > 0) {
    parts.push(`${summary.ungraded_item_count} 题未评分`)
  }
  if (summary.failed_item_count > 0) {
    parts.push(`${summary.failed_item_count} 题处理失败`)
  }
  return parts
})

function bandTotal(summary: ClassSummary): number {
  return OVERVIEW_BANDS.reduce(
    (total, band) => total + summary.bandCounts[band.id],
    0,
  )
}

function bandWidth(summary: ClassSummary, bandId: OverviewBandId): string {
  const total = bandTotal(summary)
  return total === 0
    ? '0%'
    : `${(summary.bandCounts[bandId] / total) * 100}%`
}

function bandAriaLabel(summary: ClassSummary): string {
  return OVERVIEW_BANDS.map(
    (band) => `${band.label} ${summary.bandCounts[band.id]} 人`,
  ).join('，')
}

function isActiveRow(row: ClassSummary): boolean {
  return row.key === scope.value
}

function rateTone(rate: number | null): 'low' | 'mid' | 'high' | 'none' {
  if (rate === null) return 'none'
  if (rate < 0.4) return 'low'
  if (rate < 0.7) return 'mid'
  return 'high'
}

function rateWidth(rate: number | null): string {
  return rate === null ? '0%' : `${Math.max(0, Math.min(1, rate)) * 100}%`
}

function openQuestion(questionId: string): void {
  emit('open-question', questionId, scope.value)
}

function scrollToAttention(): void {
  attentionSection.value?.scrollIntoView({ block: 'start' })
}

function blankCountFor(studentId: number): number {
  return blanks.value?.get(studentId) ?? 0
}
</script>

<template>
  <div class="overview" data-testid="results-overview">
    <div class="overview__controls">
      <div class="overview__scope" role="group" aria-label="统计范围">
        <button
          type="button"
          :class="{ 'is-active': scope === null }"
          :aria-pressed="scope === null"
          @click="scope = null"
        >全部</button>
        <button
          v-for="key in classKeys"
          :key="key"
          type="button"
          :class="{ 'is-active': scope === key }"
          :aria-pressed="scope === key"
          @click="scope = key"
        >{{ classDisplayLabel(key) }}</button>
      </div>
      <p v-if="pendingParts.length" class="overview__warning">
        <span>还有 {{ pendingParts.join(' · ') }}，以下统计只含已有成绩</span>
        <button
          type="button"
          class="overview__link"
          @click="emit('open-filter', 'attention')"
        >去处理</button>
      </p>
    </div>

    <section class="overview__section" aria-labelledby="overview-classes-title">
      <h2 id="overview-classes-title" class="overview__title">班级对比</h2>
      <div class="overview__table-wrap">
        <table class="overview__table">
          <thead>
            <tr>
              <th scope="col">班级</th>
              <th scope="col">参考</th>
              <th scope="col">缺考</th>
              <th scope="col">平均</th>
              <th scope="col">中位</th>
              <th scope="col">最高/最低</th>
              <th scope="col">及格率</th>
              <th scope="col">优秀率</th>
              <th scope="col">分数分布</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in classRows"
              :key="row.key ?? '__all__'"
              :class="{ 'is-active': isActiveRow(row), 'is-total': row.key === null }"
            >
              <th scope="row">
                <button
                  type="button"
                  class="overview__link overview__class-link"
                  :aria-pressed="isActiveRow(row)"
                  @click="scope = row.key"
                >{{ row.label }}</button>
              </th>
              <td>{{ row.studentCount }}</td>
              <td>{{ row.absentCount === null ? '—' : row.absentCount }}</td>
              <td>{{ formatScore(row.average) }}</td>
              <td>{{ formatScore(row.median) }}</td>
              <td>{{ formatScore(row.highest) }} / {{ formatScore(row.lowest) }}</td>
              <td>{{ formatRate(row.passRate) }}</td>
              <td>{{ formatRate(row.excellentRate) }}</td>
              <td>
                <span
                  class="overview__bandbar"
                  role="img"
                  :aria-label="bandAriaLabel(row)"
                >
                  <i
                    v-for="band in OVERVIEW_BANDS"
                    :key="band.id"
                    :data-band="band.id"
                    :style="{ width: bandWidth(row, band.id) }"
                    :title="`${band.label} ${row.bandCounts[band.id]} 人`"
                  ></i>
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="overview__legend">
        <span v-for="band in OVERVIEW_BANDS" :key="band.id">
          <i :data-band="band.id" aria-hidden="true"></i>{{ band.label }} {{ band.range }}
        </span>
      </p>
    </section>

    <section class="overview__section" aria-labelledby="overview-findings-title">
      <h2 id="overview-findings-title" class="overview__title">本次重点</h2>
      <ol v-if="findings.length" class="overview__findings">
        <li v-for="finding in findings" :key="finding.id">
          <template
            v-for="(segment, index) in finding.segments"
            :key="index"
          >
            <button
              v-if="segment.type === 'question'"
              type="button"
              class="overview__link"
              @click="openQuestion(segment.questionId)"
            >{{ segment.questionId }}</button>
            <strong v-else-if="segment.type === 'strong'">{{ segment.text }}</strong>
            <button
              v-else-if="segment.type === 'action'"
              type="button"
              class="overview__link overview__finding-action"
              @click="scrollToAttention"
            >{{ segment.label }}</button>
            <span v-else>{{ segment.text }}</span>
          </template>
        </li>
      </ol>
      <Skeleton
        v-if="analysisState === 'loading'"
        class="overview__skeleton-line"
        aria-hidden="true"
      />
      <p v-if="causeIssue === 'missing'" class="overview__missing">
        错因尚未整理
        <button
          type="button"
          class="overview__link"
          @click="emit('open-question', '', scope)"
        >去试题诊断查看</button>
      </p>
      <p v-else-if="causeIssue === 'legacy'" class="overview__missing">
        错因为旧版整理
        <button
          type="button"
          class="overview__link"
          @click="emit('open-question', '', scope)"
        >去试题诊断查看</button>
      </p>
      <p
        v-if="!findings.length && analysisState !== 'loading' && !causeIssue"
        class="overview__empty"
      >当前范围没有发现需要重点提示的问题。</p>
    </section>

    <section class="overview__section" aria-labelledby="overview-questions-title">
      <div class="overview__section-head">
        <h2 id="overview-questions-title" class="overview__title">题目得分率</h2>
        <div class="overview__sort" role="group" aria-label="题目排序">
          <button
            type="button"
            :class="{ 'is-active': sortBy === 'rate' }"
            :aria-pressed="sortBy === 'rate'"
            @click="sortBy = 'rate'"
          >按得分率</button>
          <button
            type="button"
            :class="{ 'is-active': sortBy === 'number' }"
            :aria-pressed="sortBy === 'number'"
            @click="sortBy = 'number'"
          >按题号</button>
        </div>
      </div>
      <ol class="overview__questions">
        <li v-for="row in questionRows" :key="row.questionId">
          <button
            type="button"
            class="overview__question"
            :data-question-id="row.questionId"
            @click="openQuestion(row.questionId)"
          >
            <span class="overview__qid">{{ row.questionId }}</span>
            <span class="overview__stem" :title="row.stem ?? undefined">
              <Skeleton
                v-if="analysisState === 'loading'"
                class="overview__skeleton-text"
                aria-hidden="true"
              />
              <template v-else>{{ row.stem ?? '—' }}</template>
            </span>
            <span class="overview__rate">
              <span
                class="overview__ratebar"
                :class="`overview__ratebar--${rateTone(row.rate)}`"
                aria-hidden="true"
              ><i :style="{ width: rateWidth(row.rate) }"></i></span>
              <strong>{{ formatRate(row.rate) }}</strong>
              <span
                v-if="row.classRates.length"
                class="overview__classrates"
              ><template
                v-for="(entry, index) in row.classRates"
                :key="entry.key"
              ><template v-if="index"> · </template><span
                :class="{ 'is-gap': entry.isLow }"
              >{{ entry.label }} {{ formatRate(entry.rate) }}</span></template></span>
            </span>
            <span class="overview__cause">
              <Skeleton
                v-if="analysisState === 'loading'"
                class="overview__skeleton-text"
                aria-hidden="true"
              />
              <template v-else-if="row.causeLegacy">
                <span class="overview__cause-legacy">旧版错因 · 去试题诊断更新</span>
              </template>
              <template v-else-if="row.causeLabel !== null">
                {{ row.causeLabel }} <b>{{ row.causeCount }} 人</b>
              </template>
              <template v-else>—</template>
            </span>
            <span class="overview__cats">
              <span
                v-for="category in row.categories"
                :key="category.category"
                class="overview__cat"
              >{{ category.category }} {{ category.count }}人</span>
            </span>
          </button>
        </li>
      </ol>
    </section>

    <section
      ref="attentionSection"
      class="overview__section"
      aria-labelledby="overview-attention-title"
    >
      <h2 id="overview-attention-title" class="overview__title">需要关注的学生</h2>
      <div class="overview__attention">
        <div class="overview__attention-group">
          <h3>低分（低于 40%）· {{ attention.low.length }} 人</h3>
          <p v-if="!attention.low.length" class="overview__empty">无</p>
          <ul v-else class="overview__chips">
            <li v-for="entry in attention.low" :key="entry.student.student_id">
              <button
                type="button"
                class="overview__chip"
                @click="emit('open-student', entry.student.student_id)"
              >
                <strong>{{ entry.student.student_name }}</strong>
                <span v-if="scope === null">
                  {{ classDisplayLabel(entry.student.class_name) }} ·
                </span>
                {{ formatScore(entry.student.current_score) }}/{{ formatScore(entry.student.max_score) }}
                · 0分 {{ entry.zeroCount }} 题<template v-if="blanks && blankCountFor(entry.student.student_id) > 0">
                  · 空白 {{ blankCountFor(entry.student.student_id) }}
                </template>
              </button>
            </li>
          </ul>
        </div>
        <div class="overview__attention-group">
          <h3>差一点及格（50%–60%）· {{ attention.nearPass.length }} 人</h3>
          <p v-if="!attention.nearPass.length" class="overview__empty">无</p>
          <ul v-else class="overview__chips">
            <li v-for="entry in attention.nearPass" :key="entry.student.student_id">
              <button
                type="button"
                class="overview__chip"
                @click="emit('open-student', entry.student.student_id)"
              >
                <strong>{{ entry.student.student_name }}</strong>
                <span v-if="scope === null">
                  {{ classDisplayLabel(entry.student.class_name) }} ·
                </span>
                {{ formatScore(entry.student.current_score) }}/{{ formatScore(entry.student.max_score) }}
                · 0分 {{ entry.zeroCount }} 题<template v-if="blanks && blankCountFor(entry.student.student_id) > 0">
                  · 空白 {{ blankCountFor(entry.student.student_id) }}
                </template>
              </button>
            </li>
          </ul>
        </div>
      </div>
    </section>
  </div>
</template>
