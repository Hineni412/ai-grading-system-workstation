<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { fetchReviewRubric, type ReviewRubricSection } from '../../api/review'
import { scoreIssue, useReviewDraftStore, type ReviewDraft } from '../../stores/review-drafts'
import { useReviewQueueStore } from '../../stores/review-queue'
import StatePanel from '../design-system/StatePanel.vue'

defineEmits<{ confirmRequest: [] }>()

const reviewStore = useReviewQueueStore()
const draftStore = useReviewDraftStore()
const rubric = ref<ReviewRubricSection | null>(null)
const rubricState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const currentDraft = ref<ReviewDraft | null>(null)
let rubricController: AbortController | null = null
let rubricGeneration = 0

const item = computed(() => reviewStore.currentItem)
const issue = computed(() => {
  if (!item.value || !currentDraft.value) return null
  return scoreIssue(currentDraft.value.scoreText, item.value.max_score)
})
const submitDisabled = computed(() =>
  !item.value ||
  !currentDraft.value ||
  !currentDraft.value.dirty ||
  issue.value !== null,
)
const disabledReason = computed(() => {
  if (!item.value) return '请先选择一条复核记录'
  if (issue.value) return issue.value
  if (!currentDraft.value?.dirty) return '请先修改教师最终分或备注'
  return ''
})
const evidenceSteps = computed(() => stringList(item.value?.metadata.evidence_steps))
const missingSteps = computed(() => stringList(item.value?.metadata.missing_steps))
const candidates = computed(() => (item.value?.candidate_scores ?? []).flatMap((candidate) => {
  const score = typeof candidate.score === 'number' && Number.isFinite(candidate.score)
    ? candidate.score
    : null
  const confidence = typeof candidate.confidence === 'number' && Number.isFinite(candidate.confidence)
    ? candidate.confidence
    : null
  const reason = typeof candidate.reason === 'string' ? candidate.reason.trim() : ''
  if (score === null && !reason) return []
  return [{ score, confidence, reason }]
}))

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((entry) => {
    if (typeof entry !== 'string') return []
    const text = entry.trim()
    return text ? [text] : []
  })
}

function formatScore(value: number): string {
  return Number.isInteger(value) ? String(value) : String(value)
}

function formatConfidence(value: number | null): string {
  if (value === null) return '未提供'
  return value <= 1 ? `${Math.round(value * 100)}%` : `${Math.round(value)}%`
}

function updateScore(event: Event): void {
  if (!currentDraft.value) return
  draftStore.updateScore(currentDraft.value.key, (event.target as HTMLInputElement).value)
}

function updateNote(event: Event): void {
  if (!currentDraft.value) return
  draftStore.updateNote(currentDraft.value.key, (event.target as HTMLTextAreaElement).value)
}

async function loadRubric(sessionId: number, questionId: string): Promise<void> {
  rubricController?.abort()
  const controller = new AbortController()
  rubricController = controller
  const generation = ++rubricGeneration
  rubric.value = null
  rubricState.value = 'loading'
  try {
    const loaded = await fetchReviewRubric(sessionId, questionId, controller.signal)
    if (generation !== rubricGeneration) return
    rubric.value = loaded
    rubricState.value = 'ready'
  } catch {
    if (generation !== rubricGeneration || controller.signal.aborted) return
    rubricState.value = 'error'
  } finally {
    if (rubricController === controller) rubricController = null
  }
}

watch(
  item,
  (next) => {
    if (!next) {
      currentDraft.value = null
      rubric.value = null
      rubricState.value = 'idle'
      rubricController?.abort()
      return
    }
    currentDraft.value = draftStore.ensureDraft(next)
    void loadRubric(next.session_id, next.question_id)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  rubricGeneration += 1
  rubricController?.abort()
})
</script>

<template>
  <aside
    id="session-inspector"
    class="session-inspector review-scoring-inspector"
    data-testid="review-scoring-inspector"
    aria-label="评分与复核检查器"
  >
    <StatePanel
      v-if="!item || !currentDraft"
      kind="empty"
      title="请选择复核记录"
      description="从左侧队列选择一名学生后，可在这里核对并确认教师最终分。"
    />

    <template v-else>
      <div class="review-scoring-inspector__scroll" data-testid="scoring-scroll-region">
        <header class="review-scoring-inspector__header">
          <p>{{ item.question_id }} · 满分 {{ formatScore(item.max_score) }}</p>
          <h2>评分与复核</h2>
          <span v-if="currentDraft.dirty" class="review-scoring-inspector__draft-state">
            教师草稿未确认
          </span>
          <span v-else-if="!item.needs_review" class="review-scoring-inspector__confirmed-state">
            教师已确认
          </span>
        </header>

        <section class="review-scoring-section" aria-labelledby="review-rubric-title">
          <h3 id="review-rubric-title">评分标准</h3>
          <p v-if="rubricState === 'loading'" class="review-scoring-section__muted">正在读取当前题评分标准…</p>
          <p v-else-if="rubricState === 'error'" class="review-scoring-section__warning">
            评分标准暂时无法读取，不影响查看和编辑当前分数。
          </p>
          <p v-else-if="rubricState === 'ready' && !rubric" class="review-scoring-section__warning">
            当前题没有可展示的评分标准，请以原始证据和既有满分为准。
          </p>
          <template v-else-if="rubric">
            <p class="review-scoring-section__meta">
              {{ rubric.title }} · {{ formatScore(rubric.maxScore) }} 分
              <span v-if="rubric.questionType"> · {{ rubric.questionType }}</span>
            </p>
            <ul v-if="rubric.knowledgeLabels.length" class="review-scoring-tags" aria-label="知识点">
              <li v-for="label in rubric.knowledgeLabels" :key="label">{{ label }}</li>
            </ul>
            <ol v-if="rubric.lines.length" class="review-rubric-lines">
              <li v-for="line in rubric.lines" :key="line.label">
                <span>{{ line.label }}</span>
                <strong v-if="line.score !== null">{{ formatScore(line.score) }} 分</strong>
              </li>
            </ol>
            <p v-else class="review-scoring-section__muted">评分配置只提供了本题满分，暂无更细步骤。</p>
          </template>
        </section>

        <section class="review-scoring-section review-scoring-section--ai" aria-labelledby="review-ai-title">
          <h3 id="review-ai-title">{{ item.needs_review ? 'AI 初评' : '确认前记录' }}</h3>
          <div class="review-ai-score">
            <strong>{{ formatScore(item.score_awarded) }}</strong>
            <span>/ {{ formatScore(item.max_score) }} 分</span>
          </div>
          <p>置信度：{{ formatConfidence(item.confidence_score) }}</p>
          <ul v-if="evidenceSteps.length" class="review-evidence-list">
            <li v-for="entry in evidenceSteps" :key="entry">已有证据：{{ entry }}</li>
          </ul>
          <ul v-if="missingSteps.length" class="review-evidence-list review-evidence-list--warning">
            <li v-for="entry in missingSteps" :key="entry">缺失步骤：{{ entry }}</li>
          </ul>
          <details v-if="candidates.length">
            <summary>查看其他 AI 候选</summary>
            <ul class="review-candidate-list">
              <li v-for="(candidate, index) in candidates" :key="index">
                <strong v-if="candidate.score !== null">{{ formatScore(candidate.score) }} 分</strong>
                <span v-if="candidate.confidence !== null"> · {{ formatConfidence(candidate.confidence) }}</span>
                <p v-if="candidate.reason">{{ candidate.reason }}</p>
              </li>
            </ul>
          </details>
        </section>

        <section class="review-scoring-section" aria-labelledby="review-risk-title">
          <h3 id="review-risk-title">风险与错因</h3>
          <p v-if="item.needs_review" class="review-scoring-section__warning">当前记录需要教师复核。</p>
          <p v-else class="review-scoring-section__muted">当前记录已由教师确认。</p>
          <dl class="review-risk-list">
            <template v-if="item.deduction_reason"><dt>扣分原因</dt><dd>{{ item.deduction_reason }}</dd></template>
            <template v-if="item.error_category"><dt>错误类别</dt><dd>{{ item.error_category }}</dd></template>
            <template v-if="item.error_summary"><dt>错误摘要</dt><dd>{{ item.error_summary }}</dd></template>
          </dl>
        </section>

        <section class="review-scoring-section review-scoring-section--teacher" aria-labelledby="review-teacher-title">
          <h3 id="review-teacher-title">教师最终分</h3>
          <label for="teacher-score">最终得分</label>
          <div class="review-teacher-score-field">
            <input
              id="teacher-score"
              data-testid="teacher-score"
              type="number"
              inputmode="decimal"
              min="0"
              :max="item.max_score"
              step="any"
              :value="currentDraft.scoreText"
              :aria-invalid="issue ? 'true' : 'false'"
              aria-describedby="teacher-score-help teacher-score-error"
              @input="updateScore"
            >
            <span>/ {{ formatScore(item.max_score) }} 分</span>
          </div>
          <p id="teacher-score-help" class="review-scoring-section__muted">教师确认结果将覆盖 AI 初评。</p>
          <p v-if="issue" id="teacher-score-error" class="review-field-error">{{ issue }}</p>
          <label for="teacher-note">教师备注</label>
          <textarea
            id="teacher-note"
            rows="3"
            :value="currentDraft.note"
            placeholder="可填写评分依据或修改说明"
            @input="updateNote"
          />
        </section>

        <section class="review-scoring-section" aria-labelledby="review-activity-title">
          <h3 id="review-activity-title">活动记录</h3>
          <p class="review-scoring-section__muted">当前接口暂未提供历史活动列表。本次确认结果仍以服务器返回为准。</p>
        </section>
      </div>

      <footer class="review-scoring-inspector__footer" data-testid="scoring-footer">
        <p v-if="draftStore.dirtyCount > 0">本机内存中有 {{ draftStore.dirtyCount }} 条未确认草稿。</p>
        <button
          type="button"
          data-testid="confirm-next"
          :disabled="submitDisabled"
          :title="disabledReason"
          @click="$emit('confirmRequest')"
        >
          确认并下一份
        </button>
      </footer>
    </template>
  </aside>
</template>
