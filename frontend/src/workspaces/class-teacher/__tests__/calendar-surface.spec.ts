import { createApp, nextTick, ref, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkNode, WorkSnapshot } from '../api/work'
import CalendarSurface from '../ordinary/CalendarSurface.vue'

const apps: App[] = []
async function mount(component: Component, props: Record<string, unknown>) {
  const host = document.createElement('div'); document.body.append(host)
  const app = createApp(component, props); app.mount(host); apps.push(app)
  await nextTick(); await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
  return host
}
function clickByText(host: HTMLElement, text: string) {
  const button = [...host.querySelectorAll('button')].find((item) => item.textContent?.includes(text))
  if (!button) throw new Error(`button not found: ${text}`)
  button.dispatchEvent(new MouseEvent('click', { bubbles: true })); return button
}
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML = ''; vi.restoreAllMocks() })

const TODAY = new Date().toISOString().slice(0, 10)
const MONTH = TODAY.slice(0, 7)
function dayIso(day: number) { return `${MONTH}-${String(day).padStart(2, '0')}` }
function isoOffset(days: number) {
  const date = new Date(`${TODAY}T12:00:00`); date.setDate(date.getDate() + days)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
let seq = 0
function node(partial: Partial<WorkNode>): WorkNode {
  seq += 1
  return {
    node_id: `node-${seq}`, kind: 'task', classification: 'ordinary', title: `工作 ${seq}`,
    details: null, status: 'pending', due_date: null, revision: 1,
    created_at: '2026-08-01', updated_at: '2026-08-01', projection_type: null, ...partial,
  }
}
function snapshotOf(nodes: WorkNode[]): WorkSnapshot {
  const active = nodes.filter((item) => !['completed', 'cancelled'].includes(item.status))
  return {
    as_of: TODAY, start_date: null, end_date: null, nodes, edges: [],
    today: active.filter((item) => item.due_date === TODAY),
    overdue: active.filter((item) => item.due_date !== null && item.due_date < TODAY),
    waiting: active.filter((item) => item.status === 'waiting'),
    review_due: [], summary: { today: 0, overdue: 0, waiting: 0, review_due: 0 },
    view: 'all', cursor: null, source_version: 'v',
  }
}
function fakeModule(nodes: WorkNode[]) {
  const snapshot = ref<WorkSnapshot | null>(null)
  return {
    snapshot,
    selected: ref(null),
    loading: ref(false),
    error: ref(''),
    load: vi.fn(async (view: string) => { if (view === 'all') snapshot.value = snapshotOf(nodes); return true }),
    inspect: vi.fn(async () => undefined),
    command: vi.fn(),
    clearSelection: vi.fn(),
  }
}

describe('calendar surface（月历 + 待办侧栏）', () => {
  it('loads the full work graph once and renders the current month', async () => {
    const module = fakeModule([node({ title: '当月事项', due_date: dayIso(15) })])
    const host = await mount(CalendarSurface, { module })

    expect(module.load).toHaveBeenCalledExactlyOnceWith('all')
    expect(host.querySelector('h2')?.textContent).toContain('月')
    expect(host.textContent).toContain('当月事项')
  })

  it('collapses a busy day behind "+N 更多" and lists everything in the popover', async () => {
    const busy = dayIso(15)
    const module = fakeModule([
      node({ title: '事项甲', due_date: busy }),
      node({ title: '事项乙', due_date: busy }),
      node({ title: '事项丙', due_date: busy }),
      node({ title: '已完成事项', due_date: busy, status: 'completed' }),
      node({ title: '已移出日历', due_date: busy, status: 'cancelled' }),
    ])
    const host = await mount(CalendarSurface, { module })

    // 每格最多平铺 2 条；已完成仍显示（变淡），已移出日历不显示
    expect(host.querySelectorAll('.cell .bar').length).toBe(2)
    expect(host.textContent).toContain('+2 更多')
    expect(host.textContent).not.toContain('已移出日历')

    clickByText(host, '+2 更多'); await nextTick()
    const pop = host.querySelector('.day-pop')
    expect(pop?.querySelectorAll('.bar').length).toBe(4)
    expect(pop?.textContent).toContain('事项丙')

    clickByText(host, '事项丙'); await nextTick()
    expect(module.inspect).toHaveBeenCalledWith(expect.objectContaining({ title: '事项丙' }))
    expect(host.querySelector('.day-pop')).toBeNull()
  })

  it('keeps overdue, today and waiting work in the sidebar instead of the grid', async () => {
    const module = fakeModule([
      node({ title: '逾期收缴', kind: 'collection', due_date: isoOffset(-3) }),
      node({ title: '今天巡视', kind: 'sop', due_date: TODAY, status: 'in_progress' }),
      node({ title: '等教务处回复', kind: 'waiting', due_date: isoOffset(2), status: 'waiting' }),
    ])
    const host = await mount(CalendarSurface, { module })

    const text = host.querySelector('.todo')?.textContent ?? ''
    expect(text).toContain('已逾期')
    expect(text).toContain('今天要办')
    expect(text).toContain('等待他人')
    expect(text).toContain('逾期收缴')
    expect(text).toContain('今天巡视')
    expect(text).toContain('等教务处回复')

    clickByText(host, '逾期收缴'); await nextTick()
    expect(module.inspect).toHaveBeenCalledWith(expect.objectContaining({ title: '逾期收缴' }))
  })

  it('switches months on the client without reloading the work graph', async () => {
    const module = fakeModule([node({ title: '当月事项', due_date: dayIso(15) })])
    const host = await mount(CalendarSurface, { module })
    const label = host.querySelector('h2')?.textContent

    clickByText(host, '下一月'); await nextTick()
    expect(host.querySelector('h2')?.textContent).not.toBe(label)
    expect(host.textContent).not.toContain('当月事项')
    expect(module.load).toHaveBeenCalledTimes(1)

    clickByText(host, '本月'); await nextTick()
    expect(host.querySelector('h2')?.textContent).toBe(label)
    expect(host.textContent).toContain('当月事项')
    expect(module.load).toHaveBeenCalledTimes(1)
  })
})
