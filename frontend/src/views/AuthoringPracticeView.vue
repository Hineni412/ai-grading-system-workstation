<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'

import { ApiError } from '../api/errors'
import {
  AUTHORING_SOLO_LEVELS,
  authoringApi,
  authoringOperationToken,
  type AuthoringContent,
  type AuthoringKind,
  type AuthoringSoloLevel,
  type AuthoringTaskCard,
  type AuthoringWorkDetail,
  type AuthoringWorkSummary,
} from '../api/authoring'
import { questionBankApi, type QuestionBankDetail } from '../api/question-bank'
import AuthoringCreatePanel from '../components/authoring/AuthoringCreatePanel.vue'
import AuthoringWorkEditor from '../components/authoring/AuthoringWorkEditor.vue'
import AuthoringWorkList from '../components/authoring/AuthoringWorkList.vue'
import {
  type DraftState,
  type PartRow,
} from '../components/authoring/authoring-draft'
import { useAuthoringCompare } from '../components/authoring/useAuthoringCompare'
import '../styles/authoring.css'

const route = useRoute() as ReturnType<typeof useRoute> | undefined

// ------------------------------------------------------------ list / storage

const works = ref<AuthoringWorkSummary[]>([])
const listState = ref<'loading' | 'ready' | 'error'>('loading')
const listError = ref('')
const storageUnavailable = ref(false)

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : '操作失败，请重试'
}

function isStorageUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'authoring_storage_unavailable'
}

async function loadWorks(): Promise<void> {
  listState.value = 'loading'
  listError.value = ''
  try {
    const result = await authoringApi.listWorks()
    works.value = result.items
    listState.value = 'ready'
  } catch (error) {
    if (isStorageUnavailable(error)) {
      storageUnavailable.value = true
      listState.value = 'ready'
      return
    }
    listError.value = errorText(error)
    listState.value = 'error'
  }
}

// ------------------------------------------------------------- create panel

const sourceId = computed(() => {
  const raw = route?.query.source
  const value = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isSafeInteger(value) && value > 0 ? value : null
})

const showCreate = ref(false)
const sourcePreview = ref<QuestionBankDetail | null>(null)
const sourceState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const sourceError = ref('')
const newKind = ref<AuthoringKind>('decompose')
const newTitle = ref('')
const taskCards = ref<AuthoringTaskCard[]>([])
const newCardId = ref('')
const cardKeepKnowledge = ref(true)
const cardDifficulty = ref('')
const cardSolo = ref<'' | AuthoringSoloLevel>('')
const cardNote = ref('')
const creating = ref(false)
const createError = ref('')
// 一次创建提交使用同一个幂等令牌，网络失败重试不会产生重复作品。
let createToken = ''

async function openCreate(): Promise<void> {
  if (sourceId.value === null) return
  showCreate.value = true
  detail.value = null
  createError.value = ''
  if (!createToken) createToken = authoringOperationToken()
  if (sourceState.value === 'idle') {
    sourceState.value = 'loading'
    try {
      sourcePreview.value = await questionBankApi.getQuestion(sourceId.value)
      sourceState.value = 'ready'
    } catch (error) {
      sourceError.value = errorText(error)
      sourceState.value = 'error'
    }
  }
  if (!taskCards.value.length) {
    try {
      taskCards.value = (await authoringApi.listTaskCards()).items
      if (!newCardId.value) newCardId.value = taskCards.value[0]?.id ?? ''
    } catch (error) {
      if (isStorageUnavailable(error)) storageUnavailable.value = true
    }
  }
}

async function submitCreate(): Promise<void> {
  if (sourceId.value === null || creating.value) return
  if (newKind.value === 'adapt' && !newCardId.value) {
    createError.value = '改编练习需要先选择一张任务卡'
    return
  }
  creating.value = true
  createError.value = ''
  try {
    const work = await authoringApi.createWork({
      kind: newKind.value,
      source_question_id: sourceId.value,
      title: newTitle.value,
      task_card: newKind.value === 'adapt'
        ? {
            method_id: newCardId.value,
            targets: {
              keep_knowledge: cardKeepKnowledge.value,
              target_difficulty: cardDifficulty.value === '' ? null : Number(cardDifficulty.value),
              target_solo: cardSolo.value || null,
              note: cardNote.value,
            },
          }
        : undefined,
      operation_token: createToken,
    })
    showCreate.value = false
    applyDetail(work)
    void loadWorks()
  } catch (error) {
    if (isStorageUnavailable(error)) {
      storageUnavailable.value = true
      showCreate.value = false
      return
    }
    createError.value = errorText(error)
  } finally {
    creating.value = false
  }
}

// ----------------------------------------------------------------- workspace

const detail = ref<AuthoringWorkDetail | null>(null)
const detailState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const detailError = ref('')
const saving = ref(false)
const saveMessage = ref('')
const saveError = ref('')
const versionConflict = ref(false)
let saveToken = ''
let saveTokenPayload = ''

function emptyDraft(): DraftState {
  return {
    intent: '',
    knowledgeText: '',
    keyStepsText: '',
    expectedErrorsText: '',
    predictedDifficulty: '',
    predictedSolo: '',
    questionText: '',
    answerText: '',
    questionType: '解答题',
    targetKnowledgeText: '',
    caseListText: '',
    notes: '',
    parts: [],
  }
}

const draft = reactive<DraftState>(emptyDraft())
const baseline = ref('')
const compare = useAuthoringCompare(detail)
const { compareA, compareB, compareRows, compareLoading, runCompare } = compare

function lines(text: string): string[] {
  return text.split('\n').map((line) => line.trim()).filter(Boolean)
}

function joinLines(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => String(item)).join('\n') : ''
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function numberText(value: unknown): string {
  return typeof value === 'number' && Number.isInteger(value) ? String(value) : ''
}

function soloValue(value: unknown): '' | AuthoringSoloLevel {
  return typeof value === 'string'
    && (AUTHORING_SOLO_LEVELS as readonly string[]).includes(value)
    ? value as AuthoringSoloLevel
    : ''
}

function partRows(value: unknown): PartRow[] {
  if (!Array.isArray(value)) return []
  return value.filter((item) => typeof item === 'object' && item !== null).map((item) => {
    const row = item as Record<string, unknown>
    return {
      part_label: text(row.part_label),
      predicted_difficulty: numberText(row.predicted_difficulty),
      predicted_solo: soloValue(row.predicted_solo),
    }
  })
}

function fillDraft(content: AuthoringContent | null): void {
  const source = content ?? {}
  Object.assign(draft, emptyDraft())
  draft.intent = text(source.intent)
  draft.predictedDifficulty = numberText(source.predicted_difficulty)
  draft.predictedSolo = soloValue(source.predicted_solo)
  draft.parts = partRows(source.parts)
  if (detail.value?.kind === 'adapt') {
    draft.questionText = text(source.question_text)
    draft.answerText = text(source.answer_text)
    draft.questionType = text(source.question_type) || '解答题'
    draft.targetKnowledgeText = joinLines(source.target_knowledge)
    draft.caseListText = joinLines(source.case_list)
    draft.notes = text(source.notes)
  } else {
    draft.knowledgeText = joinLines(source.knowledge_points)
    draft.keyStepsText = joinLines(source.key_steps)
    draft.expectedErrorsText = joinLines(source.expected_errors)
  }
  baseline.value = JSON.stringify(serializeDraft())
}

function serializeDraft(): AuthoringContent {
  const parts = draft.parts
    .map((row) => ({
      part_label: row.part_label.trim(),
      predicted_difficulty: row.predicted_difficulty === ''
        ? null
        : Number(row.predicted_difficulty),
      predicted_solo: row.predicted_solo || null,
    }))
    .filter((row) => row.part_label)
  const difficulty = draft.predictedDifficulty === ''
    ? null
    : Number(draft.predictedDifficulty)
  if (detail.value?.kind === 'adapt') {
    return {
      question_text: draft.questionText,
      answer_text: draft.answerText,
      question_type: draft.questionType,
      intent: draft.intent,
      target_knowledge: lines(draft.targetKnowledgeText),
      parts,
      predicted_difficulty: difficulty,
      expected_errors: lines(draft.expectedErrorsText),
      case_list: lines(draft.caseListText),
      notes: draft.notes,
      figure_asset_ids: [],
    }
  }
  return {
    intent: draft.intent,
    knowledge_points: lines(draft.knowledgeText),
    key_steps: lines(draft.keyStepsText),
    expected_errors: lines(draft.expectedErrorsText),
    predicted_difficulty: difficulty,
    predicted_solo: draft.predictedSolo || null,
    parts,
  }
}

const dirty = computed(() => (
  detail.value !== null && JSON.stringify(serializeDraft()) !== baseline.value
))

function applyDetail(work: AuthoringWorkDetail): void {
  detail.value = work
  detailState.value = 'ready'
  fillDraft(work.latest_content)
  saveToken = authoringOperationToken()
  saveTokenPayload = ''
  saveMessage.value = ''
  saveError.value = ''
  versionConflict.value = false
  compare.reset()
}

async function openWork(workId: string): Promise<void> {
  if (detail.value?.work_id === workId) return
  if (dirty.value && !window.confirm('当前练习有未保存的修改，切换后将丢失。确定切换吗？')) {
    return
  }
  showCreate.value = false
  detailState.value = 'loading'
  detailError.value = ''
  try {
    applyDetail(await authoringApi.getWork(workId))
  } catch (error) {
    if (isStorageUnavailable(error)) {
      storageUnavailable.value = true
      return
    }
    detail.value = null
    detailError.value = errorText(error)
    detailState.value = 'error'
  }
}

async function saveVersion(): Promise<void> {
  const work = detail.value
  if (!work || saving.value) return
  if (work.kind === 'adapt' && !draft.questionText.trim()) {
    saveError.value = '题干不能为空'
    return
  }
  const content = serializeDraft()
  const payload = JSON.stringify(content)
  // 同一令牌只能对应同一内容：内容没变说明是上一次提交的重试，沿用令牌；
  // 内容变了说明是一次新的保存，换一个新令牌。
  if (!saveToken || saveTokenPayload !== payload) {
    saveToken = authoringOperationToken()
  }
  saveTokenPayload = payload
  saving.value = true
  saveError.value = ''
  saveMessage.value = ''
  versionConflict.value = false
  try {
    const saved = await authoringApi.saveVersion(work.work_id, {
      content,
      base_version: work.current_version,
      operation_token: saveToken,
    })
    saveMessage.value = `已保存为第 ${saved.version_no} 版`
    saveToken = authoringOperationToken()
    saveTokenPayload = ''
    baseline.value = JSON.stringify(serializeDraft())
    await openWorkReload(work.work_id)
    void loadWorks()
  } catch (error) {
    if (error instanceof ApiError && error.code === 'authoring_version_conflict') {
      versionConflict.value = true
      saveError.value = '这份练习在其他地方已经保存了新版本。请重新加载最新内容后再保存。'
    } else if (error instanceof ApiError && error.code === 'authoring_token_conflict') {
      versionConflict.value = true
      saveError.value = '这次保存与上一次未完成的保存冲突，请重新加载后再保存。'
    } else {
      saveError.value = errorText(error)
    }
  } finally {
    saving.value = false
  }
}

async function openWorkReload(workId: string): Promise<void> {
  try {
    applyDetail(await authoringApi.getWork(workId))
  } catch {
    // 保存已成功，详情刷新失败不影响结果展示。
  }
}

async function reloadAfterConflict(): Promise<void> {
  const work = detail.value
  if (!work) return
  if (!window.confirm('重新加载会丢弃当前未保存的修改，确定吗？')) return
  applyDetail(await authoringApi.getWork(work.work_id))
}

async function removeWork(work: AuthoringWorkSummary): Promise<void> {
  const label = work.title || `第 ${work.source_question_number || '?'} 题`
  if (!window.confirm(`确定删除「${label}」吗？已保存的版本会一起隐藏。`)) return
  try {
    await authoringApi.deleteWork(work.work_id)
    if (detail.value?.work_id === work.work_id) {
      detail.value = null
      detailState.value = 'idle'
    }
    await loadWorks()
  } catch (error) {
    listError.value = errorText(error)
    listState.value = 'error'
  }
}

function addPart(): void {
  draft.parts.push({
    part_label: `（${draft.parts.length + 1}）`,
    predicted_difficulty: '',
    predicted_solo: '',
  })
}

function removePart(index: number): void {
  draft.parts.splice(index, 1)
}

// ------------------------------------------------------------------ lifecycle

onMounted(async () => {
  await loadWorks()
  if (sourceId.value !== null) await openCreate()
})

watch(sourceId, (value, previous) => {
  if (value === null || value === previous) return
  createToken = ''
  sourceState.value = 'idle'
  sourcePreview.value = null
  newCardId.value = ''
  void openCreate()
})

function confirmLeave(): boolean {
  return !dirty.value
    || window.confirm('当前练习有未保存的修改，离开后将丢失。确定离开吗？')
}

if (route) {
  onBeforeRouteLeave(() => confirmLeave())
}
</script>

<template>
  <section class="authoring">
    <header class="authoring__hero">
      <div>
        <p class="authoring__kicker">ITEM AUTHORING</p>
        <h1 tabindex="-1">命题练习</h1>
        <p>拆解好题、按任务卡改编，保存每一版命题。</p>
      </div>
    </header>

    <p v-if="storageUnavailable" class="authoring__notice" role="status">
      命题练习的数据表还没有建立，需要先完成数据库升级。
    </p>

    <template v-else>
      <div class="authoring__layout">
        <AuthoringWorkList
          :works="works"
          :list-state="listState"
          :list-error="listError"
          :active-work-id="detail?.work_id ?? null"
          @open="openWork"
          @remove="removeWork"
          @retry="loadWorks"
        />

        <div class="authoring__main">
          <!-- 新建练习 -->
          <AuthoringCreatePanel
            v-if="showCreate"
            v-model:kind="newKind"
            v-model:title="newTitle"
            v-model:card-id="newCardId"
            v-model:keep-knowledge="cardKeepKnowledge"
            v-model:difficulty="cardDifficulty"
            v-model:solo="cardSolo"
            v-model:note="cardNote"
            :source-preview="sourcePreview"
            :source-state="sourceState"
            :source-error="sourceError"
            :task-cards="taskCards"
            :creating="creating"
            :create-error="createError"
            @submit="submitCreate"
            @cancel="showCreate = false"
          />

          <!-- 练习工作区 -->
          <AuthoringWorkEditor
            v-else-if="detail"
            :detail="detail"
            :draft="draft"
            :dirty="dirty"
            :saving="saving"
            :save-message="saveMessage"
            :save-error="saveError"
            :version-conflict="versionConflict"
            v-model:compare-a="compareA"
            v-model:compare-b="compareB"
            :compare-rows="compareRows"
            :compare-loading="compareLoading"
            :task-cards="taskCards"
            @save="saveVersion"
            @reload-after-conflict="reloadAfterConflict"
            @compare="runCompare"
            @add-part="addPart"
            @remove-part="removePart"
          />

          <p v-else-if="detailState === 'loading'" class="authoring__notice" role="status">
            正在打开练习…
          </p>
          <p v-else-if="detailState === 'error'" class="authoring__error" role="alert">
            {{ detailError }}
          </p>
          <p v-else class="authoring__notice">
            在左侧选择一份练习；或在题库中打开一道题，点“用这道题练习”开始。
          </p>
        </div>
      </div>
    </template>
  </section>
</template>
