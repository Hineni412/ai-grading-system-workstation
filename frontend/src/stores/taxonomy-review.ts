import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  questionBankTaxonomyApi,
  TAXONOMY_DIMENSIONS,
  type TaxonomyDimension,
  type TaxonomyProposal,
  type TaxonomyReviewDecision,
  type TaxonomySuggestionItem,
  type TaxonomySuggestionRun,
  type TaxonomyTerm,
} from '../api/question-bank-taxonomy'
import { ApiError, isAmbiguousWriteError } from '../api/errors'

export type TaxonomyReviewApi = typeof questionBankTaxonomyApi
export const TAXONOMY_SUGGESTION_RUN_STORAGE_KEY =
  'ai-grading.taxonomy-suggestion-run-id'
export const TAXONOMY_SUGGESTION_COMMAND_STORAGE_KEY =
  'ai-grading.taxonomy-suggestion-pending-command'
export type TaxonomyReviewLoadState = 'idle' | 'loading' | 'ready' | 'empty' | 'error'
export type TaxonomyReviewWriteState = 'idle' | 'saving' | 'conflict' | 'error'
export type TaxonomySuggestionState = 'idle' | 'starting' | 'running' | 'error'

export interface TaxonomyReviewAction {
  decision: TaxonomyReviewDecision
  edited_name?: string
  target_term_id?: string
  target_term_ids?: string[]
  question_ids?: number[]
}

export interface TaxonomyProposalError {
  code: string
  message: string
  requestId: string
  retryable: boolean
}

function emptyDimensions(): Record<TaxonomyDimension, TaxonomyTerm[]> {
  return {
    curriculum: [],
    knowledge: [],
    ability: [],
    method: [],
    model: [],
    special_type: [],
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
  if (applicationStatus === 'partial') return `${action} 部分关联题目已更新，其余题目可稍后重试。`
  if (applicationStatus === 'failed') return `${action} 关联题目暂未更新，可稍后重试应用。`
  return action
}

function proposalError(error: unknown): TaxonomyProposalError {
  if (!(error instanceof ApiError)) {
    return {
      code: 'taxonomy_review_failed',
      message: '本次审核没有完成。你的填写仍然保留，可以稍后再试。',
      requestId: '',
      retryable: false,
    }
  }
  const storageMessages: Record<string, string> = {
    taxonomy_storage_read_only:
      '标签状态当前不能写入。请用正常方式重新启动系统，确认服务恢复后再试。',
    taxonomy_storage_busy:
      '另一个操作正在保存标签，请稍后再试。你当前填写的内容不会丢失。',
    taxonomy_storage_invalid:
      '标签状态文件内容异常。系统已经停止写入以保护现有标签，请联系维护人员处理。',
    taxonomy_storage_unavailable:
      '标签状态暂时无法保存。请确认系统以正常方式启动后再试。',
  }
  const message = storageMessages[error.code]
    ?? (
      error.status === 503
        ? '服务器暂时无法保存这次审核。你的填写仍然保留，请稍后再试。'
        : '本次审核没有完成。你的填写仍然保留，可以稍后再试。'
    )
  return {
    code: error.code,
    message,
    requestId: error.requestId,
    retryable: error.retryable,
  }
}

export const useTaxonomyReviewStore = defineStore('taxonomy-review', () => {
  const revision = ref(0)
  const dimensions = ref<Record<TaxonomyDimension, TaxonomyTerm[]>>(emptyDimensions())
  const proposals = ref<TaxonomyProposal[]>([])
  const pendingCount = ref(0)
  const historicalUnavailableCount = ref(0)
  const loadState = ref<TaxonomyReviewLoadState>('idle')
  const writeState = ref<TaxonomyReviewWriteState>('idle')
  const busyProposalId = ref<string | null>(null)
  const message = ref('')
  const proposalErrors = ref<Record<string, TaxonomyProposalError>>({})
  const suggestionRun = ref<TaxonomySuggestionRun | null>(null)
  const suggestionState = ref<TaxonomySuggestionState>('idle')
  const suggestionMessage = ref('')
  const suggestionJobId = ref<number | null>(null)

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
      pendingCount.value = proposalList.counts.actionable
      historicalUnavailableCount.value = (
        proposalList.counts.historical_unavailable
      )
      revision.value = proposalList.revision
      loadState.value = proposalList.counts.actionable === 0 ? 'empty' : 'ready'
      if (!suggestionRun.value) await restoreSuggestions(api)
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
    delete proposalErrors.value[proposalId]
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
      for (const term of result.approved_terms) addApprovedTerm(term)
      if (
        suggestionRun.value
        && suggestionRun.value.taxonomy_revision !== result.revision
      ) {
        suggestionRun.value = {
          ...suggestionRun.value,
          status: 'stale',
          stale: true,
          retryable: false,
        }
        suggestionMessage.value = '词表已经更新，剩余 AI 建议需要重新判断。'
      }
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
        proposalErrors.value[proposalId] = proposalError(error)
        message.value = '本次审核没有完成，请查看对应候选卡片中的具体原因。'
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

  function suggestionFor(proposalId: string): TaxonomySuggestionItem | null {
    return suggestionRun.value?.items.find(
      (item) => item.proposal_id === proposalId,
    ) ?? null
  }

  async function restoreSuggestions(
    api: TaxonomyReviewApi = questionBankTaxonomyApi,
  ): Promise<boolean> {
    const runId = readRememberedRunId()
    if (!runId || suggestionRun.value) return false
    try {
      suggestionRun.value = await api.getSuggestionRun(runId)
      forgetPendingCommand()
      suggestionState.value = ['queued', 'running', 'cancelling'].includes(
        suggestionRun.value.status,
      ) ? 'running' : 'idle'
      suggestionMessage.value = suggestionRun.value.stale
        ? '已恢复上次记录；词表已经变化，这批建议不能再采用。'
        : '已恢复上次 AI 建议进度，已完成的结果仍然保留。'
      return true
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        forgetRunId()
      } else {
        suggestionMessage.value = '上次 AI 建议进度暂时无法恢复，可稍后刷新。'
      }
      return false
    }
  }

  async function startSuggestions(
    proposalIds: string[],
    api: TaxonomyReviewApi = questionBankTaxonomyApi,
  ): Promise<boolean> {
    if (suggestionState.value === 'starting' || proposalIds.length === 0) return false
    const normalizedIds = [...new Set(proposalIds.filter(Boolean))].sort()
    const command = pendingCommand({
      kind: 'start',
      proposal_ids: normalizedIds,
      expected_revision: revision.value,
    })
    if (!command) {
      suggestionState.value = 'error'
      suggestionMessage.value = '上一次付费请求的结果尚未确认，请保持原选择并再次核对。'
      return false
    }
    suggestionState.value = 'starting'
    suggestionMessage.value = ''
    try {
      const result = await api.startSuggestions({
        proposal_ids: normalizedIds,
        expected_revision: revision.value,
        request_token: command.requestToken,
      })
      clearPendingCommand(command.fingerprint)
      suggestionRun.value = result.run
      rememberRunId(result.run.run_id)
      suggestionJobId.value = result.job.id
      suggestionState.value = result.run.status === 'queued'
        || result.run.status === 'running'
        ? 'running'
        : 'idle'
      suggestionMessage.value = 'AI 只会给出归并建议，仍需你逐项确认后才会写入。'
      return true
    } catch (error) {
      suggestionState.value = 'error'
      if (isAmbiguousWriteError(error)) {
        suggestionMessage.value = '启动结果未确认；再次点击会核对同一请求，不会换新请求重复计费。'
      } else {
        clearPendingCommand(command.fingerprint)
        suggestionMessage.value = 'AI 建议任务没有启动，请稍后重试。'
      }
      return false
    }
  }

  async function refreshSuggestions(
    api: TaxonomyReviewApi = questionBankTaxonomyApi,
  ): Promise<boolean> {
    if (!suggestionRun.value) return false
    try {
      const run = await api.getSuggestionRun(suggestionRun.value.run_id)
      suggestionRun.value = run
      rememberRunId(run.run_id)
      suggestionState.value = ['queued', 'running', 'cancelling'].includes(run.status)
        ? 'running'
        : 'idle'
      if (run.stale) suggestionMessage.value = '词表已经变化，这批建议已失效，请重新判断。'
      return true
    } catch {
      suggestionState.value = 'error'
      suggestionMessage.value = 'AI 建议进度暂时无法读取，已完成的建议不会丢失。'
      return false
    }
  }

  async function cancelSuggestions(
    api: TaxonomyReviewApi = questionBankTaxonomyApi,
  ): Promise<boolean> {
    if (!suggestionRun.value) return false
    try {
      suggestionRun.value = await api.cancelSuggestionRun(
        suggestionRun.value.run_id,
      )
      rememberRunId(suggestionRun.value.run_id)
      suggestionState.value = 'idle'
      suggestionMessage.value = '已停止尚未开始的项目，已经完成的建议仍会保留。'
      return true
    } catch {
      suggestionState.value = 'error'
      suggestionMessage.value = '暂时无法确认是否已停止，请先刷新进度。'
      return false
    }
  }

  async function retrySuggestions(
    api: TaxonomyReviewApi = questionBankTaxonomyApi,
  ): Promise<boolean> {
    if (!suggestionRun.value?.retryable || suggestionRun.value.stale) return false
    const command = pendingCommand({
      kind: 'retry',
      run_id: suggestionRun.value.run_id,
    })
    if (!command) {
      suggestionState.value = 'error'
      suggestionMessage.value = '上一次付费请求的结果尚未确认，请先核对同一任务。'
      return false
    }
    suggestionState.value = 'starting'
    try {
      const result = await api.retrySuggestionRun(
        suggestionRun.value.run_id,
        { request_token: command.requestToken },
      )
      clearPendingCommand(command.fingerprint)
      suggestionRun.value = result.run
      rememberRunId(result.run.run_id)
      suggestionJobId.value = result.job.id
      suggestionState.value = 'running'
      suggestionMessage.value = '正在继续未完成的项目，已有建议不会重复调用。'
      return true
    } catch (error) {
      suggestionState.value = 'error'
      if (isAmbiguousWriteError(error)) {
        suggestionMessage.value = '继续请求的结果未确认；再次点击会复用同一请求，不会重复处理已完成项。'
      } else {
        clearPendingCommand(command.fingerprint)
        suggestionMessage.value = '未完成项目暂时无法重试。'
      }
      return false
    }
  }

  return {
    revision,
    dimensions,
    proposals,
    pendingCount,
    historicalUnavailableCount,
    loadState,
    writeState,
    busyProposalId,
    message,
    proposalErrors,
    suggestionRun,
    suggestionState,
    suggestionMessage,
    suggestionJobId,
    hasCatalog,
    termsFor,
    load,
    review,
    suggestionFor,
    restoreSuggestions,
    startSuggestions,
    refreshSuggestions,
    cancelSuggestions,
    retrySuggestions,
    clearMessage,
  }
})

function readRememberedRunId(): string | null {
  try {
    const value = globalThis.localStorage?.getItem(
      TAXONOMY_SUGGESTION_RUN_STORAGE_KEY,
    )?.trim()
    return value && /^[0-9a-f]{32}$/i.test(value) ? value : null
  } catch {
    return null
  }
}

function rememberRunId(runId: string): void {
  if (!/^[0-9a-f]{32}$/i.test(runId)) return
  try {
    globalThis.localStorage?.setItem(
      TAXONOMY_SUGGESTION_RUN_STORAGE_KEY,
      runId,
    )
  } catch {
    // The visible run remains usable even when browser storage is unavailable.
  }
}

function forgetRunId(): void {
  try {
    globalThis.localStorage?.removeItem(
      TAXONOMY_SUGGESTION_RUN_STORAGE_KEY,
    )
  } catch {
    // Nothing else needs to be cleared.
  }
}

interface PendingCommand {
  fingerprint: string
  requestToken: string
}

function pendingCommand(
  command: Record<string, unknown>,
): PendingCommand | null {
  const fingerprint = JSON.stringify(command)
  try {
    const raw = globalThis.localStorage?.getItem(
      TAXONOMY_SUGGESTION_COMMAND_STORAGE_KEY,
    )
    if (raw) {
      const stored = JSON.parse(raw) as Partial<PendingCommand>
      if (
        stored.fingerprint === fingerprint
        && typeof stored.requestToken === 'string'
        && /^[0-9a-f]{32}$/i.test(stored.requestToken)
      ) {
        return {
          fingerprint,
          requestToken: stored.requestToken,
        }
      }
      return null
    }
    const created = {
      fingerprint,
      requestToken: globalThis.crypto.randomUUID().replace(/-/g, ''),
    }
    globalThis.localStorage?.setItem(
      TAXONOMY_SUGGESTION_COMMAND_STORAGE_KEY,
      JSON.stringify(created),
    )
    return created
  } catch {
    return {
      fingerprint,
      requestToken: globalThis.crypto.randomUUID().replace(/-/g, ''),
    }
  }
}

function clearPendingCommand(fingerprint: string): void {
  try {
    const raw = globalThis.localStorage?.getItem(
      TAXONOMY_SUGGESTION_COMMAND_STORAGE_KEY,
    )
    if (!raw) return
    const stored = JSON.parse(raw) as Partial<PendingCommand>
    if (stored.fingerprint === fingerprint) {
      globalThis.localStorage?.removeItem(
        TAXONOMY_SUGGESTION_COMMAND_STORAGE_KEY,
      )
    }
  } catch {
    // An unavailable cache does not affect the server-side idempotency key.
  }
}

function forgetPendingCommand(): void {
  try {
    globalThis.localStorage?.removeItem(
      TAXONOMY_SUGGESTION_COMMAND_STORAGE_KEY,
    )
  } catch {
    // The recovered server run is still authoritative.
  }
}
