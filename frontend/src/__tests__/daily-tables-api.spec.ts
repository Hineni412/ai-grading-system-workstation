import { afterEach, describe, expect, it, vi } from 'vitest'

import { dailyApi } from '../api/daily'

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json', 'x-request-id': 'rid' },
  })
}

function tableDetailPayload() {
  return {
    table: {
      id: 't1',
      title: '研学回执统计',
      status: 'active',
      created_at: '2026-09-01T08:00:00+00:00',
      updated_at: '2026-09-01T09:00:00+00:00',
    },
    columns: [
      { id: 'c1', name: '已交', col_type: 'check', options: [], position: 0 },
      { id: 'c2', name: '备注', col_type: 'text', options: [], position: 1 },
    ],
    rows: [
      {
        id: 'r1',
        student_ref: '七（1）班|01',
        display_name: '张三',
        class_label: '七（1）班',
        position: 0,
      },
    ],
    cells: { r1: { c1: '1' } },
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('daily tables API contract', () => {
  it('decodes the roster class list with student counts', async () => {
    const fetchMock = vi.fn(async () =>
      response({
        classes: [
          { class_label: '七（1）班', student_count: 30 },
          { class_label: '七（2）班', student_count: 28 },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const classes = await dailyApi.rosterClasses()

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/roster-classes')
    expect(init.method).toBe('GET')
    expect(classes).toEqual([
      { class_label: '七（1）班', student_count: 30 },
      { class_label: '七（2）班', student_count: 28 },
    ])
  })

  it('lists table summaries with the requested status filter', async () => {
    const fetchMock = vi.fn(async () =>
      response({
        tables: [
          {
            id: 't1',
            title: '研学回执统计',
            status: 'archived',
            column_count: 2,
            row_count: 58,
            created_at: '2026-09-01T08:00:00+00:00',
            updated_at: '2026-09-01T09:00:00+00:00',
          },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const tables = await dailyApi.tables('archived')

    const [path] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/tables?status=archived')
    expect(tables[0]).toMatchObject({ id: 't1', status: 'archived', row_count: 58 })
  })

  it('creates a table with the trusted header and decodes the flattened detail', async () => {
    const fetchMock = vi.fn(async () =>
      response({ ...tableDetailPayload(), empty_class_labels: ['七（2）班'] }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const created = await dailyApi.createTable({
      title: '研学回执统计',
      class_labels: ['七（1）班', '七（2）班'],
      columns: [{ name: '已交', col_type: 'check' }],
    })

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/tables')
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({ 'x-class-teacher-client': 'class-teacher-browser-v1' })
    expect(JSON.parse(String(init.body))).toEqual({
      title: '研学回执统计',
      class_labels: ['七（1）班', '七（2）班'],
      columns: [{ name: '已交', col_type: 'check' }],
    })
    expect(created.id).toBe('t1')
    expect(created.columns.map((column) => column.name)).toEqual(['已交', '备注'])
    expect(created.rows[0]).toMatchObject({ display_name: '张三', class_label: '七（1）班' })
    expect(created.cells).toEqual({ r1: { c1: '1' } })
    expect(created.empty_class_labels).toEqual(['七（2）班'])
  })

  it('decodes the table detail from the nested table payload', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(tableDetailPayload())))

    const detail = await dailyApi.table('t1')

    expect(detail.title).toBe('研学回执统计')
    expect(detail.status).toBe('active')
    expect(detail.columns[1]).toMatchObject({ id: 'c2', col_type: 'text', position: 1 })
    expect(detail.cells.r1).toEqual({ c1: '1' })
  })

  it('patches table title or status with the trusted header', async () => {
    const fetchMock = vi.fn(async () => response(tableDetailPayload()))
    vi.stubGlobal('fetch', fetchMock)

    await dailyApi.updateTable('t1', { status: 'archived' })

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/tables/t1')
    expect(init.method).toBe('PATCH')
    expect(init.headers).toMatchObject({ 'x-class-teacher-client': 'class-teacher-browser-v1' })
    expect(JSON.parse(String(init.body))).toEqual({ status: 'archived' })
  })

  it('deletes a table and decodes the deleted id', async () => {
    const fetchMock = vi.fn(async () => response({ deleted_table_id: 't1' }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(dailyApi.deleteTable('t1')).resolves.toBe('t1')

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/tables/t1')
    expect(init.method).toBe('DELETE')
  })

  it('posts a new column and decodes the column view', async () => {
    const fetchMock = vi.fn(async () =>
      response({ id: 'c3', name: '组别', col_type: 'select', options: ['A', 'B'], position: 2 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const column = await dailyApi.addTableColumn('t1', {
      name: '组别',
      col_type: 'select',
      options: ['A', 'B'],
    })

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/tables/t1/columns')
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({ 'x-class-teacher-client': 'class-teacher-browser-v1' })
    expect(JSON.parse(String(init.body))).toEqual({
      name: '组别',
      col_type: 'select',
      options: ['A', 'B'],
    })
    expect(column).toMatchObject({ id: 'c3', col_type: 'select', options: ['A', 'B'] })
  })

  it('patches and deletes columns, decoding removed cell counts', async () => {
    const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return response({ id: 'c1', name: '已交回执', col_type: 'check', options: [], position: 1 })
      }
      return response({ deleted_column_id: 'c1', removed_cells: 12 })
    })
    vi.stubGlobal('fetch', fetchMock)

    const renamed = await dailyApi.updateTableColumn('t1', 'c1', { name: '已交回执', position: 1 })
    expect(renamed).toMatchObject({ name: '已交回执', position: 1 })

    const removed = await dailyApi.deleteTableColumn('t1', 'c1')
    expect(removed).toEqual({ deleted_column_id: 'c1', removed_cells: 12 })

    const [patchPath, patchInit] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(patchPath).toBe('/api/class-teacher/daily/tables/t1/columns/c1')
    expect(patchInit.method).toBe('PATCH')
    expect(JSON.parse(String(patchInit.body))).toEqual({ name: '已交回执', position: 1 })
    const [deletePath, deleteInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
    expect(deletePath).toBe('/api/class-teacher/daily/tables/t1/columns/c1')
    expect(deleteInit.method).toBe('DELETE')
  })

  it('puts cell updates as a single batch with the trusted header', async () => {
    const fetchMock = vi.fn(async () => response({ updated: 2 }))
    vi.stubGlobal('fetch', fetchMock)

    const updated = await dailyApi.putTableCells('t1', [
      { row_id: 'r1', column_id: 'c1', value_text: '1' },
      { row_id: 'r1', column_id: 'c2', value_text: '' },
    ])

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/tables/t1/cells')
    expect(init.method).toBe('PUT')
    expect(init.headers).toMatchObject({ 'x-class-teacher-client': 'class-teacher-browser-v1' })
    expect(JSON.parse(String(init.body))).toEqual({
      updates: [
        { row_id: 'r1', column_id: 'c1', value_text: '1' },
        { row_id: 'r1', column_id: 'c2', value_text: '' },
      ],
    })
    expect(updated).toBe(2)
  })

  it('downloads the CSV export as a blob with its content disposition', async () => {
    const disposition =
      "attachment; filename=\"daily-table.csv\"; filename*=UTF-8''%E7%A0%94%E5%AD%A6.csv"
    const fetchMock = vi.fn(async () =>
      new Response('\uFEFF学生,已交\r\n张三,1\r\n', {
        status: 200,
        headers: {
          'content-type': 'text/csv; charset=utf-8',
          'content-disposition': disposition,
          'x-request-id': 'rid',
        },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await dailyApi.exportTableCsv('t1')

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/class-teacher/daily/tables/t1/export.csv')
    expect(init.method).toBe('GET')
    expect(result.contentDisposition).toBe(disposition)
    expect(await result.blob.text()).toContain('张三')
  })
})
