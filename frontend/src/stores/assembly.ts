import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  assemblyApi,
  type AssemblyDraft,
  type AssemblyExportFormat,
  type AssemblyLayoutMode,
  type AssemblyPreviewMode,
  type AssemblyQuestion,
  type AssemblyRecord,
  type AssemblySection,
} from '../api/assembly'
import { ApiError } from '../api/errors'
import type { JobResponse } from '../api/jobs'
import { useJobStore } from './jobs'

export type AssemblyLoadState = 'idle' | 'loading' | 'ready' | 'empty' | 'error'
export type AssemblySaveState = 'idle' | 'saving' | 'conflict' | 'error'

export interface AssemblyStoreDependencies {
  api: typeof assemblyApi
}

const defaultDependencies: AssemblyStoreDependencies = { api: assemblyApi }

function emptyDraft(): AssemblyDraft {
  return {
    basket_ids: [],
    order_ids: [],
    sections: [],
    title: '',
    header_text: '',
    include_answer: true,
    layout_mode: 'sequential',
    preview_mode: 'teacher',
    revision: '0'.repeat(64),
  }
}

function cleanQuestionIds(values: readonly number[]): number[] {
  const result: number[] = []
  const seen = new Set<number>()
  for (const value of values) {
    if (!Number.isSafeInteger(value) || value <= 0 || seen.has(value)) continue
    seen.add(value)
    result.push(value)
    if (result.length >= 500) break
  }
  return result
}

function draftPayload(draft: AssemblyDraft): AssemblyDraft {
  return {
    ...draft,
    basket_ids: cleanQuestionIds(draft.basket_ids),
    order_ids: cleanQuestionIds(draft.order_ids).filter((id) => draft.basket_ids.includes(id)),
    sections: draft.sections.map((section) => ({
      ...section,
      question_ids: cleanQuestionIds(section.question_ids).filter((id) => draft.basket_ids.includes(id)),
    })),
  }
}

function safeMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === 'assembly_draft_conflict') {
    return '组卷草稿已经被另一个窗口更新，请重新加载后再保存。'
  }
  return '组卷工作台暂时无法更新，请稍后重试。'
}

export const useAssemblyStore = defineStore('assembly', () => {
  const draft = ref<AssemblyDraft>(emptyDraft())
  const questions = ref<AssemblyQuestion[]>([])
  const missingQuestionIds = ref<number[]>([])
  const records = ref<AssemblyRecord[]>([])
  const loadState = ref<AssemblyLoadState>('idle')
  const questionsState = ref<AssemblyLoadState>('idle')
  const recordsState = ref<AssemblyLoadState>('idle')
  const saveState = ref<AssemblySaveState>('idle')
  const message = ref('')
  const exportJobId = ref<number | null>(null)
  const submitting = ref(false)
  let dependencies = defaultDependencies
  let questionGeneration = 0

  const questionMap = computed(() => new Map(questions.value.map((item) => [item.id, item])))
  const orderedQuestions = computed(() => draft.value.order_ids
    .map((id) => questionMap.value.get(id))
    .filter((item): item is AssemblyQuestion => item !== undefined))
  const totalScore = computed(() => orderedQuestions.value.reduce(
    (sum, item) => sum + (item.score_value ?? 0),
    0,
  ))
  const selectedQuestionCount = computed(() => draft.value.order_ids.length)
  const canExport = computed(() => selectedQuestionCount.value > 0 && saveState.value !== 'saving')

  function configure(next?: Partial<AssemblyStoreDependencies>): void {
    dependencies = { ...defaultDependencies, ...next }
  }

  async function load(next?: Partial<AssemblyStoreDependencies>): Promise<void> {
    configure(next)
    loadState.value = 'loading'
    message.value = ''
    try {
      draft.value = await dependencies.api.getDraft()
      loadState.value = draft.value.order_ids.length ? 'ready' : 'empty'
      await Promise.all([loadQuestions(), loadRecords()])
    } catch (error) {
      loadState.value = 'error'
      message.value = safeMessage(error)
    }
  }

  async function loadQuestions(): Promise<void> {
    const ids = [...draft.value.order_ids]
    const generation = ++questionGeneration
    if (!ids.length) {
      questions.value = []
      missingQuestionIds.value = []
      questionsState.value = 'empty'
      return
    }
    questionsState.value = 'loading'
    try {
      const result = await dependencies.api.resolveQuestions(ids)
      if (
        generation !== questionGeneration ||
        ids.join(',') !== draft.value.order_ids.join(',')
      ) return
      questions.value = result.items
      missingQuestionIds.value = result.missing_question_ids
      questionsState.value = result.items.length ? 'ready' : 'empty'
    } catch (error) {
      if (
        generation !== questionGeneration ||
        ids.join(',') !== draft.value.order_ids.join(',')
      ) return
      questionsState.value = 'error'
      message.value = safeMessage(error)
    }
  }

  async function loadRecords(): Promise<void> {
    recordsState.value = 'loading'
    try {
      const result = await dependencies.api.listRecords()
      records.value = result.items
      recordsState.value = result.items.length ? 'ready' : 'empty'
    } catch (error) {
      recordsState.value = 'error'
      message.value = safeMessage(error)
    }
  }

  async function save(nextDraft = draft.value): Promise<boolean> {
    saveState.value = 'saving'
    message.value = ''
    try {
      draft.value = await dependencies.api.saveDraft(draft.value.revision, draftPayload(nextDraft))
      saveState.value = 'idle'
      loadState.value = draft.value.order_ids.length ? 'ready' : 'empty'
      await loadQuestions()
      return true
    } catch (error) {
      saveState.value = error instanceof ApiError && error.code === 'assembly_draft_conflict'
        ? 'conflict'
        : 'error'
      message.value = safeMessage(error)
      return false
    }
  }

  async function addQuestions(questionIds: readonly number[]): Promise<boolean> {
    const nextIds = cleanQuestionIds([...draft.value.basket_ids, ...questionIds])
    const nextOrder = cleanQuestionIds([...draft.value.order_ids, ...questionIds])
    return save({
      ...draft.value,
      basket_ids: nextIds,
      order_ids: nextOrder.filter((id) => nextIds.includes(id)),
    })
  }

  async function removeQuestion(questionId: number): Promise<boolean> {
    return save({
      ...draft.value,
      basket_ids: draft.value.basket_ids.filter((id) => id !== questionId),
      order_ids: draft.value.order_ids.filter((id) => id !== questionId),
      sections: draft.value.sections.map((section) => ({
        ...section,
        question_ids: section.question_ids.filter((id) => id !== questionId),
      })),
    })
  }

  async function moveQuestion(questionId: number, direction: -1 | 1): Promise<boolean> {
    const order = [...draft.value.order_ids]
    const index = order.indexOf(questionId)
    const nextIndex = index + direction
    if (index < 0 || nextIndex < 0 || nextIndex >= order.length) return false
    const [item] = order.splice(index, 1)
    order.splice(nextIndex, 0, item!)
    return save({ ...draft.value, order_ids: order })
  }

  async function moveQuestionBefore(questionId: number, beforeQuestionId: number): Promise<boolean> {
    if (questionId === beforeQuestionId) return false
    const order = draft.value.order_ids.filter((id) => id !== questionId)
    const targetIndex = order.indexOf(beforeQuestionId)
    if (targetIndex < 0 || !draft.value.order_ids.includes(questionId)) return false
    order.splice(targetIndex, 0, questionId)
    return save({ ...draft.value, order_ids: order })
  }

  async function updateSettings(patch: {
    title?: string
    header_text?: string
    include_answer?: boolean
    layout_mode?: AssemblyLayoutMode
    preview_mode?: AssemblyPreviewMode
  }): Promise<boolean> {
    return save({ ...draft.value, ...patch })
  }

  async function replaceSections(sections: AssemblySection[]): Promise<boolean> {
    return save({ ...draft.value, sections, layout_mode: 'sections' })
  }

  async function submitExport(format: AssemblyExportFormat): Promise<JobResponse | null> {
    if (!canExport.value || submitting.value) return null
    submitting.value = true
    message.value = ''
    try {
      const job = await dependencies.api.submitExport(draft.value.revision, format)
      exportJobId.value = job.id
      useJobStore().track(job)
      return job
    } catch (error) {
      message.value = safeMessage(error)
      return null
    } finally {
      submitting.value = false
    }
  }

  async function retryExport(jobId: number): Promise<JobResponse | null> {
    if (submitting.value) return null
    submitting.value = true
    try {
      const job = await dependencies.api.retryExport(jobId)
      exportJobId.value = job.id
      useJobStore().track(job)
      return job
    } catch (error) {
      message.value = safeMessage(error)
      return null
    } finally {
      submitting.value = false
    }
  }

  async function deleteRecord(recordId: string): Promise<boolean> {
    try {
      const result = await dependencies.api.deleteRecord(recordId)
      if (result.deleted) records.value = records.value.filter((item) => item.id !== recordId)
      return result.deleted
    } catch (error) {
      message.value = safeMessage(error)
      return false
    }
  }

  async function restoreRecord(recordId: string): Promise<boolean> {
    saveState.value = 'saving'
    message.value = ''
    try {
      draft.value = await dependencies.api.restoreRecord(recordId, draft.value.revision)
      saveState.value = 'idle'
      loadState.value = draft.value.order_ids.length ? 'ready' : 'empty'
      await loadQuestions()
      return true
    } catch (error) {
      saveState.value = error instanceof ApiError && error.code === 'assembly_draft_conflict'
        ? 'conflict'
        : 'error'
      message.value = safeMessage(error)
      return false
    }
  }

  return {
    draft,
    questions,
    missingQuestionIds,
    records,
    loadState,
    questionsState,
    recordsState,
    saveState,
    message,
    exportJobId,
    submitting,
    questionMap,
    orderedQuestions,
    totalScore,
    selectedQuestionCount,
    canExport,
    configure,
    load,
    loadQuestions,
    loadRecords,
    save,
    addQuestions,
    removeQuestion,
    moveQuestion,
    moveQuestionBefore,
    updateSettings,
    replaceSections,
    submitExport,
    retryExport,
    deleteRecord,
    restoreRecord,
  }
})
