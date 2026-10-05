<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type {
  ResolvedReviewItem,
  ReviewRubricSection,
} from '../../api/review'
import type { ResultsCenterStudent } from '../../api/results-center'
import { translateGradingReason } from '../../utils/grading-reasons'
import ReviewAnswerPanel from '../review/ReviewAnswerPanel.vue'
import {
  classDisplayLabel,
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
  sessionId: number
  revealed: boolean
  loadItem: (studentId: number, questionId: string) => Promise<ResolvedReviewItem | null>
  loadRubric: (questionId: string) => Promise<ReviewRubricSection | null>
}>()

const emit = defineEmits<{
  viewed: [studentId: number]
  'go-review': [studentId: number, questionId: string, reviewItemId: string]
  zoom: [urls: string[]]
}>()

const byId = new Map(props.students.map((s) => [s.student_id, s] as const))

// ---- 组内 / 同学间浏览 ----
const groupIdx = ref(0)
const memberIdx = ref(0)
const group = computed(() => props.card.groups[groupIdx.value] ?? null)
const member = computed(() => group.value?.members[memberIdx.value] ?? null)
const shownStudentId = computed(() => member.value?.studentId ?? 0)
const shownStudent = computed(() => byId.get(shownStudentId.value) ?? null)

function shiftMember(delta: number): void {
  const size = group.value?.members.length ?? 0
  memberIdx.value = Math.min(Math.max(memberIdx.value + delta, 0), size - 1)
}
function selectGroup(index: number): void {
  if (index < 0 || index >= props.card.groups.length) return
  groupIdx.value = index
  memberIdx.value = 0
}
defineExpose({ shiftMember, selectGroup })

// ---- 答卷条目与图片 ----
const effectiveQid = computed(() => member.value?.questionId ?? '')
const reviewItem = ref<ResolvedReviewItem | null>(null)
const itemPending = ref(false)
const imageFailed = ref<string | null>(null) // 失败提示文本
let itemGeneration = 0

const rcItem = computed(() => {
  const s = shownStudent.value
  return s && member.value ? itemOf(s, member.value.questionId) : null
})

const imageUrls = computed<string[]>(() => {
  const url = reviewItem.value?.media?.crop_url
  return url ? [url] : []
})

async function loadCurrent(): Promise<void> {
  const generation = ++itemGeneration
  const sid = shownStudentId.value
  emit('viewed', sid)
  reviewItem.value = null
  imageFailed.value = null
  itemPending.value = true
  try {
    const item = await props.loadItem(sid, effectiveQid.value)
    if (generation !== itemGeneration) return
    reviewItem.value = item
  } catch {
    if (generation !== itemGeneration) return
    reviewItem.value = null
  } finally {
    if (generation === itemGeneration) itemPending.value = false
  }
  if (imageUrls.value.length === 0 && !itemPending.value) {
    imageFailed.value = reviewItem.value ? '暂无答卷图' : '答卷记录加载失败'
  }
}
// 名次变化大卡的成员可能同学生换题，按「学生|题」触发重载
const memberKey = computed(() => `${shownStudentId.value}|${effectiveQid.value}`)
watch(memberKey, loadCurrent, { immediate: true })

async function probeImage(url: string): Promise<void> {
  const generation = itemGeneration
  try {
    const res = await fetch(url)
    if (generation !== itemGeneration) return
    imageFailed.value = res.status === 410
      ? '原卷已清理，分数和作答记录仍保留'
      : '暂无答卷图'
  } catch {
    if (generation !== itemGeneration) return
    imageFailed.value = '暂无答卷图'
  }
}
function onImageError(url: string): void { void probeImage(url) }

// ---- 翻开分数后的面板 ----
const rubric = ref<ReviewRubricSection | null>(null)
watch([() => props.revealed, effectiveQid], async ([on, qid]) => {
  if (!on || !qid) return
  rubric.value = null
  try { rubric.value = await props.loadRubric(qid) } catch { rubric.value = null }
}, { immediate: true })

const STATUS_LABELS: Record<string, string> = {
  ai_ready: 'AI 评分',
  ai_review: '待复核',
  teacher_final: '教师确认',
  ungraded: '未评分',
  failed: '评分失败',
}
const statusLabel = computed(() => (
  STATUS_LABELS[reviewItem.value?.score_status ?? rcItem.value?.score_status ?? ''] ?? ''
))

const finalScore = computed(() => (
  reviewItem.value?.score_awarded ?? resolvedItemScore(rcItem.value)
))
const finalMax = computed(() => (
  reviewItem.value?.max_score ?? rcItem.value?.max_score ?? null
))

const steps = computed(() => {
  const item = reviewItem.value
  const section = rubric.value
  if (!item || !section) return null
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
  const reason = reviewItem.value?.deduction_reason ?? rcItem.value?.review_reason
  return reason ? translateGradingReason(reason) : null
})

const classStats = computed(() => {
  const qid = member.value?.questionId
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

const pct = (v: number) => `${Math.round(v * 100)}%`

function goReview(): void {
  const s = shownStudent.value
  const qid = member.value?.questionId
  const rid = reviewItem.value?.review_item_id ?? rcItem.value?.review_item_id
  if (!s || !qid || !rid) return
  emit('go-review', s.student_id, qid, rid)
}
</script>

<template>
  <div class="wtc">
    <div class="wtc__meta">
      <span class="wtc__cat">{{ WALKTHROUGH_CATEGORY_LABELS[card.category] }}</span>
      <strong>{{ card.title }}</strong>
    </div>

    <div class="wtc__groups">
      <button
        v-for="(g, gi) in card.groups"
        :key="g.label"
        type="button"
        class="wtc__chip"
        :aria-pressed="gi === groupIdx"
        @click="selectGroup(gi)"
      >{{ g.label }} {{ g.members.length }}人</button>
      <span v-if="card.otherLabel" class="wtc__other">{{ card.otherLabel }}</span>
    </div>

    <div class="wtc__columns">
      <div class="wtc__left">
        <div class="wtc__body">
          <button
            v-if="group && group.members.length > 1"
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
            v-if="group && group.members.length > 1"
            type="button"
            class="wtc__nav"
            aria-label="下一名同学"
            :disabled="memberIdx >= group.members.length - 1"
            @click="shiftMember(1)"
          >›</button>
        </div>
        <p v-if="group && shownStudent" class="wtc__pos">
          第 {{ memberIdx + 1 }} / {{ group.members.length }} 人 ·
          {{ shownStudent.student_name }}
          {{ shownStudent.class_name ? classDisplayLabel(shownStudent.class_name) : '' }}
          <template v-if="card.questionId === null && member"> · 第 {{ member.questionId }} 题</template>
        </p>
      </div>

      <aside v-if="member" class="wtc__aside">
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
          <p v-if="classStats && classStats.rate !== null" class="wtc__note">
            本题本班得分率 {{ pct(classStats.rate) }} · 满分 {{ pct(classStats.fullFrac) }}
          </p>
          <p v-if="reasonText" class="wtc__note">{{ reasonText }}</p>
          <p class="wtc__why">入选理由：{{ member.reason }}</p>
          <button
            type="button"
            class="wtc__review"
            @click="goReview"
          >去复核 / 改分</button>
        </section>
        <p v-else class="wtc__hint">先看作答，想一想这题能得几分 · 空格 翻开分数</p>
        <ReviewAnswerPanel
          :session-id="sessionId"
          :question-id="member.questionId"
          embedded
        />
      </aside>
    </div>
  </div>
</template>

<style scoped>
.wtc { display: flex; flex-direction: column; gap: 8px; flex: 1; min-height: 0; }
.wtc__meta { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.wtc__cat {
  background: var(--color-accent-subtle); color: var(--color-accent-hover);
  border-radius: 999px; padding: 2px 10px; font-size: var(--font-size-caption); flex-shrink: 0;
}
.wtc__groups { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.wtc__chip {
  border: 1px solid var(--color-border-default); background: var(--card);
  border-radius: 999px; padding: 3px 10px; font-size: var(--font-size-caption); cursor: pointer;
}
.wtc__chip[aria-pressed="true"] {
  border-color: var(--color-accent); color: var(--color-accent-hover);
  background: var(--color-accent-subtle);
}
.wtc__other { color: var(--color-text-muted); font-size: var(--font-size-caption); }
.wtc__columns { display: flex; gap: 12px; flex: 1; min-height: 0; }
.wtc__left {
  flex: 1; min-width: 0; min-height: 0;
  display: flex; flex-direction: column; gap: 6px;
}
.wtc__aside {
  width: clamp(320px, 30vw, 440px); flex-shrink: 0; min-height: 0;
  display: flex; flex-direction: column; gap: 8px; overflow-y: auto;
}
@media (max-width: 1100px) {
  .wtc__columns { flex-direction: column; overflow-y: auto; }
  .wtc__left { flex: none; min-height: 40vh; }
  .wtc__aside { width: auto; overflow-y: visible; }
}
.wtc__body { display: flex; align-items: stretch; gap: 8px; flex: 1; min-height: 0; }
.wtc__nav {
  flex-shrink: 0; width: 44px; font-size: var(--font-size-display); border: 1px solid var(--color-border-default);
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
  width: 100%; height: 100%; object-fit: contain; cursor: zoom-in;
  background: #fff; border-radius: 4px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}
/* 右栏内嵌答案面板铺满列宽（覆盖面板自带的 align-self: start） */
.wtc__aside :deep(.review-answer-panel) {
  width: 100%;
  align-self: stretch;
}
.wtc__loading { margin: auto; color: var(--color-text-muted); font-size: var(--font-size-body); }
.wtc__pos { margin: 0; text-align: center; font-size: var(--font-size-dense); color: var(--color-text-secondary); }
.wtc__reveal {
  border: 1px solid var(--color-border-subtle); border-radius: 10px;
  background: var(--card); padding: 10px 14px; font-size: var(--font-size-dense); flex-shrink: 0;
}
.wtc__score { margin: 0 0 4px; display: flex; align-items: baseline; gap: 8px; }
.wtc__score strong { font-size: var(--font-size-h2); color: var(--color-accent-hover); }
.wtc__score em { font-style: normal; font-size: var(--font-size-caption); color: var(--color-text-muted); }
.wtc__steps { margin: 4px 0; padding: 0; list-style: none; font-size: var(--font-size-dense); }
.wtc__steps li[data-ok="true"] { color: var(--color-accent-hover); }
.wtc__steps li[data-ok="false"] { color: var(--color-danger); }
.wtc__note { margin: 4px 0; color: var(--color-text-secondary); }
.wtc__why { margin: 4px 0; color: var(--color-text-muted); font-size: var(--font-size-caption); }
.wtc__review {
  margin-top: 6px; border: 1px solid var(--color-accent); color: var(--color-accent-hover);
  background: transparent; border-radius: 8px; padding: 4px 14px; cursor: pointer;
}
.wtc__hint { margin: 0; text-align: center; color: var(--color-text-muted); font-size: var(--font-size-dense); }
</style>
