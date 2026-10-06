import { computed, ref, toRaw } from 'vue'
import { defineStore } from 'pinia'

import {
  questionBankApi,
  type QuestionBankDetail,
  type QuestionBankFilters,
  type QuestionBankListItem,
  type QuestionBankListResponse,
  type QuestionBankPaper,
  type QuestionBankPaperListResponse,
  type QuestionBankPaperMetadataInput,
  type QuestionBankPaperMetadataResult,
  type QuestionBankTag,
  type QuestionBankWriteResult,
} from '../api/question-bank'
import { ApiError } from '../api/errors'
import type { PaperQuestionRef } from '../components/question-bank/paper-analysis-status'

export type QuestionBankListState =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'empty'
  | 'error'
  | 'stale-error'

export type QuestionBankDetailState =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'error'

export type QuestionBankListLoader = (
  filters: QuestionBankFilters,
  signal?: AbortSignal,
) => Promise<QuestionBankListResponse>

export type QuestionBankDetailLoader = (
  questionId: number,
  signal?: AbortSignal,
) => Promise<QuestionBankDetail>

export interface QuestionBankWriteApi {
  replaceTags(
    questionId: number,
    expectedRevision: string,
    tags: QuestionBankTag[],
    signal?: AbortSignal,
  ): Promise<QuestionBankWriteResult>
  softDelete(
    questionId: number,
    expectedRevision: string,
    signal?: AbortSignal,
  ): Promise<QuestionBankWriteResult>
  restore(
    questionId: number,
    expectedRevision: string,
    signal?: AbortSignal,
  ): Promise<QuestionBankWriteResult>
}

export interface QuestionBankPaperWriteApi {
  updatePaperMetadata(
    paperId: number,
    expectedUpdatedAt: string,
    metadata: QuestionBankPaperMetadataInput,
    signal?: AbortSignal,
  ): Promise<QuestionBankPaperMetadataResult>
}

const MAX_BATCH_SELECTION = 500

function copyFilters(filters: QuestionBankFilters): QuestionBankFilters {
  return {
    ...filters,
    questionTypes: [...(filters.questionTypes ?? [])],
    paperIds: [...(filters.paperIds ?? [])],
    years: [...(filters.years ?? [])],
    examTypes: [...(filters.examTypes ?? [])],
    grades: [...(filters.grades ?? [])],
    examScopes: [...(filters.examScopes ?? [])],
    knowledgePoints: [...(filters.knowledgePoints ?? [])],
    abilities: [...(filters.abilities ?? [])],
    methods: [...(filters.methods ?? [])],
    thoughts: [...(filters.thoughts ?? [])],
    models: [...(filters.models ?? [])],
    specialTypes: [...(filters.specialTypes ?? [])],
    studentLevels: [...(filters.studentLevels ?? [])],
    teachingStages: [...(filters.teachingStages ?? [])],
    subSkills: [...(filters.subSkills ?? [])],
  }
}

function safeMessage(): string {
  return '题库暂时无法更新，请稍后重试。'
}

function copyDetail(value: QuestionBankDetail): QuestionBankDetail {
  return structuredClone(toRaw(value))
}

export const useQuestionBankStore = defineStore('question-bank', () => {
  const appliedFilters = ref<QuestionBankFilters>({})
  const filterDraft = ref<QuestionBankFilters>({})
  const papers = ref<QuestionBankPaper[]>([])
  const papersState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const paperWriteState = ref<'idle' | 'saving' | 'conflict' | 'error'>('idle')
  const paperWriteMessage = ref('')
  const questions = ref<QuestionBankListItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const totalPages = ref(0)
  const listState = ref<QuestionBankListState>('idle')
  const listError = ref('')
  const listUpdatedAt = ref('')

  const selectedQuestionIds = ref<number[]>([])
  const selectedQuestionId = ref<number | null>(null)
  // 试卷卡片状态（任务↔试卷对应表、未完成题号）放在 store 层：
  // 打开试卷再返回试卷库时组件会重建，持久化后旧状态立即显示、后台再校对。
  const paperQuestionRefs = ref(new Map<number, PaperQuestionRef>())
  const paperJobLinks = ref(new Map<number, number>())
  const paperIncompleteNumbers = ref(new Map<number, string[]>())
  const detail = ref<QuestionBankDetail | null>(null)
  const detailState = ref<QuestionBankDetailState>('idle')
  const detailError = ref('')
  const tagDraft = ref<QuestionBankTag[]>([])
  const writeState = ref<'idle' | 'saving' | 'conflict' | 'error'>('idle')
  const writeMessage = ref('')
  const lastDeleted = ref<{
    question: QuestionBankDetail
    revision: string
  } | null>(null)

  let listGeneration = 0
  let listController: AbortController | null = null
  let detailGeneration = 0
  let detailController: AbortController | null = null

  const selectedCount = computed(() => selectedQuestionIds.value.length)
  const selectedOnPageCount = computed(() => {
    const selected = new Set(selectedQuestionIds.value)
    return questions.value.filter(({ id }) => selected.has(id)).length
  })
  const hiddenSelectionCount = computed(
    () => selectedCount.value - selectedOnPageCount.value,
  )
  const selectionIsFull = computed(
    () => selectedCount.value >= MAX_BATCH_SELECTION,
  )

  async function loadPapers(
    loader: (
      signal?: AbortSignal,
    ) => Promise<QuestionBankPaperListResponse> = questionBankApi.listPapers,
  ): Promise<void> {
    // 已有数据时保持 ready 静默刷新，避免列表被 loading 占位整体卸载（全屏闪动）。
    if (papers.value.length === 0) {
      papersState.value = 'loading'
    }
    try {
      papers.value = (await loader()).items
      papersState.value = 'ready'
    } catch {
      if (papers.value.length === 0) {
        papersState.value = 'error'
      }
    }
  }

  function resetPaperWriteStatus(): void {
    if (paperWriteState.value === 'saving') return
    paperWriteState.value = 'idle'
    paperWriteMessage.value = ''
  }

  async function updatePaperMetadata(
    paperId: number,
    metadata: QuestionBankPaperMetadataInput,
    api: QuestionBankPaperWriteApi = questionBankApi,
    reload: (
      signal?: AbortSignal,
    ) => Promise<QuestionBankPaperListResponse> = questionBankApi.listPapers,
  ): Promise<boolean> {
    if (paperWriteState.value === 'saving') return false
    const paper = papers.value.find(({ id }) => id === paperId)
    if (!paper) {
      paperWriteState.value = 'error'
      paperWriteMessage.value = '这份试卷已不在当前列表，请重新读取试卷库。'
      return false
    }
    paperWriteState.value = 'saving'
    paperWriteMessage.value = ''
    try {
      const result = await api.updatePaperMetadata(
        paperId,
        paper.updated_at,
        { ...metadata },
      )
      if (result.id !== paperId) throw new Error('Paper metadata scope mismatch')
      const index = papers.value.findIndex(({ id }) => id === paperId)
      if (index >= 0) {
        papers.value[index] = {
          ...papers.value[index]!,
          ...result,
        }
      }
      paperWriteState.value = 'idle'
      paperWriteMessage.value = '试卷资料已经保存。'
      return true
    } catch (error) {
      if (error instanceof ApiError && error.code === 'paper_metadata_conflict') {
        paperWriteState.value = 'conflict'
        paperWriteMessage.value = '这份试卷刚刚在别处更新。卡片已刷新，你当前填写的内容仍然保留，请核对后再次保存。'
        await loadPapers(reload)
      } else {
        paperWriteState.value = 'error'
        paperWriteMessage.value = '试卷资料没有保存，你当前填写的内容仍然保留。'
      }
      return false
    }
  }

  async function loadQuestions(
    filters: QuestionBankFilters = {},
    loader: QuestionBankListLoader = questionBankApi.listQuestions,
  ): Promise<void> {
    listController?.abort()
    const controller = new AbortController()
    listController = controller
    const generation = ++listGeneration
    const requested = copyFilters(filters)
    appliedFilters.value = requested
    listError.value = ''
    listState.value = 'loading'
    try {
      const result = await loader(requested, controller.signal)
      if (generation !== listGeneration || controller.signal.aborted) return
      if (
        result.page !== (requested.page ?? 1)
        || result.page_size !== (requested.pageSize ?? 20)
      ) {
        throw new Error('Question list scope mismatch')
      }
      questions.value = result.items
      total.value = result.total
      page.value = result.page
      pageSize.value = result.page_size
      totalPages.value = result.total_pages
      listUpdatedAt.value = new Date().toISOString()
      listState.value = result.total === 0 ? 'empty' : 'ready'
    } catch {
      if (generation !== listGeneration || controller.signal.aborted) return
      listError.value = safeMessage()
      listState.value = questions.value.length > 0 ? 'stale-error' : 'error'
    } finally {
      if (listController === controller) listController = null
    }
  }

  function toggleQuestionSelection(questionId: number, selected?: boolean): boolean {
    const next = new Set(selectedQuestionIds.value)
    const shouldSelect = selected ?? !next.has(questionId)
    if (shouldSelect) {
      if (!next.has(questionId) && next.size >= MAX_BATCH_SELECTION) return false
      next.add(questionId)
    } else {
      next.delete(questionId)
    }
    selectedQuestionIds.value = [...next]
    return true
  }

  function selectCurrentPage(selected: boolean): void {
    const next = new Set(selectedQuestionIds.value)
    if (!selected) {
      for (const question of questions.value) next.delete(question.id)
    } else {
      for (const question of questions.value) {
        if (next.size >= MAX_BATCH_SELECTION) break
        next.add(question.id)
      }
    }
    selectedQuestionIds.value = [...next]
  }

  function clearSelection(): void {
    selectedQuestionIds.value = []
  }

  async function selectQuestion(
    questionId: number | null,
    loader: QuestionBankDetailLoader = questionBankApi.getQuestion,
  ): Promise<void> {
    detailController?.abort()
    detailController = null
    detailGeneration += 1
    selectedQuestionId.value = questionId
    detail.value = null
    detailError.value = ''
    if (questionId === null) {
      detailState.value = 'idle'
      return
    }

    const controller = new AbortController()
    detailController = controller
    const generation = detailGeneration
    detailState.value = 'loading'
    try {
      const result = await loader(questionId, controller.signal)
      if (
        generation !== detailGeneration
        || controller.signal.aborted
        || selectedQuestionId.value !== questionId
      ) return
      detail.value = result
      tagDraft.value = result.tags.map((tag) => ({ ...tag }))
      detailState.value = 'ready'
    } catch {
      if (
        generation !== detailGeneration
        || controller.signal.aborted
        || selectedQuestionId.value !== questionId
      ) return
      detailError.value = '题目详情暂时无法打开，请稍后重试。'
      detailState.value = 'error'
    } finally {
      if (detailController === controller) detailController = null
    }
  }

  function applyTagWrite(questionId: number, revision: string, tags: QuestionBankTag[]): void {
    const listItem = questions.value.find(({ id }) => id === questionId)
    if (listItem) {
      listItem.revision = revision
      listItem.tags = tags
    }
    if (detail.value?.id === questionId) {
      detail.value.revision = revision
      detail.value.tags = tags
    }
  }

  function replaceTagDraft(tags: QuestionBankTag[]): void {
    tagDraft.value = tags.map((tag) => ({ ...tag }))
    if (writeState.value !== 'saving') {
      writeState.value = 'idle'
      writeMessage.value = ''
    }
  }

  async function saveTags(api: QuestionBankWriteApi = questionBankApi): Promise<boolean> {
    if (!detail.value || writeState.value === 'saving') return false
    const snapshot = tagDraft.value.map((tag) => ({ ...tag }))
    writeState.value = 'saving'
    writeMessage.value = ''
    try {
      const result = await api.replaceTags(
        detail.value.id,
        detail.value.revision,
        snapshot,
      )
      applyTagWrite(result.question_id, result.revision, result.tags)
      tagDraft.value = result.tags.map((tag) => ({ ...tag }))
      writeState.value = 'idle'
      writeMessage.value = '标签已经保存。'
      return true
    } catch (error) {
      if (error instanceof ApiError && error.code === 'question_write_conflict') {
        writeState.value = 'conflict'
        writeMessage.value = '这道题刚刚被其他操作更新。你的标签草稿仍在，请重新载入后核对。'
      } else {
        writeState.value = 'error'
        writeMessage.value = '标签没有保存，原草稿仍在。'
      }
      return false
    }
  }

  async function deleteCurrent(api: QuestionBankWriteApi = questionBankApi): Promise<boolean> {
    if (!detail.value || writeState.value === 'saving') return false
    const snapshot = copyDetail(detail.value)
    writeState.value = 'saving'
    writeMessage.value = ''
    try {
      const result = await api.softDelete(snapshot.id, snapshot.revision)
      lastDeleted.value = {
        question: snapshot,
        revision: result.revision,
      }
      removeQuestion(snapshot.id)
      writeState.value = 'idle'
      writeMessage.value = '题目已移出当前题库，可立即恢复。'
      return true
    } catch (error) {
      writeState.value = error instanceof ApiError && error.code === 'question_write_conflict'
        ? 'conflict'
        : 'error'
      writeMessage.value = writeState.value === 'conflict'
        ? '这道题刚刚被更新，请重新载入后再删除。'
        : '删除没有完成，题目保持不变。'
      return false
    }
  }

  async function restoreLastDeleted(
    api: QuestionBankWriteApi = questionBankApi,
  ): Promise<boolean> {
    if (!lastDeleted.value || writeState.value === 'saving') return false
    const pending = lastDeleted.value
    writeState.value = 'saving'
    writeMessage.value = ''
    try {
      const result = await api.restore(pending.question.id, pending.revision)
      const restored = {
        ...pending.question,
        revision: result.revision,
        tags: result.tags,
      }
      questions.value.unshift(restored)
      total.value += 1
      lastDeleted.value = null
      writeState.value = 'idle'
      writeMessage.value = '题目已经恢复。'
      await selectQuestion(restored.id, async () => restored)
      return true
    } catch (error) {
      writeState.value = error instanceof ApiError && error.code === 'question_write_conflict'
        ? 'conflict'
        : 'error'
      writeMessage.value = '恢复没有完成，请重新载入题库后核对。'
      return false
    }
  }

  function removeQuestion(questionId: number): void {
    const index = questions.value.findIndex(({ id }) => id === questionId)
    if (index >= 0) {
      questions.value.splice(index, 1)
      total.value = Math.max(0, total.value - 1)
    }
    toggleQuestionSelection(questionId, false)
    if (selectedQuestionId.value === questionId) {
      selectedQuestionId.value = null
      detail.value = null
      detailState.value = 'idle'
    }
  }

  return {
    appliedFilters,
    filterDraft,
    papers,
    papersState,
    paperWriteState,
    paperWriteMessage,
    questions,
    total,
    page,
    pageSize,
    totalPages,
    listState,
    listError,
    listUpdatedAt,
    selectedQuestionIds,
    paperQuestionRefs,
    paperJobLinks,
    paperIncompleteNumbers,
    selectedCount,
    selectedOnPageCount,
    hiddenSelectionCount,
    selectionIsFull,
    selectedQuestionId,
    detail,
    detailState,
    detailError,
    tagDraft,
    writeState,
    writeMessage,
    lastDeleted,
    loadPapers,
    resetPaperWriteStatus,
    updatePaperMetadata,
    loadQuestions,
    toggleQuestionSelection,
    selectCurrentPage,
    clearSelection,
    selectQuestion,
    applyTagWrite,
    replaceTagDraft,
    saveTags,
    deleteCurrent,
    restoreLastDeleted,
    removeQuestion,
  }
})
