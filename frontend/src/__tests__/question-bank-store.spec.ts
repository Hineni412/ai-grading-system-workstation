import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { questionBankApi, type ChapterExamProfile, type QuestionRepairPreview, type QuestionBankDetail, type QuestionBankFilters, type QuestionBankListResponse, type QuestionSkillIndex } from '../api/question-bank';
import { ApiError } from '../api/errors';

function page(
  keyword: string,
  id: number,
): QuestionBankListResponse {
  return {
    items: [{
      id,
      revision: String(id).padStart(64, 'a').slice(-64),
      paper_id: 1,
      question_number: String(id),
      question_type: '解答题',
      question_text: `${keyword}题干`,
      answer_text: null,
      difficulty: '5',
      typicality: null,
      reason: null,
      needs_review: false,
      criteria_needs_review: false,
      has_images: false,
      needs_image_review: false,
      created_at: '2026-07-18T08:00:00Z',
      updated_at: '2026-07-18T08:00:00Z',
      paper_title: '匿名试卷',
      year: '2025',
      province: null,
      city: null,
      district: null,
      exam_type: '期末',
      grade: '九年级',
      semester: null,
      textbook_version: null,
      tags: [],
      asset_urls: [],
    }],
    total: 1,
    page: 1,
    page_size: 20,
    total_pages: 1,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

function detail(id: number, text: string): QuestionBankDetail {
  return {
    ...page(text, id).items[0]!,
    page_range: null,
    assets: [],
    rich_content: {
      available: true,
      question_block_count: 1,
      answer_block_count: 0,
      question_blocks: [{
        text,
        asset_indexes: [],
        asset_urls: [],
      }],
      answer_blocks: [],
    },
    previews: [],
  }
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.restoreAllMocks())

describe('question bank store', () => {
  it('never lets an older filter response replace the latest applied range', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const old = deferred<QuestionBankListResponse>()
    let oldSignal: AbortSignal | undefined
    const oldFilters: QuestionBankFilters = { keyword: '旧范围' }
    const newFilters: QuestionBankFilters = { keyword: '新范围' }

    const oldLoad = store.loadQuestions(oldFilters, (_filters, signal) => {
      oldSignal = signal
      return old.promise
    })
    await store.loadQuestions(newFilters, async () => page('新范围', 2))
    old.resolve(page('旧范围', 1))
    await oldLoad

    expect(oldSignal?.aborted).toBe(true)
    expect(store.appliedFilters.keyword).toBe('新范围')
    expect(store.questions.map((item) => item.id)).toEqual([2])
    expect(store.listState).toBe('ready')
  })

  it('shares a pending filter read and restores its previous result before revalidation', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const pending = deferred<QuestionBankListResponse>()
    const load = vi.spyOn(questionBankApi, 'listQuestions').mockReturnValueOnce(pending.promise)
    const first = store.loadQuestions({ keyword: '范围甲', skillKeys: ['sk_2', 'sk_1'] })
    const oldOwner = store.questionReadVersion
    const same = store.loadQuestions({ skillKeys: ['sk_1', 'sk_2'], keyword: '范围甲' })
    store.cancelQuestionRead(oldOwner)
    expect(load.mock.calls[0]?.[1]?.aborted).toBe(false)
    expect(load).toHaveBeenCalledTimes(1)
    pending.resolve(page('范围甲', 1))
    await Promise.all([first, same])
    load.mockResolvedValueOnce(page('范围乙', 2))
    await store.loadQuestions({ keyword: '范围乙' })
    expect(store.questions.map(item => item.id)).toEqual([2])

    const refresh = deferred<QuestionBankListResponse>()
    load.mockReturnValueOnce(refresh.promise)
    const refreshing = store.loadQuestions({ keyword: '范围甲', skillKeys: ['sk_1', 'sk_2'] })
    expect(store.listState).toBe('ready')
    expect(store.questions.map(item => item.id)).toEqual([1])
    refresh.resolve(page('范围甲已更新', 3))
    await refreshing
    expect(store.questions.map(item => item.id)).toEqual([3])
  })

  it('does not show a stale filter snapshot after a question write', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const load = vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValueOnce(page('待处理', 1))
    await store.loadQuestions({ keyword: '待处理' })
    store.applyTagWrite(1, 'b'.repeat(64), [])
    store.questions = []
    const pending = deferred<QuestionBankListResponse>()
    load.mockReturnValueOnce(pending.promise)
    const refreshing = store.loadQuestions({ keyword: '待处理' })
    expect(store.listState).toBe('loading')
    expect(store.questions).toEqual([])
    pending.resolve(page('已更新', 2))
    await refreshing
    expect(store.questions.map(item => item.id)).toEqual([2])
  })

  it('does not restore a cancelled list after the browser clears its selection', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const pending = deferred<QuestionBankListResponse>()
    let signal: AbortSignal | undefined
    const loading = store.loadQuestions({ keyword: '离开的范围' }, (_filters, current) => {
      signal = current
      return pending.promise
    })
    store.cancelQuestionRead()
    store.questions = []
    store.total = 0
    store.listState = 'empty'
    pending.resolve(page('离开的范围', 1))
    await loading
    expect(signal?.aborted).toBe(true)
    expect(store.questions).toEqual([])
    expect(store.listState).toBe('empty')
  })

  it('keeps a shared skill-index read alive when one view leaves', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const index: QuestionSkillIndex = {
      curriculum_volume_id: 'TEST-volume', graph_release_id: null, model_calls: 0,
      question_count: 2, unlinked: { no_usable_evidence: 0, no_skill_link: 0 }, chapters: [],
    }
    const pending = deferred<QuestionSkillIndex>()
    const load = vi.spyOn(questionBankApi, 'skillIndex').mockReturnValue(pending.promise)
    const controller = new AbortController()
    const left = store.loadSkillIndex(index.curriculum_volume_id, controller.signal)
    const remaining = store.loadSkillIndex(index.curriculum_volume_id)
    controller.abort()
    await expect(left).rejects.toMatchObject({ name: 'AbortError' })
    pending.resolve(index)
    await expect(remaining).resolves.toEqual(index)
    expect(load).toHaveBeenCalledTimes(1)
    expect(store.skillIndexes.get(index.curriculum_volume_id)).toEqual(index)
  })

  it.each([
    ['volume', 'old-first'], ['volume', 'new-first'],
    ['all-volumes', 'old-first'], ['all-volumes', 'new-first'],
  ])('rejects invalidated repair previews for %s when %s resolves', async (scope, order) => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const volumeId = 'TEST-volume'
    const key = `${volumeId}:all`
    const before: QuestionRepairPreview = {
      curriculum_volume_id: volumeId, kind: 'all', fingerprint: 'a'.repeat(64),
      scanned_count: 1, question_count: 1, repairable_count: 1, model_calls: 0, skills_required: true,
      counts: { tags: 1, evidence: 0, criteria: 0, skills: 0, types: 0, knowledge_points: 0 },
      items: [{ id: 1, question_number: '1', paper_title: 'TEST-补齐前', missing: ['tags'], blocked_reason: '', revision: 'b'.repeat(64) }],
    }
    const after: QuestionRepairPreview = {
      ...before, fingerprint: 'c'.repeat(64), question_count: 0, repairable_count: 0,
      counts: { ...before.counts, tags: 0 }, items: [],
    }
    const old = deferred<QuestionRepairPreview>()
    const fresh = deferred<QuestionRepairPreview>()
    const api = vi.spyOn(questionBankApi, 'repairPreview')
      .mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
    store.repairPreviews.set(key, before)
    const oldRead = store.loadRepairPreview(volumeId, 'all')
    store.invalidateRepairPreviews(scope === 'volume' ? volumeId : undefined)
    expect(store.repairPreviews.has(key)).toBe(false)
    expect(store.repairPreviewLoading.has(key)).toBe(false)
    const currentRead = store.loadRepairPreview(volumeId, 'all')
    expect(api).toHaveBeenCalledTimes(2)
    if (order === 'new-first') {
      fresh.resolve(after)
      await currentRead
    }
    old.resolve(before)
    await expect(oldRead).rejects.toMatchObject({ name: 'AbortError' })
    expect(store.repairPreviewLoading.has(key)).toBe(order === 'old-first')
    expect(store.repairPreviews.get(key)).toEqual(order === 'old-first' ? undefined : after)
    if (order === 'old-first') {
      fresh.resolve(after)
      await currentRead
    }
    expect(store.repairPreviews.get(key)).toEqual(after)
    expect(store.repairPreviewLoading.has(key)).toBe(false)
  })

  it('rejects obsolete index and chapter results after a known question-bank write', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const index: QuestionSkillIndex = {
      curriculum_volume_id: 'TEST-volume', graph_release_id: null, model_calls: 0,
      question_count: 2, unlinked: { no_usable_evidence: 0, no_skill_link: 0 }, chapters: [],
    }
    const profile: ChapterExamProfile = {
      curriculum_volume_id: index.curriculum_volume_id, graph_release_id: null, model_calls: 0,
      counted_question_count: 2, stages: [], merged_groups: [], chapters: [],
    }
    const indexBeforeWrite = deferred<QuestionSkillIndex>()
    const profileBeforeWrite = deferred<ChapterExamProfile>()
    const nextIndex = { ...index, question_count: 3 }
    const nextProfile = { ...profile, counted_question_count: 3 }
    vi.spyOn(questionBankApi, 'skillIndex')
      .mockReturnValueOnce(indexBeforeWrite.promise).mockResolvedValueOnce(nextIndex)
    vi.spyOn(questionBankApi, 'chapterExamProfile')
      .mockReturnValueOnce(profileBeforeWrite.promise).mockResolvedValueOnce(nextProfile)
    const obsolete = Promise.allSettled([
      store.loadSkillIndex(index.curriculum_volume_id),
      store.loadChapterExamProfile(index.curriculum_volume_id),
    ])
    store.invalidateReadSnapshots()
    await Promise.all([
      store.loadSkillIndex(index.curriculum_volume_id),
      store.loadChapterExamProfile(index.curriculum_volume_id),
    ])
    indexBeforeWrite.resolve(index)
    profileBeforeWrite.resolve(profile)
    expect(await obsolete).toMatchObject([
      { status: 'rejected', reason: { name: 'AbortError' } },
      { status: 'rejected', reason: { name: 'AbortError' } },
    ])
    expect(store.skillIndexes.get(index.curriculum_volume_id)).toEqual(nextIndex)
    expect(store.chapterExamProfiles.get(index.curriculum_volume_id)).toEqual(nextProfile)
  })

  it('keeps the teacher tag draft when a revision conflict rejects the save', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    await store.selectQuestion(1, async () => detail(1, '待标注题目'))
    store.replaceTagDraft([{
      tag_type: 'knowledge_point',
      tag_value: '一次函数',
      confidence: null,
    }])

    const saved = await store.saveTags({
      async replaceTags() {
        throw new ApiError({
          kind: 'conflict',
          status: 409,
          code: 'question_write_conflict',
          message: 'conflict',
          details: {},
          requestId: 'req-conflict',
          retryable: false,
        })
      },
      async softDelete() {
        throw new Error('unused')
      },
      async restore() {
        throw new Error('unused')
      },
    })

    expect(saved).toBe(false)
    expect(store.writeState).toBe('conflict')
    expect(store.tagDraft[0]?.tag_value).toBe('一次函数')
    expect(store.detail?.tags).toEqual([])
  })

  it('shows the served last result while refreshing and swaps in the fresh one', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const volume = 'TEST-volume'
    const staleIndex: QuestionSkillIndex = {
      curriculum_volume_id: volume, graph_release_id: null, model_calls: 0,
      question_count: 2, unlinked: { no_usable_evidence: 0, no_skill_link: 0 }, chapters: [],
    }
    const freshIndex = { ...staleIndex, question_count: 3 }
    let calls = 0
    vi.spyOn(questionBankApi, 'skillIndex').mockImplementation(
      (_id, _signal, onRefreshing) => {
        calls += 1
        onRefreshing?.(calls === 1)
        return Promise.resolve(calls === 1 ? staleIndex : freshIndex)
      },
    )
    const load = store.loadSkillIndex(volume)
    await vi.waitFor(() => expect(store.skillIndexes.get(volume)).toEqual(staleIndex))
    expect(store.refreshingReads.size).toBe(1)
    await vi.waitFor(() => expect(store.refreshingReads.size).toBe(0), { timeout: 6000 })
    await load
    expect(store.skillIndexes.get(volume)).toEqual(freshIndex)
    expect(calls).toBe(2)
  })

  it('stops polling after the attempt budget and reports the retry-later state', async () => {
    const { requestWithRefresh } = await import('../api/display-refresh')
    const reports: Array<[boolean, boolean]> = []
    let calls = 0
    await requestWithRefresh(
      (onRefreshing) => { onRefreshing(true); calls += 1; return Promise.resolve(calls) },
      { report: (refreshing, exhausted) => reports.push([refreshing, exhausted]) },
      () => Promise.resolve(),
    )
    expect(calls).toBe(60)
    expect(reports[reports.length - 1]).toEqual([true, true])
  })

})
