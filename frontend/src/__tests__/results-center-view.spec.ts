import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchResultsCenter, type ResultsCenterItem, type ResultsCenterResponse } from '../api/results-center'
import { useSessionStore } from '../stores/session'
import { useResultsCenterStore } from '../stores/results-center'
import ResultsCenterView from '../views/ResultsCenterView.vue'

vi.mock('../api/results-center', async (original) => ({
  ...await original<typeof import('../api/results-center')>(),
  fetchResultsCenter: vi.fn(),
}))

function item(studentId: number, questionId: string, score: number | null, status: ResultsCenterItem['score_status'] = 'ai_ready'): ResultsCenterItem {
  return {
    review_item_id: `detail:${studentId}${questionId.slice(1)}`, question_id: questionId,
    score_awarded: score, max_score: 5, score_status: status,
    score_source: score === null ? 'none' : 'ai', confidence_score: 90,
    needs_review: status === 'ai_review', review_reason: null,
    result_id: studentId, detail_id: studentId * 10 + Number(questionId.slice(1)),
  }
}

function fixture(): ResultsCenterResponse {
  return {
    session_id: 7, session_name: '合成成绩验证',
    summary: {
      student_count: 4, complete_student_count: 3, average_sample_count: 3,
      average_score: 8, highest_score: 10, lowest_score: 6, max_score: 10,
      ungraded_item_count: 1, failed_item_count: 0, needs_review_item_count: 1,
      ai_ready_item_count: 6, teacher_final_item_count: 0,
    },
    questions: ['Q1', 'Q2'].map((questionId) => ({
      question_id: questionId, max_score: 5, total_count: 4,
      ungraded_count: questionId === 'Q2' ? 1 : 0, failed_count: 0,
      needs_review_count: questionId === 'Q1' ? 1 : 0,
      ai_ready_count: 3, teacher_final_count: 0, average_score: 3,
    })),
    students: [
      { student_id: 1, student_code: 'A01', student_name: '合成甲', class_name: '一班', current_score: 8, max_score: 10, ungraded_count: 0, failed_count: 0, needs_review_count: 0, status: 'complete', items: [item(1, 'Q1', 3), item(1, 'Q2', 5)] },
      { student_id: 2, student_code: 'A02', student_name: '合成乙', class_name: '一班', current_score: 6, max_score: 10, ungraded_count: 0, failed_count: 0, needs_review_count: 1, status: 'needs_review', items: [item(2, 'Q1', 2, 'ai_review'), item(2, 'Q2', 4)] },
      { student_id: 3, student_code: 'A03', student_name: '合成丙', class_name: '一班', current_score: 2, max_score: 10, ungraded_count: 1, failed_count: 0, needs_review_count: 0, status: 'incomplete', items: [item(3, 'Q1', 2), item(3, 'Q2', null, 'ungraded')] },
      { student_id: 4, student_code: 'B01', student_name: '合成丁', class_name: '二班', current_score: 10, max_score: 10, ungraded_count: 0, failed_count: 0, needs_review_count: 0, status: 'complete', items: [item(4, 'Q1', 5), item(4, 'Q2', 5)] },
    ],
  }
}

let app: App | null = null

async function mountView(tab = 'details') {
  localStorage.clear()
  vi.mocked(fetchResultsCenter).mockReset().mockResolvedValue(fixture())
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().$patch({ selectedSessionId: 7, loadState: 'ready', sessions: [{ id: 7, name: '合成成绩验证', status: 'grading', is_deleted: false, deleted_at: null, created_at: null, updated_at: null }] })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/results', component: ResultsCenterView },
    { path: '/grading', component: { render: () => h('div', '合成作答页面') } },
  ] })
  await router.push(`/results?tab=${tab}&session=7`)
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  app = createApp({ render: () => h('main', { id: 'main-workspace' }, [h(RouterView)]) })
  app.use(pinia).use(router).mount(host)
  await vi.waitFor(() => expect(host.querySelector('.results-conclusion')).not.toBeNull())
  return { host, router }
}

function input(element: HTMLInputElement | HTMLSelectElement, value: string): void {
  element.value = value
  element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
}

afterEach(() => {
  app?.unmount()
  app = null
  document.body.innerHTML = ''
})

describe('results center class filtering and return position', () => {
  it('removes the per-student overview while retaining the charts', async () => {
    const { host } = await mountView('overview')
    expect(host.textContent).not.toContain('逐人查看')
    expect(host.querySelector('#score-distribution-title')).not.toBeNull()
    expect(host.querySelector('#question-insights-title')).not.toBeNull()
  })

  it('uses the visible class, status and search scope without averaging incomplete scores as zero', async () => {
    const { host } = await mountView()
    input(host.querySelector<HTMLSelectElement>('[aria-label="成绩明细班级"]')!, '一班')
    await nextTick()
    const cards = () => host.querySelectorAll('.results-conclusion__strip button')
    expect(cards()[0]!.textContent).toContain('2 / 3 人')
    expect(cards()[1]!.textContent?.replace(/\s+/g, ' ')).toContain('7 / 10')
    expect(cards()[1]!.textContent).toContain('计入 2 人')
    expect(host.querySelectorAll('.results-matrix tbody tr')).toHaveLength(3)
    expect(host.querySelectorAll('.results-matrix thead th')[3]!.textContent?.replace(/\s+/g, ' ')).toContain('4.5 / 5')
    const incomplete = [...host.querySelectorAll<HTMLButtonElement>('.results-filter-bar button')].find((button) => button.textContent?.includes('未评分'))!
    incomplete.click()
    await vi.waitFor(() => expect(cards()[0]!.textContent).toContain('0 / 1 人'))
    expect(cards()[1]!.textContent?.replace(/\s+/g, ' ')).toContain('— / 10')
    input(host.querySelector<HTMLInputElement>('input[type="search"]')!, '没有这个学生')
    await nextTick()
    expect(cards()[0]!.textContent).toContain('0 / 0 人')
    expect(host.textContent).toContain('没有符合当前筛选条件的学生')
  })

  it('restores filters, sorting and both scroll containers after viewing an answer', async () => {
    const { host, router } = await mountView()
    input(host.querySelector<HTMLSelectElement>('[aria-label="成绩明细班级"]')!, '一班')
    input(host.querySelector<HTMLInputElement>('input[type="search"]')!, '合成')
    const complete = [...host.querySelectorAll<HTMLButtonElement>('.results-filter-bar button')].find((button) => button.textContent?.includes('成绩完整'))!
    complete.click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.filter).toBe('complete'))
    host.querySelector<HTMLButtonElement>('.results-matrix__total .results-matrix__sort')!.click()
    await nextTick()
    const main = host.querySelector('main')!
    const matrix = host.querySelector('.results-matrix-wrap')!
    main.scrollTop = 170
    matrix.scrollTop = 230
    matrix.scrollLeft = 390
    host.querySelector<HTMLButtonElement>('.results-matrix__score button')!.click()
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/grading'))
    expect(useResultsCenterStore().viewState).toMatchObject({ selectedClass: '一班', scrollTop: 170, matrixScrollTop: 230, matrixScrollLeft: 390 })
    router.back()
    await vi.waitFor(() => expect(host.querySelector('.results-matrix-wrap')?.scrollLeft).toBe(390))
    expect(host.querySelector('main')!.scrollTop).toBe(170)
    expect(host.querySelector('.results-matrix-wrap')!.scrollTop).toBe(230)
    expect(host.querySelector<HTMLSelectElement>('[aria-label="成绩明细班级"]')!.value).toBe('一班')
    expect(host.querySelector<HTMLInputElement>('input[type="search"]')!.value).toBe('合成')
    expect(router.currentRoute.value.query.filter).toBe('complete')
    expect(host.querySelector('th.results-matrix__total')!.getAttribute('aria-sort')).toBe('ascending')
    expect(host.querySelector('.results-matrix tbody tr')!.textContent).toContain('合成乙')
  })
})
