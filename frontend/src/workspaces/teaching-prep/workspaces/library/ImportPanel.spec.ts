import { createPinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MaterialVersion } from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { useMaterialImportQueue } from './importQueue'
import ImportPanel from './ImportPanel.vue'

function material(id: string, name: string): MaterialVersion {
  return {
    id, source_id: id, display_name: name, material_type: 'pptx',
    content_sha256: id[0]!.repeat(64), safe_filename: `${id[0]}.pptx`, size_bytes: 20,
    modified_ns: null, unit_count: 1, inspection_status: 'ready',
    availability: 'available', created_at: '2026-08-03T00:00:00Z',
  }
}

function fileChangeEvent(...files: File[]): Event {
  return { target: { files, value: '' } } as unknown as Event
}

function folderFile(path: string): File {
  const file = new File(['x'], path.split('/').pop()!)
  Object.defineProperty(file, 'webkitRelativePath', { value: path })
  return file
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('material import queue', () => {
  it('queues selected files with a suggested role', () => {
    const queue = useMaterialImportQueue()
    queue.queueFiles(fileChangeEvent(
      new File(['x'], '一次函数课件.pptx'),
      new File(['y'], '同步练习册A本.pdf'),
    ))

    expect(queue.pendingImports.value).toHaveLength(2)
    expect(queue.pendingImports.value[0]?.role).toBe('reference_ppt')
    expect(queue.pendingImports.value[1]?.role).toBe('homework_workbook')
    expect(queue.pendingImports.value[1]?.workbookVolume).toBe('A')
    expect(queue.pendingImportCount.value).toBe(2)
  })

  it('imports queued files through the catalog store', async () => {
    const queue = useMaterialImportQueue()
    const catalog = useTeachingPrepCatalogStore()
    const imported = material('m'.repeat(32), '一次函数课件.pptx')
    const importCopy = vi.spyOn(catalog, 'importMaterialCopy').mockResolvedValue(imported)
    const parse = vi.spyOn(catalog, 'parseMaterialInBackground').mockResolvedValue({} as never)
    vi.spyOn(catalog, 'load').mockResolvedValue()

    queue.queueFiles(fileChangeEvent(new File(['x'], '一次函数课件.pptx')))
    await queue.importQueuedFiles()

    expect(importCopy).toHaveBeenCalledTimes(1)
    expect(parse).toHaveBeenCalledWith(imported)
    expect(queue.pendingImports.value[0]?.state).toBe('done')
  })

  it('keeps a failed import retryable instead of dropping it', async () => {
    const queue = useMaterialImportQueue()
    const catalog = useTeachingPrepCatalogStore()
    vi.spyOn(catalog, 'importMaterialCopy').mockRejectedValue(new Error('磁盘写入失败'))
    vi.spyOn(catalog, 'load').mockResolvedValue()

    queue.queueFiles(fileChangeEvent(new File(['x'], '讲义.pdf')))
    await queue.importQueuedFiles()

    const item = queue.pendingImports.value[0]
    expect(item?.state).toBe('failed')
    expect(item?.error).toContain('磁盘写入失败')
    expect(queue.pendingImportCount.value).toBe(1)
  })

  it('cancels a processing import through the store', async () => {
    const queue = useMaterialImportQueue()
    const catalog = useTeachingPrepCatalogStore()
    const cancel = vi.spyOn(catalog, 'cancelMaterialParse').mockResolvedValue({} as never)

    queue.queueFiles(fileChangeEvent(new File(['x'], '讲义.pdf')))
    const item = queue.pendingImports.value[0]!
    item.state = 'processing'
    item.materialId = 'm'.repeat(32)
    await queue.cancelImport(item)

    expect(cancel).toHaveBeenCalledWith('m'.repeat(32))
  })

  it('clears only finished imports', () => {
    const queue = useMaterialImportQueue()
    queue.queueFiles(fileChangeEvent(
      new File(['x'], 'a.pdf'),
      new File(['y'], 'b.pdf'),
    ))
    queue.pendingImports.value[0]!.state = 'done'
    queue.clearFinishedImports()

    expect(queue.pendingImports.value).toHaveLength(1)
    expect(queue.pendingImports.value[0]?.file.name).toBe('b.pdf')
  })

  it('groups folder-imported PPTs by folder batch and keeps loose files ungrouped', () => {
    const queue = useMaterialImportQueue()
    queue.queuePptFolder(fileChangeEvent(
      folderFile('八上课件/第一章/一次函数.pptx'),
      folderFile('八上课件/第一章/二次函数.pptx'),
    ))
    queue.queueFiles(fileChangeEvent(new File(['z'], '散装讲义.pdf')))

    expect(queue.pendingImports.value).toHaveLength(3)
    expect(queue.importFolderGroups.value).toHaveLength(1)
    const group = queue.importFolderGroups.value[0]!
    expect(group.displayName).toBe('八上课件')
    expect(group.items).toHaveLength(2)
    expect(queue.loosePendingImports.value).toHaveLength(1)
    expect(queue.loosePendingImports.value[0]?.file.name).toBe('散装讲义.pdf')
  })

  it('aggregates folder progress and overall state', () => {
    const queue = useMaterialImportQueue()
    queue.queuePptFolder(fileChangeEvent(
      folderFile('八上课件/一次函数.pptx'),
      folderFile('八上课件/二次函数.pptx'),
      folderFile('八上课件/反比例函数.pptx'),
    ))
    const group = queue.importFolderGroups.value[0]!

    expect(queue.folderGroupStateLabel(group)).toBe('等待导入')
    expect(queue.folderGroupNeedsAttention(group)).toBe(false)

    group.items[0]!.state = 'done'
    group.items[1]!.state = 'done'
    expect(queue.folderGroupCompletedCount(group)).toBe(2)
    expect(queue.folderGroupProgressPercent(group)).toBe(67)
    expect(queue.folderGroupNeedsAttention(group)).toBe(false)

    group.items[2]!.state = 'failed'
    expect(queue.folderGroupStateLabel(group)).toBe('1 份未完成')
    expect(queue.folderGroupNeedsAttention(group)).toBe(true)

    group.items[2]!.state = 'done'
    expect(queue.folderGroupStateLabel(group)).toBe('全部完成')
  })

  it('applies a folder-level role to every member item', () => {
    const queue = useMaterialImportQueue()
    queue.queuePptFolder(fileChangeEvent(
      folderFile('八上课件/一次函数.pptx'),
      folderFile('八上课件/二次函数.pptx'),
    ))
    const group = queue.importFolderGroups.value[0]!
    expect(queue.folderGroupRole(group)).toBe('reference_ppt')

    queue.setFolderGroupRole(group, 'supplement')

    expect(group.items.every(item => item.role === 'supplement')).toBe(true)
    expect(queue.folderGroupRole(group)).toBe('supplement')
  })
})

describe('ImportPanel', () => {
  async function mountPanel() {
    const catalog = useTeachingPrepCatalogStore()
    let queue!: ReturnType<typeof useMaterialImportQueue>
    const Wrapper = defineComponent({
      setup() {
        queue = useMaterialImportQueue()
        return () => h(ImportPanel, { queue })
      },
    })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(Wrapper)
    app.mount(host)
    await nextTick()
    return { app, host, catalog, queue }
  }

  it('renders queued rows and starts the import from the panel', async () => {
    const { app, host, catalog, queue } = await mountPanel()
    const imported = material('m'.repeat(32), '一次函数课件.pptx')
    const importCopy = vi.spyOn(catalog, 'importMaterialCopy').mockResolvedValue(imported)
    vi.spyOn(catalog, 'parseMaterialInBackground').mockResolvedValue({} as never)
    vi.spyOn(catalog, 'load').mockResolvedValue()

    queue.queueFiles(fileChangeEvent(new File(['x'], '一次函数课件.pptx')))
    await nextTick()

    expect(host.textContent).toContain('1 份在队列中')
    expect(host.textContent).toContain('一次函数课件.pptx')

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim().startsWith('开始导入'))?.click()
    await vi.waitFor(() => expect(importCopy).toHaveBeenCalled())
    await vi.waitFor(() => expect(queue.pendingImports.value[0]?.state).toBe('done'))
    app.unmount()
  })

  it('removes a queued row from the panel', async () => {
    const { app, host, queue } = await mountPanel()
    queue.queueFiles(fileChangeEvent(new File(['x'], '讲义.pdf')))
    await nextTick()

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '移除')?.click()
    await nextTick()

    expect(queue.pendingImports.value).toHaveLength(0)
    expect(host.textContent).not.toContain('讲义.pdf')
    app.unmount()
  })

  it('renders folder imports as one aggregate row and expands member details', async () => {
    const { app, host, queue } = await mountPanel()
    queue.queuePptFolder(fileChangeEvent(
      folderFile('八上课件/一次函数.pptx'),
      folderFile('八上课件/二次函数.pptx'),
    ))
    queue.queueFiles(fileChangeEvent(new File(['z'], '散装讲义.pdf')))
    await nextTick()

    // 默认收起：只显示文件夹聚合行，散装文件保持逐份行
    const folderRow = host.querySelector('.tp-import-row--folder')
    expect(folderRow?.textContent).toContain('八上课件')
    expect(folderRow?.textContent).toContain('2 份 PPTX')
    expect(folderRow?.textContent).toContain('已完成 0/2')
    expect(host.textContent).not.toContain('一次函数.pptx')
    expect(host.textContent).toContain('散装讲义.pdf')

    const toggle = [...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '展开明细（2）')
    expect(toggle?.getAttribute('aria-expanded')).toBe('false')
    toggle?.click()
    await nextTick()

    expect(host.textContent).toContain('一次函数.pptx')
    expect(host.textContent).toContain('二次函数.pptx')
    expect(host.querySelectorAll('.tp-import-row--nested')).toHaveLength(2)

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '收起明细')?.click()
    await nextTick()
    expect(host.textContent).not.toContain('一次函数.pptx')
    app.unmount()
  })

  it('expands a folder row by default when a member failed', async () => {
    const { app, host, queue } = await mountPanel()
    queue.queuePptFolder(fileChangeEvent(
      folderFile('八上课件/一次函数.pptx'),
      folderFile('八上课件/二次函数.pptx'),
    ))
    queue.pendingImports.value[0]!.state = 'failed'
    await nextTick()

    expect(host.textContent).toContain('1 份未完成')
    expect(host.textContent).toContain('一次函数.pptx')
    const toggle = [...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '收起明细')
    expect(toggle?.getAttribute('aria-expanded')).toBe('true')
    app.unmount()
  })

  it('applies the folder role select to every member item', async () => {
    const { app, host, queue } = await mountPanel()
    queue.queuePptFolder(fileChangeEvent(
      folderFile('八上课件/一次函数.pptx'),
      folderFile('八上课件/二次函数.pptx'),
    ))
    await nextTick()

    const select = host.querySelector<HTMLSelectElement>('.tp-import-row--folder select')
    expect(select?.value).toBe('reference_ppt')
    select!.value = 'supplement'
    select!.dispatchEvent(new Event('change'))
    await nextTick()

    expect(queue.pendingImports.value.every(item => item.role === 'supplement')).toBe(true)
    app.unmount()
  })
})
