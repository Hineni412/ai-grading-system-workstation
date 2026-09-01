import { createApp, nextTick, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TableGridEditor from '../views/daily/tables/TableGridEditor.vue'
import TableListPanel from '../views/daily/tables/TableListPanel.vue'

const BASE = '/api/class-teacher/daily'

type Handler = (init: RequestInit, path: string) => Response

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json', 'x-request-id': 'rid' },
  })
}

function errorResponse(message: string, status = 422): Response {
  return json({ error: { code: 'daily_table_error', message, details: {}, request_id: 'rid' } }, status)
}

/** 按「方法 + 路径前缀」分发；数组顺序即匹配顺序（更具体的要放前面）。 */
function installFetch(routes: Array<[string, Handler]>) {
  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    for (const [key, handler] of routes) {
      const [routeMethod, prefix = ''] = key.split(' ', 2)
      if (method === routeMethod && path.startsWith(prefix)) {
        return handler(init ?? {}, path)
      }
    }
    throw new Error(`unexpected fetch: ${method} ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function tableSummary(id: string, title: string, status: 'active' | 'archived') {
  return {
    id,
    title,
    status,
    column_count: 2,
    row_count: 58,
    created_at: '2026-09-01T08:00:00+00:00',
    updated_at: '2026-09-01T09:00:00+00:00',
  }
}

function editorDetail() {
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
      { id: 'c2', name: '次数', col_type: 'number', options: [], position: 1 },
      { id: 'c3', name: '组别', col_type: 'select', options: ['A组', 'B组'], position: 2 },
      { id: 'c4', name: '交期', col_type: 'date', options: [], position: 3 },
      { id: 'c5', name: '备注', col_type: 'text', options: [], position: 4 },
    ],
    rows: [
      { id: 'r1', student_ref: '七（1）班|01', display_name: '张三', class_label: '七（1）班', position: 0 },
      { id: 'r2', student_ref: '七（1）班|02', display_name: '李四', class_label: '七（1）班', position: 1 },
    ],
    cells: {},
  }
}

const mounted: App[] = []

function mountComponent(component: Component, props: Record<string, unknown>): HTMLElement {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(component, props)
  app.mount(host)
  mounted.push(app)
  return host
}

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

function setInputValue(element: HTMLInputElement, value: string): void {
  element.value = value
  element.dispatchEvent(new Event('input'))
}

function setSelectValue(element: HTMLSelectElement, value: string): void {
  element.value = value
  element.dispatchEvent(new Event('change'))
}

/** 按可见文本找按钮（忽略模板缩进产生的空白）。 */
function buttonByText(host: HTMLElement, text: string): HTMLButtonElement {
  const button = [...host.querySelectorAll('button')].find(
    (item) => item.textContent?.trim() === text,
  )
  if (!button) throw new Error(`button not found: ${text}`)
  return button
}

function putBodies(fetchMock: ReturnType<typeof installFetch>): unknown[] {
  return fetchMock.mock.calls
    .map((call) => call as unknown as [string, RequestInit])
    .filter(([, init]) => init.method === 'PUT')
    .map(([, init]) => JSON.parse(String(init.body)))
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('daily table list panel', () => {
  it('renders active and archived tables in separate sections', async () => {
    installFetch([
      [`GET ${BASE}/tables?status=all`, () =>
        json({
          tables: [
            tableSummary('t1', '研学回执统计', 'active'),
            tableSummary('t2', '作业收交登记', 'archived'),
          ],
        })],
    ])

    const host = mountComponent(TableListPanel, {})
    await vi.waitFor(() => expect(host.textContent).toContain('研学回执统计'))

    const sections = [...host.querySelectorAll('section[aria-label]')]
    const active = sections.find((section) => section.getAttribute('aria-label') === '进行中的表格')
    const archived = sections.find((section) => section.getAttribute('aria-label') === '已归档的表格')
    expect(active?.textContent).toContain('研学回执统计')
    expect(active?.textContent).toContain('58 行 × 2 列')
    expect(active?.textContent).not.toContain('作业收交登记')
    expect(archived?.textContent).toContain('作业收交登记')
    expect(active?.textContent).toContain('归档')
    expect(archived?.textContent).toContain('恢复')
  })

  it('creates a table through the wizard and opens the editor', async () => {
    const fetchMock = installFetch([
      [`GET ${BASE}/tables?status=all`, () => json({ tables: [] })],
      [`GET ${BASE}/roster-classes`, () =>
        json({
          classes: [
            { class_label: '七（1）班', student_count: 30 },
            { class_label: '七（2）班', student_count: 28 },
          ],
        })],
      [`POST ${BASE}/tables`, () =>
        json({ ...editorDetail(), empty_class_labels: ['七（2）班'] })],
      [`GET ${BASE}/tables/t1`, () => json(editorDetail())],
    ])

    const host = mountComponent(TableListPanel, {})
    await vi.waitFor(() => expect(host.textContent).toContain('暂无进行中的表格'))

    buttonByText(host, '新建表格').click()
    await vi.waitFor(() => expect(host.textContent).toContain('七（1）班（30 人）'))
    expect(host.textContent).toContain('创建时按当前名单快照生成学生行')

    const checkboxes = [...host.querySelectorAll<HTMLInputElement>('.table-wizard__class-list input')]
    for (const checkbox of checkboxes) {
      checkbox.checked = true
      checkbox.dispatchEvent(new Event('change'))
    }
    await settle()
    expect(host.textContent).toContain('已选 2 个班，共 58 人')

    const titleInput = host.querySelector<HTMLInputElement>('.table-wizard__field input')!
    setInputValue(titleInput, '研学回执统计')

    const draftName = host.querySelector<HTMLInputElement>('input[aria-label="预建列列名"]')!
    setInputValue(draftName, '已交')
    buttonByText(host, '添加列').click()
    await settle()

    buttonByText(host, '创建表格').click()

    await vi.waitFor(() => expect(host.textContent).toContain('张三'))
    const postCall = fetchMock.mock.calls.find(
      (call) => (call as unknown as [string, RequestInit])[1].method === 'POST',
    ) as unknown as [string, RequestInit]
    expect(postCall[0]).toBe(`${BASE}/tables`)
    expect(JSON.parse(String(postCall[1].body))).toEqual({
      title: '研学回执统计',
      class_labels: ['七（1）班', '七（2）班'],
      columns: [{ name: '已交', col_type: 'check' }],
    })
    expect(host.textContent).toContain('班级 七（2）班 的名单为空')
  })

  it('guides the teacher to import the roster when no classes exist', async () => {
    installFetch([
      [`GET ${BASE}/tables?status=all`, () => json({ tables: [] })],
      [`GET ${BASE}/roster-classes`, () => json({ classes: [] })],
    ])

    const host = mountComponent(TableListPanel, {})
    await vi.waitFor(() => expect(host.textContent).toContain('新建表格'))
    buttonByText(host, '新建表格').click()
    await vi.waitFor(() =>
      expect(host.textContent).toContain('请先在学生管理中导入学生名单'),
    )
  })

  it('archives, restores and deletes tables with confirmation', async () => {
    let tables = [tableSummary('t1', '研学回执统计', 'active' as const)]
    const fetchMock = installFetch([
      [`PATCH ${BASE}/tables/t1`, (init) => {
        const body = JSON.parse(String(init.body)) as { status: 'active' | 'archived' }
        tables = [tableSummary('t1', '研学回执统计', body.status)]
        return json(editorDetail())
      }],
      [`DELETE ${BASE}/tables/t1`, () => {
        tables = []
        return json({ deleted_table_id: 't1' })
      }],
      [`GET ${BASE}/tables?status=all`, () => json({ tables })],
    ])
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const host = mountComponent(TableListPanel, {})
    await vi.waitFor(() => expect(host.textContent).toContain('研学回执统计'))

    buttonByText(host, '归档').click()
    await vi.waitFor(() => {
      const archived = host.querySelector('section[aria-label="已归档的表格"]')
      expect(archived?.textContent).toContain('研学回执统计')
    })
    expect(fetchMock.mock.calls.some((call) => {
      const [path, init] = call as unknown as [string, RequestInit]
      return (
        path === `${BASE}/tables/t1`
        && init.method === 'PATCH'
        && JSON.parse(String(init.body)).status === 'archived'
      )
    })).toBe(true)

    buttonByText(host, '恢复').click()
    await vi.waitFor(() => {
      const active = host.querySelector('section[aria-label="进行中的表格"]')
      expect(active?.textContent).toContain('研学回执统计')
    })

    buttonByText(host, '删除').click()
    await vi.waitFor(() => expect(host.textContent).toContain('暂无进行中的表格'))
    expect(window.confirm).toHaveBeenCalled()
    expect(fetchMock.mock.calls.some((call) => {
      const [path, init] = call as unknown as [string, RequestInit]
      return path === `${BASE}/tables/t1` && init.method === 'DELETE'
    })).toBe(true)
  })
})

describe('daily table grid editor', () => {
  function editorFetch(detail: ReturnType<typeof editorDetail>, options?: { failNextPut?: boolean }) {
    let putFailures = options?.failNextPut ? 1 : 0
    return installFetch([
      [`PUT ${BASE}/tables/t1/cells`, () => {
        if (putFailures > 0) {
          putFailures -= 1
          return errorResponse('数字列只能填写数字')
        }
        return json({ updated: 1 })
      }],
      [`GET ${BASE}/tables/t1`, () => json(detail)],
    ])
  }

  it('saves each cell type through the batch cells endpoint', async () => {
    const fetchMock = editorFetch(editorDetail())
    const host = mountComponent(TableGridEditor, { tableId: 't1' })
    await vi.waitFor(() => expect(host.textContent).toContain('张三'))

    // 打勾：点击即存为 '1'
    const check = host.querySelector<HTMLInputElement>('input[aria-label="张三·已交"]')!
    check.checked = true
    check.dispatchEvent(new Event('change'))
    await vi.waitFor(() =>
      expect(putBodies(fetchMock)).toContainEqual({
        updates: [{ row_id: 'r1', column_id: 'c1', value_text: '1' }],
      }),
    )

    // 数字：blur 保存
    const number = host.querySelector<HTMLInputElement>('input[aria-label="李四·次数"]')!
    setInputValue(number, '3')
    number.dispatchEvent(new Event('blur'))
    await vi.waitFor(() =>
      expect(putBodies(fetchMock)).toContainEqual({
        updates: [{ row_id: 'r2', column_id: 'c2', value_text: '3' }],
      }),
    )

    // 单选：选中即存；空选项清除
    const select = host.querySelector<HTMLSelectElement>('select[aria-label="张三·组别"]')!
    setSelectValue(select, 'A组')
    await vi.waitFor(() =>
      expect(putBodies(fetchMock)).toContainEqual({
        updates: [{ row_id: 'r1', column_id: 'c3', value_text: 'A组' }],
      }),
    )

    // 日期：change 即存
    const date = host.querySelector<HTMLInputElement>('input[aria-label="李四·交期"]')!
    date.value = '2026-09-05'
    date.dispatchEvent(new Event('change'))
    await vi.waitFor(() =>
      expect(putBodies(fetchMock)).toContainEqual({
        updates: [{ row_id: 'r2', column_id: 'c4', value_text: '2026-09-05' }],
      }),
    )

    // 文本：blur 保存并去掉首尾空白
    const text = host.querySelector<HTMLInputElement>('input[aria-label="张三·备注"]')!
    setInputValue(text, '  已电话通知  ')
    text.dispatchEvent(new Event('blur'))
    await vi.waitFor(() =>
      expect(putBodies(fetchMock)).toContainEqual({
        updates: [{ row_id: 'r1', column_id: 'c5', value_text: '已电话通知' }],
      }),
    )
    const puts = fetchMock.mock.calls.filter(
      (call) => (call as unknown as [string, RequestInit])[1].method === 'PUT',
    )
    expect(puts.every((call) => {
      const init = (call as unknown as [string, RequestInit])[1]
      return (init.headers as Record<string, string>)['x-class-teacher-client']
        === 'class-teacher-browser-v1'
    })).toBe(true)
  })

  it('keeps the local draft and flags the cell when saving fails', async () => {
    editorFetch(editorDetail(), { failNextPut: true })
    const host = mountComponent(TableGridEditor, { tableId: 't1' })
    await vi.waitFor(() => expect(host.textContent).toContain('张三'))

    const text = host.querySelector<HTMLInputElement>('input[aria-label="张三·备注"]')!
    setInputValue(text, '草稿内容')
    text.dispatchEvent(new Event('blur'))

    await vi.waitFor(() => expect(host.textContent).toContain('未保存'))
    expect(host.textContent).toContain('数字列只能填写数字')
    expect(text.value).toBe('草稿内容')
  })

  it('manages columns: add, reorder, rename and delete with confirmation', async () => {
    const detail = editorDetail()
    const fetchMock = installFetch([
      [`POST ${BASE}/tables/t1/columns`, () =>
        json({ id: 'c6', name: '签名', col_type: 'text', options: [], position: 5 })],
      [`PATCH ${BASE}/tables/t1/columns/c1`, () =>
        json({ id: 'c1', name: '已交回执', col_type: 'check', options: [], position: 1 })],
      [`DELETE ${BASE}/tables/t1/columns/c1`, () =>
        json({ deleted_column_id: 'c1', removed_cells: 12 })],
      [`GET ${BASE}/tables/t1`, () => json(detail)],
    ])
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const host = mountComponent(TableGridEditor, { tableId: 't1' })
    await vi.waitFor(() => expect(host.textContent).toContain('张三'))

    buttonByText(host, '列管理').click()
    await vi.waitFor(() => expect(host.textContent).toContain('修改列类型不会清空'))

    // 加列
    const nameInput = host.querySelector<HTMLInputElement>('input[aria-label="新列列名"]')!
    setInputValue(nameInput, '签名')
    buttonByText(host, '添加列').click()
    await vi.waitFor(() => expect(host.textContent).toContain('已添加列「签名」'))
    expect(fetchMock.mock.calls.some((call) => {
      const [path, init] = call as unknown as [string, RequestInit]
      return (
        path === `${BASE}/tables/t1/columns`
        && init.method === 'POST'
        && JSON.parse(String(init.body)).name === '签名'
      )
    })).toBe(true)

    // 右移第一列 → PATCH position
    const moveButton = host.querySelector<HTMLButtonElement>('button[aria-label="右移 已交"]')!
    moveButton.click()
    await vi.waitFor(() => expect(host.textContent).toContain('已调整列顺序'))
    expect(fetchMock.mock.calls.some((call) => {
      const [path, init] = call as unknown as [string, RequestInit]
      return (
        path === `${BASE}/tables/t1/columns/c1`
        && init.method === 'PATCH'
        && JSON.parse(String(init.body)).position === 1
      )
    })).toBe(true)

    // 改列名
    const firstColumnRow = [...host.querySelectorAll('.column-editor__list li')].find((item) =>
      item.textContent?.includes('已交'),
    )!
    // 行内按钮顺序：← → 编辑 删除
    firstColumnRow.querySelectorAll('button')[2]!.click()
    await settle()
    const editName = host.querySelector<HTMLInputElement>('input[aria-label="列名"]')!
    setInputValue(editName, '已交回执')
    const saveEdit = [...host.querySelectorAll<HTMLButtonElement>('.column-editor__edit button')].find(
      (button) => button.textContent === '保存',
    )!
    saveEdit.click()
    await vi.waitFor(() => expect(host.textContent).toContain('列设置已保存'))
    expect(fetchMock.mock.calls.some((call) => {
      const [path, init] = call as unknown as [string, RequestInit]
      return (
        path === `${BASE}/tables/t1/columns/c1`
        && init.method === 'PATCH'
        && JSON.parse(String(init.body)).name === '已交回执'
      )
    })).toBe(true)

    // 删列（确认后连带清空提示由 confirm 文案承担）
    const deleteButton = [...host.querySelectorAll('.column-editor__list li')].find((item) =>
      item.textContent?.includes('已交'),
    )!.querySelector<HTMLButtonElement>('.column-editor__danger')!
    deleteButton.click()
    await vi.waitFor(() => expect(host.textContent).toContain('已删除列「已交」'))
    expect(fetchMock.mock.calls.some((call) => {
      const [path, init] = call as unknown as [string, RequestInit]
      return path === `${BASE}/tables/t1/columns/c1` && init.method === 'DELETE'
    })).toBe(true)
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining('连带清空该列已填写的所有内容'),
    )
  })

  it('renames, archives, deletes from the header and exports CSV', async () => {
    const detail = editorDetail()
    const disposition =
      "attachment; filename=\"daily-table.csv\"; filename*=UTF-8''%E7%A0%94%E5%AD%A6.csv"
    const fetchMock = installFetch([
      [`GET ${BASE}/tables/t1/export.csv`, () =>
        new Response('\uFEFF学生,已交\r\n张三,1\r\n', {
          status: 200,
          headers: { 'content-type': 'text/csv', 'content-disposition': disposition, 'x-request-id': 'rid' },
        })],
      [`PATCH ${BASE}/tables/t1`, () => json(detail)],
      [`DELETE ${BASE}/tables/t1`, () => json({ deleted_table_id: 't1' })],
      [`GET ${BASE}/tables/t1`, () => json(detail)],
    ])
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const NativeURL = globalThis.URL
    class TestURL extends NativeURL {}
    Object.defineProperties(TestURL, {
      createObjectURL: { configurable: true, value: vi.fn(() => 'blob:csv') },
      revokeObjectURL: { configurable: true, value: vi.fn() },
    })
    vi.stubGlobal('URL', TestURL)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const closeSpy = vi.fn()
    const host = mountComponent(TableGridEditor, { tableId: 't1', onClose: closeSpy })
    await vi.waitFor(() => expect(host.textContent).toContain('研学回执统计'))

    // 重命名
    buttonByText(host, '重命名').click()
    await settle()
    const renameInput = host.querySelector<HTMLInputElement>('input[aria-label="表格名称"]')!
    setInputValue(renameInput, '研学回执统计（新）')
    const saveRename = [...host.querySelectorAll<HTMLButtonElement>('.table-editor__rename button')].find(
      (button) => button.textContent === '保存',
    )!
    saveRename.click()
    await vi.waitFor(() => expect(host.textContent).toContain('已重命名表格'))
    expect(fetchMock.mock.calls.some((call) => {
      const [path, init] = call as unknown as [string, RequestInit]
      return (
        path === `${BASE}/tables/t1`
        && init.method === 'PATCH'
        && JSON.parse(String(init.body)).title === '研学回执统计（新）'
      )
    })).toBe(true)

    // 归档
    buttonByText(host, '归档').click()
    await vi.waitFor(() => expect(host.textContent).toContain('已归档，回到列表后'))
    expect(fetchMock.mock.calls.some((call) => {
      const [path, init] = call as unknown as [string, RequestInit]
      return (
        path === `${BASE}/tables/t1`
        && init.method === 'PATCH'
        && JSON.parse(String(init.body)).status === 'archived'
      )
    })).toBe(true)

    // 导出 CSV
    buttonByText(host, '导出 CSV').click()
    await vi.waitFor(() => expect(host.textContent).toContain('已导出 CSV'))
    expect(URL.createObjectURL).toHaveBeenCalled()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:csv')

    // 删除（确认后返回列表）
    buttonByText(host, '删除').click()
    await vi.waitFor(() => expect(closeSpy).toHaveBeenCalled())
    expect(fetchMock.mock.calls.some((call) => {
      const [path, init] = call as unknown as [string, RequestInit]
      return path === `${BASE}/tables/t1` && init.method === 'DELETE'
    })).toBe(true)
  })
})
