<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { ApiError } from '../../api/errors'
import {
  questionBankCriteriaApi,
  type TrainingCriterionPoint,
  type TrainingCriterionVersion,
  type TrainingCriterionWorkspace,
} from '../../api/question-bank-criteria'
import AppButton from '../design-system/AppButton.vue'
import SolutionEvidenceReview from './SolutionEvidenceReview.vue'

interface EditorPoint extends TrainingCriterionPoint {
  equivalent_text: string
  counterexample_text: string
}

const props = defineProps<{ questionId: number }>()

const workspace = ref<TrainingCriterionWorkspace | null>(null)
const loadState = ref<'loading' | 'ready' | 'error'>('loading')
const writeState = ref<'idle' | 'saving' | 'reviewing' | 'generating'>('idle')
const message = ref('')
const reviewNote = ref('')
const rationale = ref('')
const auxiliaryText = ref('')
const editorPoints = ref<EditorPoint[]>([])
let loadController: AbortController | null = null

const current = computed(() => workspace.value?.current_version ?? null)
const currentSourceLabel = computed(() => ({
  combined_model: '联合分析',
  confirmed_rubric_adapter: '评分依据转换',
  teacher_manual: '教师修改',
  backfill: 'AI 回填',
}[current.value?.source_kind ?? 'combined_model']))
const canReview = computed(() => current.value?.status === 'proposed')
const blockingCodes = computed(() => (
  (current.value?.quality_codes ?? []).filter((code) => code !== 'missing_actual_image')
))
const advisoryCodes = computed(() => (
  (current.value?.quality_codes ?? []).filter((code) => code === 'missing_actual_image')
))
const canApprove = computed(() => canReview.value && blockingCodes.value.length === 0)
const statusCopy = computed(() => {
  if (workspace.value?.available) {
    return {
      tone: 'available',
      label: '以后生成的训练卷可用',
      detail: current.value?.status === 'proposed'
        ? '已批准版本继续有效；当前新版仍需核对。'
        : '当前批准版已冻结，历史训练卷仍保留各自使用的旧版本。',
    }
  }
  const state = workspace.value?.state
  if (state === 'proposed') {
    const blocked = blockingCodes.value.length > 0
    return {
      tone: blocked ? 'blocked' : 'review',
      label: blocked ? '需要先修正' : '等待教师批准',
      detail: blocked
        ? '质量检查未通过，请先按下方列出的问题修正。'
        : advisoryCodes.value.length > 0
          ? '判定点内容可用，可以批准。题目配图当前不可用，只作提醒，不拦截批准。'
          : '批准后才会进入以后生成的训练卷。',
    }
  }
  if (state === 'stale') {
    return {
      tone: 'blocked',
      label: '题目已变化，旧版停用',
      detail: '请重新生成或手动修订；旧版仍保留在历史记录中。',
    }
  }
  if (state === 'rejected') {
    return {
      tone: 'blocked',
      label: '当前版本已退回',
      detail: '可在下方修改后保存为新版本，或重新生成。',
    }
  }
  return {
    tone: 'missing',
    label: '尚无判定点',
    detail: '可手动录入，或仅为这道题重新生成候选版本。',
  }
})

const qualityMessages: Record<string, string> = {
  calculation_process_missing: '计算题不能只保留最终答案，需要可观察的过程。',
  proof_obligations_incomplete: '证明题需要条件、依据、推理和结论。',
  construction_evidence_missing: '作图题需要写清可从答卷上观察到的作图结果。',
  objective_answer_missing: '客观题需要明确答案。',
  multiple_blanks_collapsed: '多空填空题需要逐空设置判定点。',
  duplicate_point_id: '判定点编号不能重复。',
  duplicate_obligation: '不同判定点重复要求了同一件事。',
  missing_actual_image: '题目引用了图片，但当前图片内容不可用。',
  unknown_dependency: '判定点之间的依赖关系不完整，需要核对。',
  source_stale: '判定点对应的题目内容已经变化。',
  question_mismatch: '判定点不属于当前题目。',
  no_points: '至少需要一个判定点。',
  unobservable_point: '每个判定点都需要明确的目标和可观察依据。',
  objective_point_count: '客观题的判定点数量需要与答案结构一致。',
}

function qualityNote(code: string): string {
  return qualityMessages[code] ?? `需要修正：${code}`
}

const blockingQualityNotes = computed(() => blockingCodes.value.map(qualityNote))
const advisoryQualityNotes = computed(() => advisoryCodes.value.map(qualityNote))

function token(): string {
  return globalThis.crypto.randomUUID().replace(/-/g, '').toLowerCase()
}

function editorPoint(point?: TrainingCriterionPoint): EditorPoint {
  return {
    point_id: point?.point_id ?? `p-${editorPoints.value.length + 1}`,
    target: point?.target ?? '',
    observable_evidence: point?.observable_evidence ?? '',
    equivalent_rules: [...(point?.equivalent_rules ?? [])],
    counterexamples: [...(point?.counterexamples ?? [])],
    depends_on: [...(point?.depends_on ?? [])],
    equivalent_text: (point?.equivalent_rules ?? []).join('\n'),
    counterexample_text: (point?.counterexamples ?? []).join('\n'),
  }
}

function syncEditor(version: TrainingCriterionVersion | null): void {
  const criteria = version?.criteria
  editorPoints.value = criteria?.points.length
    ? criteria.points.map((point) => editorPoint(point))
    : [editorPoint()]
  auxiliaryText.value = (criteria?.auxiliary_rules ?? []).join('\n')
  rationale.value = criteria?.rationale ?? ''
}

function cleanLines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function userMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '操作没有完成，请刷新后重试。'
  if (error.code === 'criterion_revision_conflict') {
    return '这道题的判定点刚被更新，请刷新后再操作。'
  }
  if (error.code === 'criterion_quality_failed') {
    return '当前版本没有通过质量检查，修正并保存新版本后才能批准。'
  }
  if (error.code === 'criterion_backfill_unavailable') {
    return '重新生成功能暂时不可用，手动编辑仍可继续。'
  }
  return `${error.message}（请求编号 ${error.requestId}）`
}

async function load(): Promise<void> {
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  loadState.value = 'loading'
  message.value = ''
  try {
    const result = await questionBankCriteriaApi.getWorkspace(
      props.questionId,
      controller.signal,
    )
    if (controller.signal.aborted) return
    workspace.value = result
    syncEditor(result.current_version)
    loadState.value = 'ready'
  } catch (error) {
    if (controller.signal.aborted) return
    loadState.value = 'error'
    message.value = userMessage(error)
  }
}

function addPoint(): void {
  editorPoints.value.push(editorPoint())
}

function removePoint(index: number): void {
  if (editorPoints.value.length <= 1) return
  editorPoints.value.splice(index, 1)
}

function validDraft(): boolean {
  return editorPoints.value.length > 0 && editorPoints.value.every((point) => (
    point.point_id.trim().length >= 2
    && point.target.trim().length > 0
    && point.observable_evidence.trim().length > 0
  ))
}

async function saveDraft(): Promise<void> {
  if (!workspace.value || !validDraft()) {
    message.value = '每个判定点都需要编号、目标和可观察依据。'
    return
  }
  writeState.value = 'saving'
  message.value = ''
  try {
    const result = await questionBankCriteriaApi.saveDraft(props.questionId, {
      expected_revision: workspace.value.revision,
      parent_version_id: current.value?.version_id ?? null,
      request_token: token(),
      reason: '教师核对与调整',
      points: editorPoints.value.map((point) => ({
        point_id: point.point_id.trim(),
        target: point.target.trim(),
        observable_evidence: point.observable_evidence.trim(),
        equivalent_rules: cleanLines(point.equivalent_text),
        counterexamples: cleanLines(point.counterexample_text),
        depends_on: [...(point.depends_on ?? [])],
      })),
      auxiliary_rules: cleanLines(auxiliaryText.value),
      rationale: rationale.value.trim(),
      confidence: 1,
    })
    workspace.value = result
    syncEditor(result.current_version)
    message.value = '已保存为新版本，旧版本没有被覆盖。'
  } catch (error) {
    message.value = userMessage(error)
  } finally {
    writeState.value = 'idle'
  }
}

async function review(action: 'approve' | 'reject'): Promise<void> {
  if (!workspace.value || !current.value) return
  writeState.value = 'reviewing'
  message.value = ''
  try {
    const result = await questionBankCriteriaApi.review(props.questionId, {
      version_id: current.value.version_id,
      expected_revision: workspace.value.revision,
      action,
      reason: reviewNote.value.trim() || (
        action === 'approve' ? '教师核对通过' : '教师退回修改'
      ),
    })
    workspace.value = result
    syncEditor(result.current_version)
    message.value = action === 'approve'
      ? '已批准。该版本可用于以后生成的训练卷。'
      : '已退回。可继续修改并保存为新版本。'
  } catch (error) {
    message.value = userMessage(error)
  } finally {
    writeState.value = 'idle'
  }
}

async function regenerate(): Promise<void> {
  const confirmed = window.confirm(
    '确认只为这道题重新生成判定点吗？这可能调用已配置的 AI 服务并产生费用；当前已批准版本会继续有效，直到新版再次获批。',
  )
  if (!confirmed) return
  writeState.value = 'generating'
  message.value = ''
  try {
    await questionBankCriteriaApi.startBackfill(
      [props.questionId],
      token(),
      'regenerate',
    )
    message.value = '已开始重新生成。完成后刷新这里即可核对新版本。'
  } catch (error) {
    message.value = userMessage(error)
  } finally {
    writeState.value = 'idle'
  }
}

watch(() => props.questionId, load, { immediate: true })
onBeforeUnmount(() => loadController?.abort())
</script>

<template>
  <section class="criterion-review" aria-labelledby="criterion-review-title">
    <header class="criterion-review__heading">
      <div>
        <h3 id="criterion-review-title">判定点</h3>
      </div>
      <button
        type="button"
        class="qb-link"
        :disabled="loadState === 'loading' || writeState !== 'idle'"
        @click="load"
      >
        刷新
      </button>
    </header>

    <p v-if="loadState === 'loading'" class="criterion-review__loading" role="status">
      正在读取判定点…
    </p>
    <div v-else-if="loadState === 'error'" class="qb-feedback is-error" role="alert">
      {{ message }}
    </div>
    <template v-else-if="workspace">
      <div class="criterion-status" :data-tone="statusCopy.tone">
        <span class="criterion-status__mark" aria-hidden="true" />
        <div>
          <strong>{{ statusCopy.label }}</strong>
          <p>{{ statusCopy.detail }}</p>
        </div>
        <small v-if="current">
          版本 {{ current.version_number }} · {{ currentSourceLabel }}
        </small>
      </div>

      <ul v-if="blockingQualityNotes.length" class="criterion-quality is-blocking" aria-label="需要修正的质量问题">
        <li v-for="note in blockingQualityNotes" :key="note">{{ note }}</li>
      </ul>
      <ul v-if="advisoryQualityNotes.length" class="criterion-quality" aria-label="提醒">
        <li v-for="note in advisoryQualityNotes" :key="note">{{ note }}</li>
      </ul>

      <SolutionEvidenceReview
        :question-id="questionId"
        embedded
      />

      <div class="criterion-points">
        <article
          v-for="(point, index) in editorPoints"
          :key="`${point.point_id}-${index}`"
          class="criterion-point"
        >
          <div class="criterion-point__rail">
            <span>{{ index + 1 }}</span>
          </div>
          <div class="criterion-point__body">
            <div class="criterion-point__topline">
              <label>
                <span>判定点编号</span>
                <input v-model="point.point_id" maxlength="64" :aria-label="`第 ${index + 1} 个判定点编号`">
              </label>
              <button
                type="button"
                class="qb-link is-danger"
                :disabled="editorPoints.length <= 1 || writeState !== 'idle'"
                @click="removePoint(index)"
              >
                移除
              </button>
            </div>
            <label>
              <span>要达成什么</span>
              <input
                v-model="point.target"
                maxlength="500"
                :aria-label="`第 ${index + 1} 个判定点目标`"
                placeholder="例如：建立正确方程"
              >
            </label>
            <label>
              <span>从答卷上看到什么才算达成</span>
              <textarea
                v-model="point.observable_evidence"
                rows="2"
                maxlength="1000"
                :aria-label="`第 ${index + 1} 个可观察依据`"
                placeholder="例如：列出与题意一致的等量关系"
              />
            </label>
            <details class="criterion-point__rules">
              <summary>等价写法与反例</summary>
              <div>
                <label>
                  <span>可接受的等价写法（每行一条）</span>
                  <textarea v-model="point.equivalent_text" rows="2" />
                </label>
                <label>
                  <span>不能算达成的情况（每行一条）</span>
                  <textarea v-model="point.counterexample_text" rows="2" />
                </label>
              </div>
            </details>
          </div>
        </article>
      </div>

      <button
        type="button"
        class="criterion-add"
        :disabled="writeState !== 'idle' || editorPoints.length >= 50"
        @click="addPoint"
      >
        <span aria-hidden="true">＋</span>
        添加一个判定点
      </button>

      <div class="criterion-meta">
        <label>
          <span>辅助规则（每行一条，不计入达成点数）</span>
          <textarea v-model="auxiliaryText" rows="2" />
        </label>
        <label>
          <span>制定说明</span>
          <textarea v-model="rationale" rows="2" maxlength="2000" />
        </label>
      </div>

      <div class="criterion-actions">
        <AppButton
          variant="secondary"
          :disabled="writeState !== 'idle' || !validDraft()"
          @click="saveDraft"
        >
          {{ writeState === 'saving' ? '正在保存…' : '保存为新版本' }}
        </AppButton>
        <button
          type="button"
          class="qb-button is-ai"
          :disabled="writeState !== 'idle'"
          @click="regenerate"
        >
          {{ writeState === 'generating' ? '正在启动…' : '重新生成' }}
        </button>
      </div>

      <div v-if="canReview" class="criterion-decision">
        <label>
          <span>审核说明（可选）</span>
          <input v-model="reviewNote" maxlength="500" placeholder="例如：已与标准答案逐项核对">
        </label>
        <div>
          <AppButton
            variant="danger"
            :disabled="writeState !== 'idle'"
            @click="review('reject')"
          >
            退回修改
          </AppButton>
          <AppButton
            variant="primary"
            :disabled="writeState !== 'idle' || !canApprove"
            @click="review('approve')"
          >
            批准用于以后训练
          </AppButton>
        </div>
      </div>

      <p
        v-if="message"
        class="qb-feedback"
        :class="{ 'is-error': message.includes('没有') || message.includes('不能') || message.includes('失败') }"
        role="status"
      >
        {{ message }}
      </p>

      <details v-if="workspace.versions.length" class="criterion-history">
        <summary>查看版本记录（{{ workspace.versions.length }}）</summary>
        <ol>
          <li v-for="version in workspace.versions" :key="version.version_id">
            <span>v{{ version.version_number }}</span>
            <strong>{{ {
              proposed: '待审核',
              approved: '已批准',
              rejected: '已退回',
              superseded: '已被新版替代',
              stale: '题目变化后停用',
            }[version.status] }}</strong>
            <small>{{ version.source_kind === 'teacher_manual' ? '教师修改' : '系统生成' }}</small>
          </li>
        </ol>
      </details>
    </template>
  </section>
</template>
