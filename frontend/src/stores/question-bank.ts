import { computed, ref } from 'vue'
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
  type QuestionBankPaperStateResult,
  type QuestionBankTag,
  type QuestionBankWriteResult,
} from '../api/question-bank'
import { ApiError } from '../api/errors'

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

export interface QuestionBankPaperStateApi {
  listPapers(
    deleted: boolean,
    signal?: AbortSignal,
  ): Promise<QuestionBankPaperListResponse>
  trashPaper(
    paperId: number,
    expectedUpdatedAt: string,
    signal?: AbortSignal,
  ): Promise<QuestionBankPaperStateResult>
  restorePaper(
    paperId: number,
    expectedUpdatedAt: string,
    signal?: AbortSignal,
  ): Promise<QuestionBankPaperStateResult>
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
  return JSON.parse(JSON.stringify(value)) as QuestionBankDetail
}

export const useQuestionBankStore = defineStore('question-bank', () => {
  const appliedFilters = ref<QuestionBankFilters>({})
  const filterDraft = ref<QuestionBankFilters>({})
  const papers = ref<QuestionBankPaper[]>([])
  const papersState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const trashedPapers = ref<QuestionBankPaper[]>([])
  const trashPapersState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const paperWriteState = ref<'idle' | 'saving' | 'conflict' | 'error'>('idle')
  const paperWriteMessage = ref('')
  const paperTrashState = ref<'idle' | 'saving' | 'conflict' | 'error'>('idle')
  const paperTrashMessage = ref('')
  const paperTrashBusyId = ref<number | null>(null)
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
    papersState.value = 'loading'
    try {
      papers.value = (await loader()).items
      papersState.value = 'ready'
    } catch {
      papersState.value = 'error'
    }
  }

  async function loadTrashedPapers(
    loader: (
      signal?: AbortSignal,
    ) => Promise<QuestionBankPaperListResponse> = (
      signal,
    ) => questionBankApi.listPapers(true, signal),
  ): Promise<void> {
    trashPapersState.value = 'loading'
    try {
      trashedPapers.value = (await loader()).items
      trashPapersState.value = 'ready'
    } catch {
      trashPapersState.value = 'error'
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

  async function movePaperToTrash(
    paperId: number,
    api: QuestionBankPaperStateApi = questionBankApi,
  ): Promise<boolean> {
    return changePaperDeletedState(paperId, true, api)
  }

  function resetPaperTrashStatus(): void {
    if (paperTrashState.value === 'saving') return
    paperTrashState.value = 'idle'
    paperTrashMessage.value = ''
  }

  async function restorePaperFromTrash(
    paperId: number,
    api: QuestionBankPaperStateApi = questionBankApi,
  ): Promise<boolean> {
    return changePaperDeletedState(paperId, false, api)
  }

  async function changePaperDeletedState(
    paperId: number,
    deleted: boolean,
    api: QuestionBankPaperStateApi,
  ): Promise<boolean> {
    if (paperTrashBusyId.value !== null) return false
    const source = (deleted ? papers.value : trashedPapers.value)
      .find(({ id }) => id === paperId)
    if (!source) {
      paperTrashState.value = 'error'
      paperTrashMessage.value = '这份试卷已不在当前列表，请重新读取后核对。'
      return false
    }
    paperTrashBusyId.value = paperId
    paperTrashState.value = 'saving'
    paperTrashMessage.value = ''
    try {
      const result = deleted
        ? await api.trashPaper(paperId, source.updated_at)
        : await api.restorePaper(paperId, source.updated_at)
      if (result.id !== paperId || result.deleted !== deleted) {
        throw new Error('Paper state scope mismatch')
      }
      moveLocalPaper(source, result)
      paperTrashState.value = 'idle'
      paperTrashMessage.value = deleted
        ? '试卷已经移入回收站，可在回收站中恢复。'
        : '试卷已经恢复到试卷库。'
      return true
    } catch (error) {
      const confirmed = await reconcilePaperState(paperId, deleted, api)
      if (confirmed) {
        paperTrashState.value = 'idle'
        paperTrashMessage.value = deleted
          ? '试卷已经移入回收站；页面已重新核对服务器状态。'
          : '试卷已经恢复；页面已重新核对服务器状态。'
        return true
      }
      paperTrashState.value = (
        error instanceof ApiError && error.code === 'paper_state_conflict'
      )
        ? 'conflict'
        : 'error'
      paperTrashMessage.value = paperTrashState.value === 'conflict'
        ? '这份试卷刚刚在别处变化，列表已刷新，请核对后再操作。'
        : '操作结果暂时无法确认，列表已重新读取，请核对后再试。'
      return false
    } finally {
      paperTrashBusyId.value = null
    }
  }

  function moveLocalPaper(
    paper: QuestionBankPaper,
    result: QuestionBankPaperStateResult,
  ): void {
    const next = {
      ...paper,
      import_status: result.import_status,
      updated_at: result.updated_at,
    }
    if (result.deleted) {
      papers.value = papers.value.filter(({ id }) => id !== paper.id)
      trashedPapers.value = [
        next,
        ...trashedPapers.value.filter(({ id }) => id !== paper.id),
      ]
    } else {
      trashedPapers.value = trashedPapers.value.filter(({ id }) => id !== paper.id)
      papers.value = [
        next,
        ...papers.value.filter(({ id }) => id !== paper.id),
      ]
    }
  }

  async function reconcilePaperState(
    paperId: number,
    deleted: boolean,
    api: QuestionBankPaperStateApi,
  ): Promise<boolean> {
    try {
      const [active, trash] = await Promise.all([
        api.listPapers(false),
        api.listPapers(true),
      ])
      papers.value = active.items
      trashedPapers.value = trash.items
      papersState.value = 'ready'
      trashPapersState.value = 'ready'
      const activeHasPaper = active.items.some(({ id }) => id === paperId)
      const trashHasPaper = trash.items.some(({ id }) => id === paperId)
      return deleted
        ? !activeHasPaper && trashHasPaper
        : activeHasPaper && !trashHasPaper
    } catch {
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
    trashedPapers,
    trashPapersState,
    paperWriteState,
    paperWriteMessage,
    paperTrashState,
    paperTrashMessage,
    paperTrashBusyId,
    questions,
    total,
    page,
    pageSize,
    totalPages,
    listState,
    listError,
    listUpdatedAt,
    selectedQuestionIds,
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
    loadTrashedPapers,
    resetPaperWriteStatus,
    updatePaperMetadata,
    movePaperToTrash,
    resetPaperTrashStatus,
    restorePaperFromTrash,
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
