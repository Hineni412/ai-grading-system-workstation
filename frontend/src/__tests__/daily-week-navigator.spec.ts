import { createApp, h, nextTick, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { dailyApi } from '../api/daily'
import WeekNavigator from '../views/daily/timetable/WeekNavigator.vue'

const mountedApps: App[] = []

function mountNavigator(
  props: { weekStart: string; weekNo: number | null },
  onAnchor: (weekNo: number) => void,
) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({
    setup: () => () => h(WeekNavigator, { ...props, onAnchor }),
  })
  app.mount(host)
  mountedApps.push(app)
  return { host }
}

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json', 'x-request-id': 'rid' },
  })
}

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('daily timetable WeekNavigator', () => {
  it('submits a typed week number as a numeric anchor and issues the PUT week-anchor', async () => {
    const anchored: number[] = []
    const { host } = mountNavigator(
      { weekStart: '2026-08-31', weekNo: null },
      (weekNo) => anchored.push(weekNo),
    )

    const input = host.querySelector<HTMLInputElement>('.week-navigator__anchor input')
    expect(input).not.toBeNull()
    input!.value = '3'
    input!.dispatchEvent(new Event('input'))
    await nextTick()

    const submit = [...host.querySelectorAll<HTMLButtonElement>('.week-navigator__anchor button')]
      .find((button) => button.textContent === '确定')
    expect(submit).not.toBeUndefined()
    submit!.click()
    await nextTick()

    // 之前在这里崩溃：number 输入框的 v-model 可能给出 number，.trim() 抛 TypeError。
    expect(anchored).toEqual([3])
    expect(typeof anchored[0]).toBe('number')

    const fetchMock = vi.fn(async () =>
      response({ anchor_monday: '2026-08-31', week_no: 3, updated_at: '2026-09-01T09:00:00+00:00' }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await dailyApi.setWeekAnchor('2026-08-31', anchored[0]!)

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/timetable/week-anchor')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toEqual({ date: '2026-08-31', week_no: 3 })
  })

  it('rejects a non-numeric week number without emitting', async () => {
    const anchored: number[] = []
    const { host } = mountNavigator(
      { weekStart: '2026-08-31', weekNo: null },
      (weekNo) => anchored.push(weekNo),
    )

    const input = host.querySelector<HTMLInputElement>('.week-navigator__anchor input')!
    input.value = 'abc'
    input.dispatchEvent(new Event('input'))
    await nextTick()

    const submit = [...host.querySelectorAll<HTMLButtonElement>('.week-navigator__anchor button')]
      .find((button) => button.textContent === '确定')!
    submit.click()
    await nextTick()

    expect(anchored).toEqual([])
    expect(host.querySelector('.week-navigator__error')?.textContent).toContain('1 到 40')
  })
})
