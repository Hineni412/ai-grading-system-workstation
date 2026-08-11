import { computed, ref } from 'vue'

import type { JobResponse } from '../../../../api/jobs'
import type { SemesterMaterialRole } from '../../api/catalog'
import { teachingPrepCatalogApi } from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { fileSizeLabel, suggestedRole } from './libraryShared'

export type PendingImportState =
  | 'pending'
  | 'uploading'
  | 'processing'
  | 'done'
  | 'failed'
  | 'cancelled'

export interface PendingMaterialImport {
  id: string
  file: File
  role: SemesterMaterialRole
  workbookSeries: string
  workbookVolume: 'A' | 'B'
  state: PendingImportState
  materialId: string | null
  error: string
  folderBatchId: string | null
  relativePath: string
}

export interface PendingPptFolderBatch {
  id: string
  requestToken: string
  displayName: string
  pptCount: number
  ignoredFileCount: number
  duplicatePptCount: number
  state: 'pending' | 'creating' | 'done' | 'failed'
  error: string
}

/**
 * 资料导入队列：受控复制 + 后台解析 + PPT 文件夹合集建立。
 * 由 LibraryPage 创建，ImportPanel 与 ReferenceCollections 共享（逻辑自旧
 * MaterialLibraryWorkspace 平移，后台行为不变）。
 */
export function useMaterialImportQueue() {
  const catalog = useTeachingPrepCatalogStore()
  const pendingImports = ref<PendingMaterialImport[]>([])
  const pendingPptFolders = ref<PendingPptFolderBatch[]>([])
  const importBatchRunning = ref(false)
  const message = ref('')

  const pendingImportCount = computed(
    () => pendingImports.value.filter(item => (
      item.state === 'pending' || item.state === 'failed' || item.state === 'cancelled'
    )).length,
  )

  function parseJobFor(item: PendingMaterialImport): JobResponse | null {
    return item.materialId
      ? catalog.materialParseJobs[item.materialId] ?? null
      : null
  }

  function progressPercent(item: PendingMaterialImport): number {
    if (item.state === 'done') return 100
    const job = parseJobFor(item)
    if (job) return Math.max(0, Math.min(100, Math.round(job.progress * 100)))
    return item.state === 'uploading' ? 2 : 0
  }

  function importStateLabel(item: PendingMaterialImport): string {
    const job = parseJobFor(item)
    if (item.state === 'processing' && job) {
      if (job.status === 'queued') return '等待解析'
      if (job.cancel_requested) return '正在安全停止'
      return job.detail || '正在逐页处理'
    }
    return {
      pending: '等待导入',
      uploading: '正在复制到本机资料库',
      processing: '正在逐页处理',
      done: '已完成',
      failed: '未完成，可重试',
      cancelled: '已停止，可继续',
    }[item.state]
  }

  function failureMessage(error: unknown): string {
    if (error instanceof Error && error.message.trim()) return error.message
    return catalog.errorMessage || '资料处理没有完成，可保留进度后重试。'
  }

  function queueFiles(event: Event): void {
    const input = event.target as HTMLInputElement
    const files = Array.from(input.files ?? [])
    input.value = ''
    for (const file of files) {
      pendingImports.value.push({
        id: globalThis.crypto.randomUUID(),
        file,
        role: suggestedRole(file.name),
        workbookSeries: file.name.replace(/\.[^.]+$/, '').replace(/[（(]?\s*[AB]\s*本?[）)]?$/i, '').trim(),
        workbookVolume: /(?:^|[^a-z])b\s*本?(?:[^a-z]|$)/i.test(file.name) ? 'B' : 'A',
        state: 'pending',
        materialId: null,
        error: '',
        folderBatchId: null,
        relativePath: file.name,
      })
    }
    if (files.length > 0) {
      message.value = `已加入 ${files.length} 个文件，请逐份确认资料角色后开始导入。`
    }
  }

  function queuePptFolder(event: Event): void {
    const input = event.target as HTMLInputElement
    const selected = Array.from(input.files ?? [])
    input.value = ''
    if (selected.length === 0) return
    const paths = selected.map(file => (
      (file as File & { webkitRelativePath?: string }).webkitRelativePath
        || file.name
    ).replaceAll('\\', '/'))
    const firstParts = paths[0]?.split('/').filter(Boolean) ?? []
    const rootName = firstParts.length > 1 ? firstParts[0]! : '参考课件合集'
    const pptFiles = selected.filter(file => file.name.toLowerCase().endsWith('.pptx'))
    const batch: PendingPptFolderBatch = {
      id: globalThis.crypto.randomUUID(),
      requestToken: `ppt-folder-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
      displayName: rootName,
      pptCount: pptFiles.length,
      ignoredFileCount: selected.length - pptFiles.length,
      duplicatePptCount: 0,
      state: 'pending',
      error: '',
    }
    if (pptFiles.length === 0) {
      message.value = '这个文件夹中没有找到 PPTX，未导入任何文件。'
      return
    }
    pendingPptFolders.value.push(batch)
    for (const file of pptFiles) {
      const rawPath = (
        (file as File & { webkitRelativePath?: string }).webkitRelativePath
          || file.name
      ).replaceAll('\\', '/')
      const parts = rawPath.split('/').filter(Boolean)
      const relativePath = parts.length > 1 ? parts.slice(1).join('/') : file.name
      pendingImports.value.push({
        id: globalThis.crypto.randomUUID(),
        file,
        role: 'reference_ppt',
        workbookSeries: '',
        workbookVolume: 'A',
        state: 'pending',
        materialId: null,
        error: '',
        folderBatchId: batch.id,
        relativePath,
      })
    }
    message.value = (
      `已选择“${rootName}”：${pptFiles.length} 份 PPTX`
      + (batch.ignoredFileCount > 0 ? `，${batch.ignoredFileCount} 个非 PPT 文件将忽略。` : '。')
    )
  }

  function removePendingImport(id: string): void {
    pendingImports.value = pendingImports.value.filter(item => item.id !== id)
  }

  function clearFinishedImports(): void {
    pendingImports.value = pendingImports.value.filter(item => item.state !== 'done')
  }

  async function importQueuedFiles(): Promise<void> {
    if (importBatchRunning.value || pendingImportCount.value === 0) return
    importBatchRunning.value = true
    let completed = 0
    let failed = 0
    const parseTasks: Promise<void>[] = []
    try {
      for (const item of pendingImports.value) {
        if (
          item.state !== 'pending'
          && item.state !== 'failed'
          && item.state !== 'cancelled'
        ) continue
        item.state = 'uploading'
        item.error = ''
        message.value = `正在复制“${item.file.name}”，复制后会在后台逐页处理…`
        try {
          const material = item.materialId
            ? catalog.materials.find(value => value.id === item.materialId)
            : await catalog.importMaterialCopy(
                item.file,
                item.role,
                item.role === 'homework_workbook'
                  ? { series: item.workbookSeries.trim() || item.file.name, volume: item.workbookVolume }
                  : undefined,
              )
          if (!material) throw new Error('没有找到可继续处理的资料副本。')
          item.materialId = material.id
          item.state = 'processing'
          parseTasks.push(
            catalog.parseMaterialInBackground(material)
              .then(() => {
                item.state = 'done'
                completed += 1
              })
              .catch((error: unknown) => {
                const job = parseJobFor(item)
                item.state = job?.status === 'cancelled' ? 'cancelled' : 'failed'
                item.error = failureMessage(error)
                failed += 1
              }),
          )
        } catch (error) {
          item.state = 'failed'
          item.error = failureMessage(error)
          failed += 1
        }
      }
      importBatchRunning.value = false
      await Promise.allSettled(parseTasks)
      await catalog.load()
      const completedFolderCount = pendingPptFolders.value.filter(
        batch => batch.state === 'done',
      ).length
      await createCompletedPptCollections()
      const createdFolder = pendingPptFolders.value.filter(
        batch => batch.state === 'done',
      ).length > completedFolderCount
      message.value = failed > 0
        ? `已完成 ${completed} 份，${failed} 份未完成；其余资料已保留，可单独重试失败项。`
        : createdFolder
          ? '课件文件夹已完整收录，并生成无需模型的课时树建议，请在下方核对。'
          : `已完成 ${completed} 份资料的受控复制、角色登记和页级解析。`
    } finally {
      importBatchRunning.value = false
    }
  }

  async function cancelImport(item: PendingMaterialImport): Promise<void> {
    if (!item.materialId || item.state !== 'processing') return
    try {
      await catalog.cancelMaterialParse(item.materialId)
      message.value = `正在安全停止“${item.file.name}”；已完成的预览会保留。`
    } catch (error) {
      item.error = failureMessage(error)
    }
  }

  async function createCompletedPptCollections(): Promise<void> {
    const semesterId = catalog.selectedSemester?.id
    if (!semesterId) return
    for (const batch of pendingPptFolders.value) {
      if (batch.state === 'done' || batch.state === 'creating') continue
      const imports = pendingImports.value.filter(item => item.folderBatchId === batch.id)
      if (imports.length !== batch.pptCount || imports.some(item => item.state !== 'done')) {
        continue
      }
      const membersByRecord = new Map<string, { material_record_id: string; relative_path: string }>()
      let missingMaterialCount = 0
      for (const item of imports) {
        const record = catalog.semesterMaterials.find(value => (
          value.current_material_version_id === item.materialId
        ))
        if (!record) {
          missingMaterialCount += 1
          continue
        }
        if (!membersByRecord.has(record.id)) {
          membersByRecord.set(record.id, {
            material_record_id: record.id,
            relative_path: item.relativePath,
          })
        }
      }
      if (missingMaterialCount > 0) {
        batch.state = 'failed'
        batch.error = '个别课件尚未加入本学期，请刷新后重试建立合集。'
        continue
      }
      const members = Array.from(membersByRecord.values())
      batch.duplicatePptCount = imports.length - members.length
      batch.state = 'creating'
      batch.error = ''
      try {
        await teachingPrepCatalogApi.createReferencePptCollection(
          semesterId,
          {
            request_token: batch.requestToken,
            display_name: batch.displayName,
            ignored_file_count: batch.ignoredFileCount,
            members,
          },
        )
        batch.state = 'done'
        await catalog.load()
        message.value = (
          `“${batch.displayName}”已收录为课件文件夹，并生成本地课时树建议；`
          + '没有调用大模型，请在下方核对后确认。'
        )
      } catch (error) {
        batch.state = 'failed'
        batch.error = failureMessage(error)
      }
    }
  }

  async function retryPptCollection(batch: PendingPptFolderBatch): Promise<void> {
    batch.state = 'pending'
    batch.error = ''
    await createCompletedPptCollections()
  }

  return {
    pendingImports,
    pendingPptFolders,
    importBatchRunning,
    pendingImportCount,
    message,
    fileSizeLabel,
    parseJobFor,
    progressPercent,
    importStateLabel,
    queueFiles,
    queuePptFolder,
    removePendingImport,
    clearFinishedImports,
    importQueuedFiles,
    cancelImport,
    createCompletedPptCollections,
    retryPptCollection,
  }
}

export type MaterialImportQueue = ReturnType<typeof useMaterialImportQueue>
