import { createApp, defineComponent, nextTick, ref, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import type { GraphQueryInput } from '../api/graph'
import EvidenceScopeFilters from '../components/evidence/EvidenceScopeFilters.vue'
import { createAppRouter } from '../router'

const sessions = [{
  id: 7,
  name: '匿名阶段测验',
  status: 'completed',
  is_deleted: false,
  deleted_at: null,
  created_at: null,
  updated_at: null,
}]

const students = [
  { id: 12, student_code: 'S012', name: '匿名学生甲', class_name: '七年级一班', created_at: null },
  { id: 22, student_code: 'S022', name: '匿名学生乙', class_name: '七年级一班', created_at: null },
  { id: 33, student_code: 'S033', name: '匿名学生丙', class_name: '七年级二班', created_at: null },
]

const scoreProfiles = {
  12: { score_rate: 0.55, score_rate_source: 'current_exam' },
  22: { score_rate: 0.9, score_rate_source: 'current_exam' },
  33: { score_rate: 0.3, score_rate_source: 'current_exam' },
}

const initialQuery: GraphQueryInput = {
  scope: {
    mode: 'all',
    include_student_ids: [],
    exclude_student_ids: [],
    use_historical_fallback: true,
  },
  exam_scope: { mode: 'current', session_ids: [7] },
}

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountFilters(compactRoster = false, showScoreFloor = false, query = initialQuery) {
  const applies: GraphQueryInput[] = []
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createAppRouter(createMemoryHistory())
  await router.push('/')
  await router.isReady()
  const Wrapper = defineComponent({
    components: { EvidenceScopeFilters },
    setup() {
      const model = ref<GraphQueryInput>(query)
      const onApply = (next: GraphQueryInput): void => {
        applies.push(next)
        model.value = next
      }
      return { model, onApply, sessions, students, scoreProfiles, compactRoster, showScoreFloor }
    },
    template: `
      <EvidenceScopeFilters
        :model-value="model"
        :students="students"
        :sessions="sessions"
        :current-session-id="7"
        :applying="false"
        :score-profiles="scoreProfiles"
        :compact-roster="compactRoster"
        :show-score-floor="showScoreFloor"
        @apply="onApply"
      />`,
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(Wrapper)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await settle()
  return { host, applies }
}

function clickButton(host: HTMLElement, label: string): void {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find((item) => item.textContent?.trim() === label)
  expect(button, `button ${label}`).toBeTruthy()
  button!.click()
}

function setScoreMin(host: HTMLElement, value: string): HTMLInputElement {
  const input = host.querySelector<HTMLInputElement>('input[aria-label="最低得分率"]')!
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  return input
}

function clickApply(host: HTMLElement): void {
  host.querySelector<HTMLButtonElement>('[data-testid="apply-evidence-scope"]')!.click()
}

function setSearch(host: HTMLElement, value: string): void {
  const input = host.querySelector<HTMLInputElement>('input[aria-label="搜索学生"]')!
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('evidence scope filters queue', () => {
  it('exposes the chapter score floor without expanding the roster and preserves it for explicit members', async () => {
    const { host, applies } = await mountFilters(true)
    expect(host.querySelector('.evidence-scope__roster')).toBeNull()
    const floor = setScoreMin(host, '50')
    floor.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    expect(applies[applies.length - 1]?.scope.score_rate_min).toBe(.5)
    expect(host.textContent).toContain('无可用成绩的学生不进入推荐')
    clickButton(host, '更多筛选')
    await settle()
    const add = host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生甲"]')!
    add.click()
    await settle()
    clickApply(host)
    await settle()
    expect(applies[applies.length - 1]?.scope.mode).toBe('selected')
    expect(applies[applies.length - 1]?.scope.student_ids).toEqual(['12'])
    expect(applies[applies.length - 1]?.scope.score_rate_min).toBe(.5)
  })
  it('applies and restores the student score floor even for a saved queue, without changing the roster layout', async () => {
    const query: GraphQueryInput = { ...initialQuery, scope: { ...initialQuery.scope,
      mode: 'selected', student_ids: ['12', '22'], score_rate_min: .2 } }
    const { host, applies } = await mountFilters(false, true, query)
    expect(host.querySelector<HTMLInputElement>('input[aria-label="最低得分率"]')!.value).toBe('20')
    expect(host.querySelector('.evidence-scope__queue')!.textContent).toContain('队列 2 人')
    setScoreMin(host, '80')
    clickApply(host)
    await settle()
    expect(applies[applies.length - 1]?.scope).toMatchObject({ mode: 'selected', student_ids: ['12', '22'], score_rate_min: .8 })
    clickButton(host, '更多筛选')
    await settle()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(1)
    expect(host.querySelector('.evidence-scope__card-name b')?.textContent).toBe('匿名学生乙')
    setScoreMin(host, '101')
    clickApply(host)
    await settle()
    expect(applies).toHaveLength(1)
    expect(host.textContent).toContain('得分率请输入 0–100 之间的数字')
    setScoreMin(host, '')
    clickApply(host)
    await settle()
    expect(applies[applies.length - 1]?.scope.score_rate_min).toBeUndefined()
    expect(applies[applies.length - 1]?.scope.student_ids).toEqual(['12', '22'])
  })
  it('always renders the queue row with an empty placeholder until students are queued', async () => {
    const { host } = await mountFilters()
    // 初始空队列：行常显占位文案，chips 不存在。
    const queue = host.querySelector<HTMLElement>('.evidence-scope__queue')!
    expect(queue).toBeTruthy()
    expect(queue.querySelector('.evidence-scope__queue-empty')!.textContent).toContain('队列为空')
    expect(queue.querySelectorAll('li')).toHaveLength(0)

    clickButton(host, '更多筛选')
    await settle()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(0)
    expect(host.textContent).toContain('输入姓名、学号，或设定班级、得分率后显示匹配学生')
    setSearch(host, '匿名')
    await nextTick()
    host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生甲"]')!.click()
    await nextTick()
    // 勾选后占位让位给 chips，行本身不增删，下方内容不位移。
    expect(host.querySelector('.evidence-scope__queue-empty')).toBeNull()
    expect(host.querySelector('.evidence-scope__queue')!.textContent).toContain('队列 1 人')

    clickButton(host, '清空')
    await nextTick()
    expect(host.querySelector('.evidence-scope__queue-empty')!.textContent).toContain('队列为空')
  })

  it('keeps checked cards in place and shows the queue as removable chips', async () => {
    const { host, applies } = await mountFilters()
    // 默认状态（无班级、未展开更多筛选）不显示卡片区。
    expect(host.querySelector('.evidence-scope__roster')).toBeNull()

    clickButton(host, '更多筛选')
    await settle()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(0)
    setSearch(host, '匿名')
    await nextTick()
    const before = [...host.querySelectorAll('.evidence-scope__card-name b')]
      .map((node) => node.textContent)
    expect(before).toHaveLength(3)

    const min = setScoreMin(host, '60')
    await nextTick()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(1)
    host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生乙"]')!.click()
    await nextTick()
    // 勾选后卡片原地保留，只切换勾选态，不改变卡片列表。
    const after = [...host.querySelectorAll('.evidence-scope__card-name b')]
      .map((node) => node.textContent)
    expect(after).toEqual(['匿名学生乙'])
    expect(host.querySelector('.evidence-scope__card.is-checked .evidence-scope__card-name b')?.textContent)
      .toBe('匿名学生乙')

    // 队列以快捷栏下方的紧凑 chips 常显。
    const queue = host.querySelector<HTMLElement>('.evidence-scope__queue')!
    expect(queue.textContent).toContain('队列 1 人')
    expect(queue.textContent).toContain('匿名学生乙')

    clickApply(host)
    await settle()
    const selected = applies[applies.length - 1]!
    expect(selected.scope.mode).toBe('selected')
    expect(selected.scope.student_ids).toEqual(['22'])
    expect(selected.scope).not.toHaveProperty('score_rate_min')
    expect(selected.scope).not.toHaveProperty('score_rate_max')
    expect(selected.scope).not.toHaveProperty('class_ids')

    // selected 回包只回同步队列，用户输入的区间保持原样。
    expect(min.value).toBe('60')

    // 换区间后筛选结果为空，队列成员仍固定在 chips 行。
    setScoreMin(host, '95')
    await nextTick()
    expect(host.textContent).toContain('当前条件下没有匹配的学生')
    expect(host.querySelector('.evidence-scope__queue')!.textContent).toContain('匿名学生乙')
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(0)

    // 区间放回后可继续勾选累积。
    setScoreMin(host, '40')
    await nextTick()
    host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生甲"]')!.click()
    await nextTick()
    clickApply(host)
    await settle()
    expect(applies[applies.length - 1]!.scope.student_ids).toEqual(['22', '12'])
    expect(host.querySelector('.evidence-scope__queue')!.textContent).toContain('队列 2 人')
    expect(host.querySelectorAll('.evidence-scope__queue li')).toHaveLength(2)
  })

  it('merges all filtered results into the queue and clears it from the chips row', async () => {
    const { host, applies } = await mountFilters()
    clickButton(host, '更多筛选')
    await settle()
    setSearch(host, '匿名')
    await nextTick()

    host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生乙"]')!.click()
    await nextTick()
    clickButton(host, '全选筛选结果')
    await nextTick()
    // 去重并入：已在队列的匿名学生乙不重复。
    expect(host.querySelector('.evidence-scope__queue')!.textContent).toContain('队列 3 人')
    expect(host.querySelectorAll('.evidence-scope__queue li')).toHaveLength(3)

    // 已在队列的卡片显示勾选态，取消勾选卡片仍在原位。
    const card = host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生甲"]')!
    expect(card.checked).toBe(true)
    card.click()
    await nextTick()
    expect(host.querySelector('.evidence-scope__queue')!.textContent).toContain('队列 2 人')
    expect(host.querySelector('input[aria-label="选择匿名学生甲"]')).toBeTruthy()

    // chip 上的 × 移除单个成员。
    host.querySelector<HTMLButtonElement>('button[aria-label="从队列移除匿名学生丙"]')!.click()
    await nextTick()
    expect(host.querySelector('.evidence-scope__queue')!.textContent).toContain('队列 1 人')

    clickButton(host, '清空')
    await nextTick()
    // 队列行常显：清空后回到占位文案，而不是整行消失。
    expect(host.querySelector('.evidence-scope__queue-empty')!.textContent).toContain('队列为空')
    expect(host.textContent).toContain('未指定学生')

    clickApply(host)
    await settle()
    expect(applies[applies.length - 1]!.scope.mode).toBe('all')
  })

  it('collapses the card area unless a class is chosen or more filters are open', async () => {
    const { host, applies } = await mountFilters()
    expect(host.querySelector('.evidence-scope__roster')).toBeNull()

    clickButton(host, '更多筛选')
    await settle()
    setSearch(host, '匿名')
    await nextTick()
    expect(host.querySelector('.evidence-scope__roster')).not.toBeNull()

    host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生丙"]')!.click()
    await nextTick()

    clickButton(host, '收起筛选')
    await settle()
    // 卡片区折叠，队列 chips 独立常显，统计语义不变。
    expect(host.querySelector('.evidence-scope__roster')).toBeNull()
    expect(host.querySelector('.evidence-scope__queue')!.textContent).toContain('队列 1 人')

    clickApply(host)
    await settle()
    const scoped = applies[applies.length - 1]!
    expect(scoped.scope.mode).toBe('selected')
    expect(scoped.scope.student_ids).toEqual(['33'])
  })

  it('keeps class and score-range semantics while the queue is empty', async () => {
    const { host, applies } = await mountFilters()
    ;[...host.querySelectorAll<HTMLLabelElement>('.evidence-scope__classes label')]
      .find((label) => label.textContent?.includes('七年级一班'))!
      .querySelector<HTMLInputElement>('input')!.click()
    await nextTick()
    clickButton(host, '更多筛选')
    await settle()
    setSearch(host, '匿名')
    await nextTick()
    setScoreMin(host, '50')
    await nextTick()
    clickApply(host)
    await settle()

    const scoped = applies[applies.length - 1]!
    expect(scoped.scope.mode).toBe('class')
    expect(scoped.scope.class_ids).toEqual(['七年级一班'])
    expect(scoped.scope.score_rate_min).toBeCloseTo(0.5)
    expect(scoped.scope).not.toHaveProperty('score_rate_max')
  })

  it('lists students after a score range or class is set without requiring a name search', async () => {
    const { host } = await mountFilters()
    clickButton(host, '更多筛选')
    await settle()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(0)
    expect(host.textContent).toContain('输入姓名、学号，或设定班级、得分率后显示匹配学生')

    setScoreMin(host, '0')
    const maximum = host.querySelector<HTMLInputElement>('input[aria-label="最高得分率"]')!
    maximum.value = '100'
    maximum.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(3)

    setScoreMin(host, '80')
    await nextTick()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(1)
    expect(host.querySelector('.evidence-scope__card-name b')?.textContent).toBe('匿名学生乙')
  })
})
