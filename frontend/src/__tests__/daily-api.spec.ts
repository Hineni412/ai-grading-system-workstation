import { afterEach, describe, expect, it, vi } from 'vitest'

import { dailyApi } from '../api/daily'
import { buildDragOps } from '../views/daily/timetable/timetableModel'

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json', 'x-request-id': 'rid' },
  })
}

function weekPayload() {
  return {
    week_start: '2026-08-31',
    week_no: 3,
    days: [
      { day_of_week: 1, date: '2026-08-31' },
      { day_of_week: 2, date: '2026-09-01' },
      { day_of_week: 3, date: '2026-09-02' },
      { day_of_week: 4, date: '2026-09-03' },
      { day_of_week: 5, date: '2026-09-04' },
    ],
    slots: [
      {
        slot_key: 'lesson:1',
        kind: 'lesson',
        custom_slot_id: null,
        label: '第1节',
        start_text: null,
        end_text: null,
        position: null,
        lesson_no: 1,
      },
    ],
    cells: [
      {
        day_of_week: 1,
        slot_key: 'lesson:1',
        course_text: '数学',
        class_label: '七（1）班',
        source: 'regular',
        override_id: null,
        note: null,
      },
    ],
    today: { date: '2026-09-01', day_of_week: 2, in_week: true },
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('daily API contract', () => {
  it('decodes the merged timetable week', async () => {
    const fetchMock = vi.fn(async () => response(weekPayload()))
    vi.stubGlobal('fetch', fetchMock)

    const week = await dailyApi.timetable('2026-08-31')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/class-teacher/daily/timetable?week_start=2026-08-31',
      expect.objectContaining({ method: 'GET' }),
    )
    expect(week.week_no).toBe(3)
    expect(week.days).toHaveLength(5)
    expect(week.cells[0]).toMatchObject({ course_text: '数学', source: 'regular' })
    expect(week.today).toMatchObject({ in_week: true, day_of_week: 2 })
  })

  it('decodes an unset week anchor as null', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(null)))
    await expect(dailyApi.weekAnchor()).resolves.toBeNull()
  })

  it('submits drag-generated overrides as one atomic batch with the trusted header', async () => {
    const fetchMock = vi.fn(async () =>
      response({
        week_start: '2026-08-31',
        overrides: [
          {
            override_id: 'ov-1',
            week_start: '2026-08-31',
            day_of_week: 4,
            slot_key: 'lesson:5',
            action: 'set',
            course_text: '数学',
            class_label: '七（1）班',
            note: null,
            created_at: '2026-09-01T08:00:00+00:00',
          },
          {
            override_id: 'ov-2',
            week_start: '2026-08-31',
            day_of_week: 2,
            slot_key: 'lesson:3',
            action: 'set',
            course_text: '体育',
            class_label: '',
            note: null,
            created_at: '2026-09-01T08:00:00+00:00',
          },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const cells = [
      {
        day_of_week: 2,
        slot_key: 'lesson:3',
        course_text: '数学',
        class_label: '七（1）班',
        source: 'regular' as const,
        override_id: null,
        note: null,
      },
      {
        day_of_week: 4,
        slot_key: 'lesson:5',
        course_text: '体育',
        class_label: '',
        source: 'regular' as const,
        override_id: null,
        note: null,
      },
    ]
    const ops = buildDragOps(
      { day_of_week: 2, slot_key: 'lesson:3' },
      { day_of_week: 4, slot_key: 'lesson:5' },
      cells,
    )
    const applied = await dailyApi.applyOverrides('2026-08-31', ops)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/timetable/overrides')
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({ 'x-class-teacher-client': 'class-teacher-browser-v1' })
    expect(JSON.parse(String(init.body))).toEqual({
      week_start: '2026-08-31',
      ops: [
        {
          day_of_week: 4,
          slot_key: 'lesson:5',
          action: 'set',
          course_text: '数学',
          class_label: '七（1）班',
        },
        {
          day_of_week: 2,
          slot_key: 'lesson:3',
          action: 'set',
          course_text: '体育',
          class_label: '',
        },
      ],
    })
    expect(applied).toHaveLength(2)
    expect(applied[0]).toMatchObject({ override_id: 'ov-1', action: 'set' })
  })

  it('requests recent notes grouped by the given classes in order', async () => {
    const fetchMock = vi.fn(async () =>
      response({
        classes: [
          {
            class_label: '七（1）班',
            notes: [
              {
                id: 'n1',
                note_date: '2026-09-01',
                slot_key: 'lesson:3',
                class_label: '七（1）班',
                content_text: '有理数复习',
                homework_text: '练习册 P12',
                created_at: '2026-09-01T09:00:00+00:00',
              },
            ],
          },
          { class_label: '七（2）班', notes: [] },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const groups = await dailyApi.recentNotes(['七（1）班', '七（2）班'], 10)

    const [path] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toContain('/api/class-teacher/daily/notes/recent?')
    expect(path).toContain(`class_labels=${encodeURIComponent('七（1）班,七（2）班')}`)
    expect(path).toContain('limit=10')
    expect(groups.map((group) => group.class_label)).toEqual(['七（1）班', '七（2）班'])
    expect(groups[0]?.notes[0]).toMatchObject({ content_text: '有理数复习', homework_text: '练习册 P12' })
    expect(groups[1]?.notes).toEqual([])
  })

  it('puts the week anchor with the trusted header', async () => {
    const fetchMock = vi.fn(async () =>
      response({ anchor_monday: '2026-08-31', week_no: 3, updated_at: '2026-09-01T09:00:00+00:00' }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const anchor = await dailyApi.setWeekAnchor('2026-09-01', 3)

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/timetable/week-anchor')
    expect(init.method).toBe('PUT')
    expect(init.headers).toMatchObject({ 'x-class-teacher-client': 'class-teacher-browser-v1' })
    expect(JSON.parse(String(init.body))).toEqual({ date: '2026-09-01', week_no: 3 })
    expect(anchor).toMatchObject({ anchor_monday: '2026-08-31', week_no: 3 })
  })
})
