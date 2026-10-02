<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type {
  ResolvedReviewItem,
  ReviewRubricSection,
} from '../../api/review'
import type { ResultsCenterStudent } from '../../api/results-center'
import { translateGradingReason } from '../../utils/grading-reasons'
import {
  classDisplayLabel,
  classRanksOf,
  formatScore,
  isCompleteStudent,
  resolvedItemScore,
} from './results-overview'
import {
  WALKTHROUGH_CATEGORY_LABELS,
  itemOf,
  type WalkthroughCard,
} from './paper-walkthrough'

const props = defineProps<{
  card: WalkthroughCard
  students: readonly ResultsCenterStudent[]
  revealed: boolean
  /** 典型错法组卡片需要的整卷题号（取任一题的答卷条目拿整页图） */
  mediaQuestionId: string
  loadItem: (studentId: number, questionId: string) => Promise<ResolvedReviewItem | null>
  loadRubric: (questionId: string) => Promise<ReviewRubricSection | null>
}>()

const emit = defineEmits<{
  viewed: [studentId: number]
  'go-review': [studentId: number, questionId: string, reviewItemId: string]
  zoom: [urls: string[]]
}>()

const byId = new Map(props.students.map((s) => [s.student_id, s] as const))

// ---- 组内 / 同学间浏览（仅典型错法卡）----
const groupIdx = ref(props.card.groupIndex ?? 0)
const memberIdx = ref(0)
const group = computed(() => props.card.groups?.[groupIdx.value] ?? null)
const shownStudentId = computed(() => (
  group.value ? group.value.studentIds[memberIdx.value]! : props.card.studentId
))
const shownStudent = computed(() => byId.get(shownStudentId.value) ?? null)

function shiftMember(delta: number): void {
  const size = group.value?.studentIds.length ?? 0
  memberIdx.value = Math.min(Math.max(memberIdx.value + delta, 0), size - 1)
}
function selectGroup(index: number): void {
  if (!props.card.groups || index < 0 || index >= props.card.groups.length) return
  groupIdx.value = index
  memberIdx.value = 0
}
defineExpose({ shiftMember, selectGroup })

// ---- 答卷条目与图片 ----
const effectiveQid = computed(() => props.card.questionId ?? props.mediaQuestionId)
const reviewItem = ref<ResolvedReviewItem | null>(null)
const itemPending = ref(false)
const imageFailed = ref<string | null>(null) // 失败提示文本

const rcItem = computed(() => {
  const s = shownStudent.value
  return s && props.card.questionId ? itemOf(s, props.card.questionId) : null
})

const imageUrls = computed<string[]>(() => {
  const media = reviewItem.value?.media
  if (!media) return []
  if (props.card.questionId !== null) return media.crop_url ? [media.crop_url] : []
  return [media.original_front_url, media.original_back_url]
    .filter((url): url is string => typeof url === 'string' && url.length > 0)
})

async function loadCurrent(): Promise<void> {
  const sid = shownStudentId.value
  emit('viewed', sid)
  reviewItem.value = null
  imageFailed.value = null
  itemPending.value = true
  try {
    reviewItem.value = await props.loadItem(sid, effectiveQid.value)
  } catch {
    reviewItem.value = null
  } finally {
    itemPending.value = false
  }
  if (imageUrls.value.length === 0 && !itemPending.value) {
    imageFailed.value = reviewItem.value ? '暂无答卷图' : '答卷记录加载失败'
  }
}
watch(shownStudentId, loadCurrent, { immediate: true })

async function probeImage(url: string): Promise<void> {
  try {
    const res = await fetch(url)
    imageFailed.value = res.status === 410
      ? '原卷已清理，分数和作答记录仍保留'
      : '暂无答卷图'
  } catch {
    imageFailed.value = '暂无答卷图'
  }
}
function onImageError(url: string): void { void probeImage(url) }

// ---- 翻开分数后的面板 ----
const rubric = ref<ReviewRubricSection | null>(null)
watch(() => props.revealed, async (on) => {
  if (!on || props.card.questionId === null) return
  try { rubric.value = await props.loadRubric(props.card.questionId) } catch { rubric.value = null }
}, { immediate: true })

const STATUS_LABELS: Record<string, string> = {
  ai_ready: 'AI 评分',
  ai_review: '待复核',
  teacher_final: '教师确认',
  ungraded: '未评分',
  failed: '评分失败',
}
const statusLabel = computed(() => (
  props.card.questionId === null
    ? ''
    : STATUS_LABELS[reviewItem.value?.score_status ?? rcItem.value?.score_status ?? ''] ?? ''
))

const finalScore = computed(() => (
  props.card.questionId === null
    ? shownStudent.value?.current_score ?? null
    : reviewItem.value?.score_awarded ?? resolvedItemScore(rcItem.value)
))
const finalMax = computed(() => (
  props.card.questionId === null
    ? shownStudent.value?.max_score ?? null
    : reviewItem.value?.max_score ?? rcItem.value?.max_score ?? null
))

const steps = computed(() => {
  const item = reviewItem.value
  const section = rubric.value
  if (!item || !section || props.card.questionId === null) return null
  const review = item.metadata.teacher_review as
    | { revision?: number; steps?: Record<string, unknown>[] }
    | undefined
  const source = item.teacher_locked && review?.revision === item.revision
    ? review.steps
    : item.metadata.step_assessments
  if (!Array.isArray(source)) return null
  const rows = section.points.map((point) => {
    // 步骤记录可能只有 step_id（小问场景），有 part_id 时才要求一致
    const hit = source.find(
      (entry) => entry.step_id === point.step_id
        && (entry.part_id === undefined
          || entry.part_id === null
          || entry.part_id === point.part_id),
    )
    const got = typeof hit?.score_awarded === 'number' ? hit.score_awarded : null
    return { name: point.core_goal, got, max: point.score }
  })
  if (rows.length === 0 || rows.some((row) => row.got === null)) return null
  const sum = rows.reduce((acc, row) => acc + (row.got ?? 0), 0)
  if (finalScore.value === null || sum !== finalScore.value) return null
  return rows
})

const reasonText = computed(() => {
  if (props.card.questionId === null) return null
  const reason = reviewItem.value?.deduction_reason ?? rcItem.value?.review_reason
  return reason ? translateGradingReason(reason) : null
})

const classStats = computed(() => {
  const qid = props.card.questionId
  const s = shownStudent.value
  if (!qid || !s) return null
  const peers = props.students.filter(
    (peer) => (peer.class_name ?? '') === (s.class_name ?? '') && isCompleteStudent(peer),
  )
  let resolved = 0; let full = 0; let sum = 0; let max = 0
  for (const peer of peers) {
    const item = itemOf(peer, qid)
    const score = resolvedItemScore(item)
    if (item === null || score === null) continue
    resolved += 1; sum += score; max += item.max_score
    if (score >= item.max_score) full += 1
  }
  if (resolved === 0) return null
  return { rate: max > 0 ? sum / max : null, fullFrac: full / resolved }
})

const wholeStrip = computed(() => {
  const s = shownStudent.value
  if (!s || props.card.questionId !== null) return null
  return s.items.map((item) => ({
    qid: item.question_id,
    score: resolvedItemScore(item),
    max: item.max_score,
  }))
})
const classRank = computed(() => {
  const s = shownStudent.value
  if (!s) return null
  const peers = props.students.filter(
    (peer) => (peer.class_name ?? '') === (s.class_name ?? ''),
  )
  return classRanksOf(peers).get(s.student_id) ?? null
})

const pct = (v: number) => `${Math.round(v * 100)}%`

function goReview(): void {
  const s = shownStudent.value
  const qid = props.card.questionId
  const rid = reviewItem.value?.review_item_id ?? rcItem.value?.review_item_id
  if (!s || !qid || !rid) return
  emit('go-review', s.student_id, qid, rid)
}
</script>

<template>
  <div class="wtc">
    <div class="wtc__meta">
      <span class="wtc__cat">{{ WALKTHROUGH_CATEGORY_LABELS[card.category] }}</span>
      <template v-if="card.groups?.length">
        <strong>第 {{ card.questionId }} 题 · 本题 {{ card.groups.length }} 种典型错法 · 共 {{ card.lostCount }} 人失分</strong>
      </template>
      <template v-else-if="shownStudent">
        <strong>
          {{ shownStudent.student_name }}
          <small v-if="shownStudent.student_code"> · {{ shownStudent.student_code }}</small>
          <small v-if="shownStudent.class_name"> · {{ classDisplayLabel(shownStudent.class_name) }}</small>
        </strong>
        <span class="wtc__qid">{{ card.questionId === null ? '整卷' : `第 ${card.questionId} 题` }}</span>
      </template>
    </div>

    <div v-if="card.groups?.length" class="wtc__groups">
      <button
        v-for="(g, gi) in card.groups"
        :key="g.label"
        type="button"
        class="wtc__chip"
        :aria-pressed="gi === groupIdx"
        @click="selectGroup(gi)"
      >{{ g.label }} {{ g.studentIds.length }}人</button>
      <span v-if="card.otherCount" class="wtc__other">
        另有 {{ card.otherCount }} 人错法各不相同或未整理
      </span>
    </div>

    <div class="wtc__body">
      <button
        v-if="group"
        type="button"
        class="wtc__nav"
        aria-label="上一名同学"
        :disabled="memberIdx <= 0"
        @click="shiftMember(-1)"
      >‹</button>
      <div class="wtc__paper">
        <p v-if="itemPending" class="wtc__loading">答卷加载中…</p>
        <template v-else-if="imageUrls.length">
          <img
            v-for="url in imageUrls"
            :key="url"
            class="wtc__img"
            :src="url"
            alt="答卷图片"
            @click="emit('zoom', imageUrls)"
            @error="onImageError(url)"
          >
        </template>
        <p v-else class="wtc__loading">{{ imageFailed ?? '暂无答卷图' }}</p>
      </div>
      <button
        v-if="group"
        type="button"
        class="wtc__nav"
        aria-label="下一名同学"
        :disabled="memberIdx >= group.studentIds.length - 1"
        @click="shiftMember(1)"
      >›</button>
    </div>
    <p v-if="group && shownStudent" class="wtc__pos">
      第 {{ memberIdx + 1 }} / {{ group.studentIds.length }} 人 ·
      {{ shownStudent.student_name }}
      {{ shownStudent.class_name ? classDisplayLabel(shownStudent.class_name) : '' }}
    </p>

    <section v-if="revealed" class="wtc__reveal">
      <p class="wtc__score">
        <strong>{{ formatScore(finalScore) }}</strong>
        <small v-if="finalMax !== null">/ {{ formatScore(finalMax) }}</small>
        <em v-if="statusLabel">{{ statusLabel }}</em>
      </p>
      <ul v-if="steps" class="wtc__steps">
        <li v-for="(row, i) in steps" :key="i" :data-ok="row.got !== null && row.got >= row.max">
          {{ row.got !== null && row.got >= row.max ? '✓' : '✗' }}
          {{ row.name }} {{ formatScore(row.got) }}/{{ formatScore(row.max) }}
        </li>
      </ul>
      <ul v-else-if="wholeStrip" class="wtc__steps">
        <li v-for="row in wholeStrip" :key="row.qid" :data-ok="row.score !== null && row.score >= row.max">
          {{ row.score !== null && row.score >= row.max ? '✓' : '✗' }}
          {{ row.qid }} {{ formatScore(row.score) }}/{{ formatScore(row.max) }}
        </li>
      </ul>
      <p v-if="card.questionId === null && classRank" class="wtc__note">
        班内名次第 {{ classRank.rank }} / {{ classRank.size }} 名
      </p>
      <p v-if="classStats && classStats.rate !== null" class="wtc__note">
        本题本班得分率 {{ pct(classStats.rate) }} · 满分 {{ pct(classStats.fullFrac) }}
      </p>
      <p v-if="reasonText" class="wtc__note">{{ reasonText }}</p>
      <p class="wtc__why">入选理由：{{ card.reason }}</p>
      <button
        v-if="card.questionId !== null"
        type="button"
        class="wtc__review"
        @click="goReview"
      >去复核 / 改分</button>
    </section>
    <p v-else class="wtc__hint">先看作答，想一想这题能得几分 · 空格 翻开分数</p>
  </div>
</template>

<style scoped>
.wtc { display: flex; flex-direction: column; gap: 8px; flex: 1; min-height: 0; }
.wtc__meta { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.wtc__cat {
  background: var(--color-accent-subtle); color: var(--color-accent-hover);
  border-radius: 999px; padding: 2px 10px; font-size: 12px; flex-shrink: 0;
}
.wtc__qid { color: var(--color-text-secondary); font-size: 13px; }
.wtc__groups { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.wtc__chip {
  border: 1px solid var(--color-border-default); background: var(--card);
  border-radius: 999px; padding: 3px 10px; font-size: 12px; cursor: pointer;
}
.wtc__chip[aria-pressed="true"] {
  border-color: var(--color-accent); color: var(--color-accent-hover);
  background: var(--color-accent-subtle);
}
.wtc__other { color: var(--color-text-muted); font-size: 12px; }
.wtc__body { display: flex; align-items: stretch; gap: 8px; flex: 1; min-height: 0; }
.wtc__nav {
  flex-shrink: 0; width: 44px; font-size: 34px; border: 1px solid var(--color-border-default);
  border-radius: 10px; background: var(--card); cursor: pointer;
  color: var(--color-text-secondary);
}
.wtc__nav:disabled { opacity: 0.3; cursor: default; }
.wtc__paper {
  flex: 1; min-height: 0; display: flex; gap: 8px; overflow: auto;
  background: var(--color-bg-subtle); border: 1px solid var(--color-border-subtle);
  border-radius: 10px; padding: 10px; justify-content: center; align-items: flex-start;
}
.wtc__img {
  max-width: 100%; max-height: 100%; object-fit: contain; cursor: zoom-in;
  background: #fff; border-radius: 4px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}
.wtc__loading { margin: auto; color: var(--color-text-muted); font-size: 14px; }
.wtc__pos { margin: 0; text-align: center; font-size: 13px; color: var(--color-text-secondary); }
.wtc__reveal {
  border: 1px solid var(--color-border-subtle); border-radius: 10px;
  background: var(--card); padding: 10px 14px; font-size: 13px;
  max-height: 38%; overflow: auto;
}
.wtc__score { margin: 0 0 4px; display: flex; align-items: baseline; gap: 8px; }
.wtc__score strong { font-size: 22px; color: var(--color-accent-hover); }
.wtc__score em { font-style: normal; font-size: 12px; color: var(--color-text-muted); }
.wtc__steps { margin: 4px 0; padding: 0; list-style: none; font-size: 13px; }
.wtc__steps li[data-ok="true"] { color: var(--color-accent-hover); }
.wtc__steps li[data-ok="false"] { color: var(--color-danger); }
.wtc__note { margin: 4px 0; color: var(--color-text-secondary); }
.wtc__why { margin: 4px 0; color: var(--color-text-muted); font-size: 12px; }
.wtc__review {
  margin-top: 6px; border: 1px solid var(--color-accent); color: var(--color-accent-hover);
  background: transparent; border-radius: 8px; padding: 4px 14px; cursor: pointer;
}
.wtc__hint { margin: 0; text-align: center; color: var(--color-text-muted); font-size: 13px; }
</style>
