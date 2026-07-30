<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  trainingApi,
  type PersonalizedPaperInstance,
  type PersonalizedRecommendationDraft,
  type PersonalizedRecommendationItem,
  type TrainingDiagnosis,
  type TrainingExamScopeRequest,
  type TrainingStage,
  type TrainingStageRatios,
  type TrainingStudentScopeRequest,
} from '../../api/training'
import { ApiError } from '../../api/errors'
import TrainingScanBatchPanel from './TrainingScanBatchPanel.vue'

const props = defineProps<{
  diagnosis: TrainingDiagnosis | null
  scope: TrainingStudentScopeRequest
  examScope: TrainingExamScopeRequest
  questionCount: number
  stageRatios: TrainingStageRatios
  excludeCurrentExamOriginals: boolean
  disabled?: boolean
}>()

type RequestState = 'idle' | 'loading' | 'ready' | 'error' | 'editing'

const expectedMinutes = ref(45)
const difficultyMin = ref(1)
const difficultyMax = ref(10)
const selectedTargets = ref<string[]>([])
const editReason = ref('教师根据课堂安排调整推荐草稿')
const state = ref<RequestState>('idle')
const draft = ref<PersonalizedRecommendationDraft | null>(null)
const errorMessage = ref('')
const actionMessage = ref('')
const paperInstances = ref<PersonalizedPaperInstance[]>([])
const paperFiles = ref<Record<string, File | undefined>>({})
const paperBusy = ref('')
const paperContextWindow = ref<32768 | 65536 | 128000>(32768)

const targetOptions = computed(() => [...new Set(
  (props.diagnosis?.students ?? [])
    .flatMap((student) => student.weak_points)
    .map((weak) => weak.knowledge_point.trim())
    .filter(Boolean),
)].sort((left, right) => left.localeCompare(right, 'zh-CN')))

const canGenerate = computed(() => (
  Boolean(props.diagnosis)
  && !props.disabled
  && state.value !== 'loading'
  && state.value !== 'editing'
  && expectedMinutes.value >= 10
  && expectedMinutes.value <= 180
  && difficultyMin.value >= 1
  && difficultyMax.value <= 10
  && difficultyMin.value <= difficultyMax.value
))

watch(
  () => props.diagnosis,
  () => {
    draft.value = null
    state.value = 'idle'
    errorMessage.value = ''
    actionMessage.value = ''
    paperInstances.value = []
    paperFiles.value = {}
    paperBusy.value = ''
    selectedTargets.value = [...targetOptions.value]
  },
  { immediate: true },
)

function requestToken(): string {
  const bytes = new Uint8Array(16)
  globalThis.crypto.getRandomValues(bytes)
  return [...bytes]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('')
}

function stageLabel(stage: TrainingStage): string {
  return {
    direct: '直接巩固',
    prerequisite: '先修补强',
    transfer: '迁移应用',
  }[stage]
}

function safeError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.code === 'personalized_recommendation_revision_conflict') {
      return '草稿已在另一处发生变化，请重新生成后再调整。'
    }
    if (error.code === 'personalized_recommendation_source_changed') {
      return '题目、关系或判定点已经变化，请重新生成草稿。'
    }
    if (error.code === 'personalized_paper_revision_conflict') {
      return '这份训练卷已在另一处发生变化，请刷新后再继续。'
    }
    if (error.code === 'personalized_paper_source_changed') {
      return '题目、关系或判定点已经变化，请重新生成草稿和训练卷。'
    }
    if (error.code === 'personalized_paper_invalid') {
      return '上传的 WPS 审核稿与本卷题目或顺序不一致，请使用本版本下载的审核稿。'
    }
  }
  return fallback
}

async function generate(): Promise<void> {
  if (!canGenerate.value) return
  state.value = 'loading'
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    draft.value = await trainingApi.createPersonalizedDraft({
      request_token: requestToken(),
      scope: props.scope,
      exam_scope: props.examScope,
      question_count: props.questionCount,
      expected_minutes: expectedMinutes.value,
      difficulty_min: difficultyMin.value,
      difficulty_max: difficultyMax.value,
      stage_ratios: props.stageRatios,
      target_names: selectedTargets.value,
      exclude_current_exam_originals: props.excludeCurrentExamOriginals,
    })
    paperInstances.value = []
    paperFiles.value = {}
    state.value = 'ready'
    actionMessage.value = '已生成可审核草稿；尚未形成正式训练卷。'
  } catch (error) {
    state.value = 'error'
    errorMessage.value = safeError(
      error,
      '个性化草稿暂时无法生成，当前选择已保留，请稍后重试。',
    )
  }
}

function instancesForStudent(studentId: string): PersonalizedPaperInstance[] {
  return paperInstances.value
    .filter((item) => item.student_id === studentId)
    .sort((left, right) => right.series_version - left.series_version)
}

async function createPaper(studentId: string): Promise<void> {
  if (!draft.value || paperBusy.value) return
  paperBusy.value = `create:${studentId}`
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const instance = await trainingApi.createPaperInstance(
      draft.value.draft_id,
      {
        operation_token: requestToken(),
        expected_draft_revision: draft.value.revision,
        student_id: studentId,
        context_window_tokens: paperContextWindow.value,
      },
    )
    paperInstances.value = [
      instance,
      ...paperInstances.value.filter(
        (item) => item.paper_instance_id !== instance.paper_instance_id,
      ),
    ]
    actionMessage.value = 'WPS 审核稿已生成；下载检查后再上传确认。'
  } catch (error) {
    if (
      error instanceof ApiError
      && error.code === 'personalized_paper_budget_exceeded'
    ) {
      errorMessage.value = '整卷题量、判定点、图片或页数超过处理容量，请减少内容或选择更大容量。'
    } else {
      errorMessage.value = safeError(
        error,
        'WPS 审核稿暂时无法生成，推荐草稿和旧版本均未改变。',
      )
    }
  } finally {
    paperBusy.value = ''
  }
}

function setPaperFile(instanceId: string, event: Event): void {
  const input = event.target as HTMLInputElement
  paperFiles.value = {
    ...paperFiles.value,
    [instanceId]: input.files?.[0],
  }
}

async function freezePaper(
  instance: PersonalizedPaperInstance,
): Promise<void> {
  const file = paperFiles.value[instance.paper_instance_id]
  if (!file || paperBusy.value) return
  paperBusy.value = `freeze:${instance.paper_instance_id}`
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const frozen = await trainingApi.freezePaperInstance(
      instance,
      file,
      requestToken(),
    )
    paperInstances.value = paperInstances.value.map((item) => (
      item.paper_instance_id === frozen.paper_instance_id ? frozen : item
    ))
    actionMessage.value = '该版本已冻结为 PDF；不会自动打印，也不会覆盖旧版本。'
  } catch (error) {
    errorMessage.value = safeError(
      error,
      '冻结失败，当前审核稿仍可继续使用，未留下半成品 PDF。',
    )
  } finally {
    paperBusy.value = ''
  }
}

async function downloadPaper(
  instance: PersonalizedPaperInstance,
  kind: 'review_docx' | 'reviewed_docx' | 'frozen_pdf',
): Promise<void> {
  const path = instance.downloads[kind]
  if (!path) return
  paperBusy.value = `download:${instance.paper_instance_id}:${kind}`
  errorMessage.value = ''
  try {
    const response = await trainingApi.downloadPaperArtifact(path)
    const extension = kind === 'frozen_pdf' ? 'pdf' : 'docx'
    const url = URL.createObjectURL(response.blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `个性化训练卷-${instance.student_name || instance.student_code || instance.student_id}-V${instance.series_version}.${extension}`
    anchor.click()
    URL.revokeObjectURL(url)
  } catch {
    errorMessage.value = '文件暂时无法下载，请稍后重试；已保存的版本不会受影响。'
  } finally {
    paperBusy.value = ''
  }
}

async function editItem(
  studentId: string,
  item: PersonalizedRecommendationItem,
  action: 'lock' | 'unlock' | 'exclude' | 'replace',
): Promise<void> {
  if (!draft.value || state.value === 'editing') return
  state.value = 'editing'
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    draft.value = await trainingApi.editPersonalizedDraft(
      draft.value.draft_id,
      {
        request_token: requestToken(),
        expected_revision: draft.value.revision,
        action,
        student_id: studentId,
        item_id: item.item_id,
        reason: editReason.value.trim() || '教师调整推荐草稿',
      },
    )
    state.value = 'ready'
    actionMessage.value = {
      lock: '已锁定该题，替换或排除前需先解锁。',
      unlock: '已解锁该题。',
      exclude: '已排除该题；空缺会保留，不会模糊补题。',
      replace: '已替换为下一道满足同一规则的题目，并保留原题历史。',
    }[action]
  } catch (error) {
    state.value = 'error'
    errorMessage.value = safeError(
      error,
      '草稿调整未保存，原草稿保持不变。',
    )
  }
}
</script>

<template>
  <section class="personalized-draft" aria-labelledby="personalized-draft-title">
    <header>
      <div>
        <p class="training-eyebrow">P4 · 一人一卷草稿</p>
        <h3 id="personalized-draft-title">个性化推荐草稿</h3>
        <p>只使用已确认关系和已批准判定点；题目不足时会保留空缺。</p>
      </div>
      <span v-if="draft">版本 {{ draft.revision }}</span>
    </header>

    <div class="personalized-controls">
      <label>
        预计时长
        <input v-model.number="expectedMinutes" type="number" min="10" max="180">
        <span>分钟</span>
      </label>
      <label>
        最低难度
        <input v-model.number="difficultyMin" type="number" min="1" max="10">
      </label>
      <label>
        最高难度
        <input v-model.number="difficultyMax" type="number" min="1" max="10">
      </label>
    </div>

    <fieldset v-if="targetOptions.length" class="personalized-targets">
      <legend>本次训练目标</legend>
      <label v-for="target in targetOptions" :key="target">
        <input v-model="selectedTargets" type="checkbox" :value="target">
        {{ target }}
      </label>
      <p v-if="!selectedTargets.length">未勾选时，将按每名学生当前薄弱证据自动选择。</p>
    </fieldset>
    <p v-else class="training-empty is-compact">
      当前没有可确认的薄弱目标；生成后会明确标注为保守复习，不会补造薄弱点。
    </p>

    <button
      type="button"
      class="training-button is-secondary"
      data-testid="generate-personalized-draft"
      :disabled="!canGenerate"
      @click="generate"
    >
      {{ state === 'loading' ? '正在生成…' : '生成个性化草稿' }}
    </button>

    <p v-if="actionMessage" class="training-feedback" role="status">
      {{ actionMessage }}
    </p>
    <p v-if="errorMessage" class="training-feedback is-warning" role="alert">
      {{ errorMessage }}
    </p>

    <template v-if="draft">
      <label class="personalized-edit-reason">
        调整原因
        <input v-model="editReason" maxlength="500">
      </label>

      <ul v-if="draft.warnings.length" class="training-warning-list">
        <li v-for="warning in draft.warnings" :key="warning">{{ warning }}</li>
      </ul>

      <article
        v-for="student in draft.students"
        :key="student.student_id"
        class="personalized-student"
      >
        <header>
          <div>
            <strong>{{ student.student_name || student.student_code || student.student_id }}</strong>
            <span>
              {{ student.items.length }} 题 · 约 {{ student.estimated_minutes }} 分钟
            </span>
          </div>
          <small>
            {{ student.selection_mode === 'maintenance_fallback' ? '保守复习' : '按掌握证据推荐' }}
          </small>
        </header>

        <ol>
          <li v-for="item in student.items" :key="item.item_id">
            <div class="personalized-item-main">
              <span>{{ stageLabel(item.stage) }} · 题 {{ item.question_number }}</span>
              <strong>{{ item.matched_name }}</strong>
              <p>{{ item.reason }}</p>
              <small>
                难度 {{ item.difficulty }} · 约 {{ item.estimated_minutes }} 分钟 ·
                {{ item.criterion_point_count }} 个已批准判定点
              </small>
              <small v-if="item.relation">
                已确认{{ item.relation.relation_type === 'prerequisite' ? '先修' : '相关' }}关系：
                {{ item.relation.rationale }}
              </small>
            </div>
            <div class="personalized-item-actions">
              <button
                type="button"
                class="training-link"
                :disabled="state === 'editing'"
                @click="editItem(student.student_id, item, item.locked ? 'unlock' : 'lock')"
              >
                {{ item.locked ? '解锁' : '锁定' }}
              </button>
              <button
                type="button"
                class="training-link"
                :disabled="item.locked || state === 'editing'"
                @click="editItem(student.student_id, item, 'replace')"
              >
                替换
              </button>
              <button
                type="button"
                class="training-link"
                :disabled="item.locked || state === 'editing'"
                @click="editItem(student.student_id, item, 'exclude')"
              >
                排除
              </button>
            </div>
          </li>
        </ol>

        <ul v-if="student.warnings.length" class="training-warning-list">
          <li v-for="warning in student.warnings" :key="warning">{{ warning }}</li>
        </ul>

        <section class="personalized-paper-panel">
          <header>
            <div>
              <strong>生成该生训练卷</strong>
              <small>每次生成都是独立新版本，不会覆盖旧卷。</small>
            </div>
            <label>
              处理容量
              <select v-model.number="paperContextWindow">
                <option :value="32768">标准</option>
                <option :value="65536">较大</option>
                <option :value="128000">最大</option>
              </select>
            </label>
            <button
              type="button"
              class="training-button is-secondary"
              :disabled="Boolean(paperBusy) || !student.items.length"
              @click="createPaper(student.student_id)"
            >
              {{ paperBusy === `create:${student.student_id}` ? '正在生成…' : '生成 WPS 审核稿' }}
            </button>
          </header>

          <p v-if="!instancesForStudent(student.student_id).length" class="training-empty is-compact">
            尚未生成训练卷。先完成草稿调整，再生成 WPS 审核稿。
          </p>

          <article
            v-for="instance in instancesForStudent(student.student_id)"
            :key="instance.paper_instance_id"
            class="personalized-paper-version"
          >
            <div>
              <strong>V{{ instance.series_version }}</strong>
              <span>
                {{ instance.question_count }} 题 ·
                {{ instance.criterion_point_count }} 个判定点 ·
                {{ instance.status === 'frozen' ? `${instance.pages.length} 页冻结 PDF` : '等待 WPS 审核' }}
              </span>
            </div>
            <small>
              整卷预计占用 {{ instance.budget.estimated_total_tokens.toLocaleString() }} /
              {{ instance.budget.context_window_tokens.toLocaleString() }}
            </small>
            <div class="personalized-paper-actions">
              <button
                v-if="instance.downloads.review_docx"
                type="button"
                class="training-link"
                :disabled="Boolean(paperBusy)"
                @click="downloadPaper(instance, 'review_docx')"
              >
                下载 WPS 审核稿
              </button>
              <button
                v-if="instance.downloads.reviewed_docx"
                type="button"
                class="training-link"
                :disabled="Boolean(paperBusy)"
                @click="downloadPaper(instance, 'reviewed_docx')"
              >
                下载确认稿
              </button>
              <button
                v-if="instance.downloads.frozen_pdf"
                type="button"
                class="training-link"
                :disabled="Boolean(paperBusy)"
                @click="downloadPaper(instance, 'frozen_pdf')"
              >
                下载冻结 PDF
              </button>
            </div>
            <div v-if="instance.status === 'review_pending'" class="personalized-paper-freeze">
              <label>
                上传在 WPS 中检查后的同版本 DOCX
                <input
                  type="file"
                  accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  @change="setPaperFile(instance.paper_instance_id, $event)"
                >
              </label>
              <button
                type="button"
                class="training-button"
                :disabled="Boolean(paperBusy) || !paperFiles[instance.paper_instance_id]"
                @click="freezePaper(instance)"
              >
                {{ paperBusy === `freeze:${instance.paper_instance_id}` ? '正在冻结…' : '确认并冻结 PDF' }}
              </button>
            </div>
          </article>
          <p class="personalized-paper-note">
            冻结只生成带页面身份的 PDF，不会自动打印；要改内容请生成新版本。
          </p>
        </section>
      </article>

      <TrainingScanBatchPanel :instances="paperInstances" />

      <p class="personalized-footnote">
        训练卷使用生成时的题目、推荐理由和已批准判定点快照；以后来源变化不会改写旧卷。
      </p>
    </template>
  </section>
</template>

<style scoped>
.personalized-draft {
  margin-top: 1.25rem;
  padding: 1rem;
  border: 1px solid var(--line, #d6d3cd);
  border-radius: 14px;
  background: #fbfaf7;
}

.personalized-draft > header,
.personalized-student > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.personalized-draft h3,
.personalized-draft p {
  margin: 0.2rem 0;
}

.personalized-draft > header > span {
  flex: 0 0 auto;
  white-space: nowrap;
}

.personalized-controls {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.75rem;
  margin: 1rem 0;
}

.personalized-controls label,
.personalized-edit-reason {
  display: grid;
  gap: 0.35rem;
  color: #49443d;
  font-size: 0.9rem;
}

.personalized-controls input,
.personalized-edit-reason input {
  min-width: 0;
  padding: 0.55rem 0.65rem;
  border: 1px solid #cbc6bd;
  border-radius: 8px;
  background: white;
}

.personalized-targets {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem 1rem;
  margin: 0 0 1rem;
  padding: 0.75rem;
  border: 1px solid #ddd8cf;
  border-radius: 10px;
}

.personalized-targets legend {
  padding: 0 0.35rem;
  font-weight: 700;
}

.personalized-targets p {
  flex-basis: 100%;
  color: #7a5d22;
}

.personalized-edit-reason {
  margin: 1rem 0;
}

.personalized-student {
  margin-top: 1rem;
  padding: 0.85rem;
  border: 1px solid #e1ddd5;
  border-radius: 10px;
  background: white;
}

.personalized-student header div,
.personalized-item-main {
  display: grid;
  gap: 0.25rem;
}

.personalized-student ol {
  display: grid;
  gap: 0.65rem;
  margin: 0.85rem 0 0;
  padding-left: 1.4rem;
}

.personalized-student li {
  padding: 0.7rem;
  border-radius: 8px;
  background: #f6f4ef;
}

.personalized-item-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-top: 0.55rem;
}

.personalized-paper-panel {
  display: grid;
  gap: 0.75rem;
  margin-top: 1rem;
  padding: 0.85rem;
  border: 1px solid #cfd9df;
  border-radius: 10px;
  background: #f3f7f8;
}

.personalized-paper-panel > header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: end;
  gap: 0.75rem;
}

.personalized-paper-panel > header div,
.personalized-paper-panel > header label,
.personalized-paper-version > div:first-child,
.personalized-paper-freeze label {
  display: grid;
  gap: 0.25rem;
}

.personalized-paper-panel select,
.personalized-paper-freeze input {
  min-width: 0;
  padding: 0.48rem 0.6rem;
  border: 1px solid #b9c8cf;
  border-radius: 8px;
  background: white;
}

.personalized-paper-version {
  display: grid;
  gap: 0.45rem;
  padding: 0.75rem;
  border: 1px solid #d6e0e5;
  border-radius: 9px;
  background: white;
}

.personalized-paper-actions,
.personalized-paper-freeze {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.75rem;
}

.personalized-paper-freeze label {
  flex: 1 1 18rem;
}

.personalized-paper-note {
  color: #55636a;
  font-size: 0.84rem;
}

.personalized-footnote {
  margin-top: 1rem !important;
  color: #675f55;
  font-size: 0.88rem;
}

@media (max-width: 760px) {
  .personalized-controls {
    grid-template-columns: 1fr;
  }

  .personalized-paper-panel > header {
    grid-template-columns: 1fr;
    align-items: stretch;
  }
}
</style>
