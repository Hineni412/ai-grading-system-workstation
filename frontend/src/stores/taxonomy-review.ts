import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  questionBankTaxonomyApi,
  TAXONOMY_DIMENSIONS,
  type TaxonomyDimension,
  type TaxonomyProposal,
  type TaxonomyReviewDecision,
  type TaxonomyTerm,
} from '../api/question-bank-taxonomy'
import { ApiError, isAmbiguousWriteError } from '../api/errors'

export type TaxonomyReviewApi = typeof questionBankTaxonomyApi
export type TaxonomyReviewLoadState = 'idle' | 'loading' | 'ready' | 'empty' | 'error'
export type TaxonomyReviewWriteState = 'idle' | 'saving' | 'conflict' | 'error'

export interface TaxonomyReviewAction {
  decision: TaxonomyReviewDecision
  edited_name?: string
  target_term_id?: string
}

function emptyDimensions(): Record<TaxonomyDimension, TaxonomyTerm[]> {
  return {
    curriculum: [],
    knowledge: [],
    ability: [],
    method: [],
    model: [],
  }
}

function successMessage(
  decision: TaxonomyReviewDecision,
  applicationStatus: string | null,
): string {
  const action = {
    approve: '已批准并加入规范词表。',
    edit: '已按修改后的名称批准并加入规范词表。',
    merge: '已合并到现有规范词。',
    reject: '已拒绝，这个新词不会进入规范词表。',
  }[decision]
  if (applicationStatus === 'pending') return `${action} 关联题目的标签正在更新。`
  if (applicationStatus === 'failed') return `${action} 关联题目暂未更新，可稍后重试应用。`
  return action
}

export const useTaxonomyReviewStore = defineStore('taxonomy-review', () => {
  const revision = ref(0)
  const dimensions = ref<Record<TaxonomyDimension, TaxonomyTerm[]>>(emptyDimensions())
  const proposals = ref<TaxonomyProposal[]>([])
  const pendingCount = ref(0)
  const loadState = ref<TaxonomyReviewLoadState>('idle')
  const writeState = ref<TaxonomyReviewWriteState>('idle')
  const busyProposalId = ref<string | null>(null)
  const message = ref('')

  let loadGeneration = 0
  let loadController: AbortController | null = null

  const hasCatalog = computed(
    () => TAXONOMY_DIMENSIONS.some((key) => dimensions.value[key].length > 0),
  )

  function termsFor(dimension: TaxonomyDimension): TaxonomyTerm[] {
    return dimensions.value[dimension]
  }

  async function load(
    api: TaxonomyReviewApi = questionBankTaxonomyApi,
  ): Promise<boolean> {
    loadController?.abort()
    const controller = new AbortController()
    loadController = controller
    const generation = ++loadGeneration
    loadState.value = 'loading'
    message.value = ''
    try {
      let [catalog, proposalList] = await Promise.all([
        api.getCatalog(controller.signal),
        api.listProposals(controller.signal),
      ])
      if (catalog.revision !== proposalList.revision) {
        catalog = await api.getCatalog(controller.signal)
        proposalList = await api.listProposals(controller.signal)
        if (catalog.revision !== proposalList.revision) {
          throw new Error('Taxonomy snapshot changed while loading')
        }
      }
      if (generation !== loadGeneration || controller.signal.aborted) return false
      dimensions.value = catalog.dimensions
      proposals.value = proposalList.items
      pendingCount.value = proposalList.counts.pending
      revision.value = proposalList.revision
      loadState.value = proposalList.counts.pending === 0 ? 'empty' : 'ready'
      return true
    } catch {
      if (generation !== loadGeneration || controller.signal.aborted) return false
      loadState.value = 'error'
      message.value = '新词候选暂时无法读取，请稍后重新读取。'
      return false
    } finally {
      if (loadController === controller) loadController = null
    }
  }

  function addApprovedTerm(term: TaxonomyTerm): void {
    const terms = dimensions.value[term.dimension]
    const index = terms.findIndex(({ id }) => id === term.id)
    if (index >= 0) terms[index] = term
    else terms.push(term)
    terms.sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
  }

  async function review(
    proposalId: string,
    action: TaxonomyReviewAction,
    api: TaxonomyReviewApi = questionBankTaxonomyApi,
  ): Promise<boolean> {
    if (busyProposalId.value || writeState.value === 'saving') return false
    const expectedRevision = revision.value
    busyProposalId.value = proposalId
    writeState.value = 'saving'
    message.value = ''
    try {
      const result = await api.reviewProposal(proposalId, {
        ...action,
        expected_revision: expectedRevision,
        request_token: globalThis.crypto.randomUUID().replace(/-/g, ''),
      })
      revision.value = result.revision
      const index = proposals.value.findIndex(({ id }) => id === proposalId)
      if (result.proposal.status === 'pending') {
        if (index >= 0) proposals.value[index] = result.proposal
      } else if (index >= 0) {
        proposals.value.splice(index, 1)
      }
      pendingCount.value = Math.max(0, pendingCount.value - (
        result.proposal.status === 'pending' ? 0 : 1
      ))
      if (result.approved_term) addApprovedTerm(result.approved_term)
      loadState.value = pendingCount.value === 0 ? 'empty' : 'ready'
      writeState.value = 'idle'
      message.value = successMessage(action.decision, result.application_status)
      return true
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const refreshed = await load(api)
        writeState.value = 'conflict'
        message.value = refreshed
          ? '词表刚刚发生变化。候选已刷新，你填写的名称和合并目标仍保留，请核对后再次确认。'
          : '词表刚刚发生变化，但最新候选暂时无法读取。你的填写仍保留，请稍后刷新后再确认。'
      } else if (isAmbiguousWriteError(error)) {
        const refreshed = await load(api)
        writeState.value = 'error'
        message.value = refreshed
          ? '审核结果未能确认，已重新读取最新状态。请先核对候选是否仍在，再决定是否重试。'
          : '审核结果未能确认，最新状态也暂时无法读取。请勿立即重复操作，稍后刷新后再核对。'
      } else {
        writeState.value = 'error'
        message.value = '本次审核没有完成，你填写的内容仍保留。'
      }
      return false
    } finally {
      busyProposalId.value = null
    }
  }

  function clearMessage(): void {
    message.value = ''
    if (writeState.value !== 'saving') writeState.value = 'idle'
  }

  return {
    revision,
    dimensions,
    proposals,
    pendingCount,
    loadState,
    writeState,
    busyProposalId,
    message,
    hasCatalog,
    termsFor,
    load,
    review,
    clearMessage,
  }
})
