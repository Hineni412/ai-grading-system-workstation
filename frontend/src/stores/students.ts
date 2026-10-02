import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  studentRosterApi,
  type StudentDeletionImpact,
  type StudentImportMapping,
  type StudentImportPreview,
  type StudentSummary,
  type StudentUpsertInput,
  type StudentWorkspace,
  type StudentWorkspaceQuery,
} from '../api/students'

export type StudentRosterApi = typeof studentRosterApi
export type StudentRosterLoadState = 'idle' | 'loading' | 'ready' | 'empty' | 'error'
export type StudentImportState =
  | 'idle'
  | 'previewing'
  | 'previewed'
  | 'committing'
  | 'committed'
  | 'conflict'
  | 'error'

function safeRosterMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === 'student_roster_conflict') {
    return '学生名单已经变化，请重新预览或刷新后再操作。'
  }
  return '学生名单暂时无法更新，请稍后重试。'
}

function safeImportMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'student_roster_conflict') {
      return '名单已经变化，请重新预览后再确认导入。'
    }
    if (error.code === 'student_roster_unreadable') {
      return '无法读取这个名单文件，请检查是否为有效的 CSV 或 XLSX 文件。'
    }
  }
  return '名单预览失败，请检查文件内容后重试。'
}

function safeDeleteMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === 'student_grading_active') {
    return '批改仍在进行，请等待批改结束后再删除学生。'
  }
  if (error instanceof ApiError && error.code === 'student_backup_failed') {
    return '备份没有成功，系统已停止删除，学生数据保持不变。'
  }
  if (error instanceof ApiError && error.code === 'student_roster_conflict') {
    return '名单已经变化，请重新查看删除影响后再确认。'
  }
  return '删除没有完成，学生数据保持不变。'
}

export const useStudentRosterStore = defineStore('student-roster', () => {
  const workspace = ref<StudentWorkspace | null>(null)
  const loadState = ref<StudentRosterLoadState>('idle')
  const importState = ref<StudentImportState>('idle')
  const errorMessage = ref('')
  const noticeMessage = ref('')
  const search = ref('')
  const className = ref('')
  const page = ref(1)
  const pageSize = ref(50)
  const selectedStudentId = ref<number | null>(null)
  const preview = ref<StudentImportPreview | null>(null)
  const importMapping = ref<StudentImportMapping>({
    student_code: null,
    name: null,
    class_name: null,
  })
  const selectedSourceRows = ref<number[]>([])
  const deletionImpact = ref<StudentDeletionImpact | null>(null)
  const deletionState = ref<'idle' | 'loading' | 'ready' | 'deleting' | 'error'>('idle')

  let loadGeneration = 0
  let loadController: AbortController | null = null
  let previewController: AbortController | null = null
  let deletionGeneration = 0

  const selectedStudent = computed<StudentSummary | null>(() => (
    workspace.value?.items.find(({ id }) => id === selectedStudentId.value) ?? null
  ))

  const selectedImportRows = computed(() => {
    const selected = new Set(selectedSourceRows.value)
    return preview.value?.rows.filter(
      (row) => row.selectable && selected.has(row.source_row),
    ) ?? []
  })

  async function load(
    query: StudentWorkspaceQuery = {},
    api: StudentRosterApi = studentRosterApi,
  ): Promise<void> {
    loadController?.abort()
    const controller = new AbortController()
    loadController = controller
    const generation = ++loadGeneration
    search.value = query.search ?? search.value
    className.value = query.class_name ?? className.value
    page.value = query.page ?? page.value
    pageSize.value = query.page_size ?? pageSize.value
    loadState.value = 'loading'
    errorMessage.value = ''
    try {
      const result = await api.getWorkspace({
        search: search.value,
        class_name: className.value,
        page: page.value,
        page_size: pageSize.value,
      }, controller.signal)
      if (generation !== loadGeneration || controller.signal.aborted) return
      workspace.value = result
      loadState.value = result.total === 0 ? 'empty' : 'ready'
      if (
        selectedStudentId.value !== null
        && !result.items.some(({ id }) => id === selectedStudentId.value)
      ) {
        selectedStudentId.value = null
        deletionImpact.value = null
        deletionState.value = 'idle'
      }
    } catch (error) {
      if (generation !== loadGeneration || controller.signal.aborted) return
      loadState.value = 'error'
      errorMessage.value = safeRosterMessage(error)
    } finally {
      if (loadController === controller) loadController = null
    }
  }

  function selectStudent(studentId: number | null): void {
    if (deletionState.value === 'deleting') return
    deletionGeneration += 1
    selectedStudentId.value = studentId
    deletionImpact.value = null
    deletionState.value = 'idle'
    errorMessage.value = ''
    if (studentId !== null) noticeMessage.value = ''
  }

  async function previewFile(
    file: File,
    mapping?: StudentImportMapping,
    api: StudentRosterApi = studentRosterApi,
  ): Promise<void> {
    previewController?.abort()
    const controller = new AbortController()
    previewController = controller
    importState.value = 'previewing'
    errorMessage.value = ''
    noticeMessage.value = ''
    const requestedMapping = mapping ?? importMapping.value
    try {
      const result = await api.previewImport(file, requestedMapping, controller.signal)
      if (controller.signal.aborted) return
      preview.value = result
      importMapping.value = { ...result.mapping }
      selectedSourceRows.value = result.rows
        .filter(({ selectable }) => selectable)
        .map(({ source_row }) => source_row)
      importState.value = 'previewed'
    } catch (error) {
      if (controller.signal.aborted) return
      importState.value = 'error'
      errorMessage.value = safeImportMessage(error)
    } finally {
      if (previewController === controller) previewController = null
    }
  }

  function toggleImportRow(sourceRow: number, selected: boolean): void {
    const rows = new Set(selectedSourceRows.value)
    if (selected) rows.add(sourceRow)
    else rows.delete(sourceRow)
    selectedSourceRows.value = [...rows]
  }

  async function commitPreview(
    api: StudentRosterApi = studentRosterApi,
  ): Promise<void> {
    if (!preview.value || selectedImportRows.value.length === 0) return
    importState.value = 'committing'
    errorMessage.value = ''
    noticeMessage.value = ''
    try {
      const result = await api.commitImport(
        preview.value.roster_revision,
        selectedImportRows.value.map((row) => ({
          student_code: row.student_code,
          name: row.name,
          class_name: row.class_name,
        })),
      )
      importState.value = 'committed'
      noticeMessage.value = `已导入 ${result.inserted} 名新学生，更新 ${result.updated} 名学生。`
      if (workspace.value) workspace.value.roster_revision = result.roster_revision
    } catch (error) {
      importState.value = (
        error instanceof ApiError && error.code === 'student_roster_conflict'
      ) ? 'conflict' : 'error'
      errorMessage.value = safeImportMessage(error)
    }
  }

  async function saveStudent(
    values: StudentUpsertInput,
    api: StudentRosterApi = studentRosterApi,
  ): Promise<boolean> {
    if (!selectedStudent.value || !workspace.value) return false
    errorMessage.value = ''
    noticeMessage.value = ''
    try {
      const result = await api.updateStudent(
        selectedStudent.value.id,
        workspace.value.roster_revision,
        values,
      )
      const index = workspace.value.items.findIndex(({ id }) => id === result.student.id)
      if (index >= 0) workspace.value.items[index] = result.student
      workspace.value.roster_revision = result.roster_revision
      noticeMessage.value = '学生信息已保存。'
      return true
    } catch (error) {
      errorMessage.value = safeRosterMessage(error)
      return false
    }
  }

  async function loadDeletionImpact(
    api: StudentRosterApi = studentRosterApi,
  ): Promise<void> {
    if (!selectedStudent.value) return
    const studentId = selectedStudent.value.id
    const generation = ++deletionGeneration
    deletionState.value = 'loading'
    errorMessage.value = ''
    try {
      const result = await api.getDeletionImpact(studentId)
      if (
        generation !== deletionGeneration
        || selectedStudentId.value !== studentId
        || result.student.id !== studentId
      ) return
      deletionImpact.value = result
      deletionState.value = 'ready'
    } catch (error) {
      if (generation !== deletionGeneration || selectedStudentId.value !== studentId) return
      deletionState.value = 'error'
      errorMessage.value = safeDeleteMessage(error)
    }
  }

  async function deleteSelected(
    api: StudentRosterApi = studentRosterApi,
  ): Promise<boolean> {
    if (
      !selectedStudent.value
      || !deletionImpact.value
      || deletionImpact.value.student.id !== selectedStudent.value.id
    ) return false
    deletionState.value = 'deleting'
    errorMessage.value = ''
    noticeMessage.value = ''
    try {
      const studentId = selectedStudent.value.id
      const result = await api.deleteStudent(
        studentId,
        deletionImpact.value.roster_revision,
      )
      if (workspace.value) {
        workspace.value.items = workspace.value.items.filter(({ id }) => id !== studentId)
        workspace.value.total = Math.max(0, workspace.value.total - 1)
        workspace.value.roster_revision = result.roster_revision
      }
      selectedStudentId.value = null
      deletionImpact.value = null
      deletionState.value = 'idle'
      noticeMessage.value = [
        '学生及关联记录已安全删除，备份已完成：',
        `${result.deleted_students} 名学生、`,
        `${result.deleted_results} 份成绩、`,
        `${result.deleted_details} 条评分明细、`,
        `${result.deleted_annotations} 条批注、`,
        `${result.deleted_attendance} 条考勤；`,
        `已解除 ${result.unlinked_papers} 份答卷关联。`,
      ].join('')
      return true
    } catch (error) {
      deletionState.value = 'error'
      errorMessage.value = safeDeleteMessage(error)
      return false
    }
  }

  function resetImport(): void {
    previewController?.abort()
    previewController = null
    preview.value = null
    selectedSourceRows.value = []
    importState.value = 'idle'
    importMapping.value = {
      student_code: null,
      name: null,
      class_name: null,
    }
  }

  return {
    workspace,
    loadState,
    importState,
    errorMessage,
    noticeMessage,
    search,
    className,
    page,
    pageSize,
    selectedStudentId,
    selectedStudent,
    preview,
    importMapping,
    selectedSourceRows,
    selectedImportRows,
    deletionImpact,
    deletionState,
    load,
    selectStudent,
    previewFile,
    toggleImportRow,
    commitPreview,
    saveStudent,
    loadDeletionImpact,
    deleteSelected,
    resetImport,
  }
})
