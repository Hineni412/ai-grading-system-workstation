import { computed, ref, toRaw } from 'vue'
import { defineStore } from 'pinia'

import { isAmbiguousWriteError, ApiError } from '../api/errors'
import { createClientRequestToken } from '../api/config-workspace'
import {
  abandonTemplateSubmission,
  assignTemplatePageRole,
  commitRegions,
  discardRegionDraft,
  fetchRegionReadiness,
  fetchRegionWorkspace,
  fetchTemplateSubmission,
  retryRegionSnapshot,
  saveRegionDraft,
  uploadTemplate,
  type PageRole,
  type Region,
  type RegionCommitResponse,
  type RegionWorkspace,
} from '../api/template-regions'
import type { EditorState } from '../components/template-regions/TemplateRegionEditor.vue'

export type RegionLoadState = 'idle' | 'loading' | 'ready' | 'error'
export type RegionSaveState = 'idle' | 'saving' | 'saved' | 'conflict' | 'error'
export type TemplateUploadState = 'idle' | 'uploading' | 'unknown' | 'error'
export type TemplateAssignmentState = 'idle' | 'saving' | 'error'
export const TEMPLATE_REGION_UPLOAD_STORAGE_KEY = 'ai-grading:template-upload:v1'

interface PersistedTemplateUpload {
  sessionId: number
  requestToken: string
}

function readPersistedUpload(sessionId: number): string | null {
  const raw = localStorage.getItem(TEMPLATE_REGION_UPLOAD_STORAGE_KEY)
  if (raw === null) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error()
    const value = parsed as Record<string, unknown>
    if (Object.keys(value).sort().join(',') !== 'requestToken,sessionId'
      || !Number.isSafeInteger(value.sessionId) || Number(value.sessionId) < 1
      || typeof value.requestToken !== 'string'
      || !/^[0-9a-f]{32}$/.test(value.requestToken)) throw new Error()
    return Number(value.sessionId) === sessionId ? value.requestToken : null
  } catch {
    localStorage.removeItem(TEMPLATE_REGION_UPLOAD_STORAGE_KEY)
    return null
  }
}

function persistUpload(value: PersistedTemplateUpload): void {
  localStorage.setItem(TEMPLATE_REGION_UPLOAD_STORAGE_KEY, JSON.stringify(value))
}

function clearPersistedUpload(sessionId: number, requestToken: string): void {
  if (readPersistedUpload(sessionId) === requestToken) {
    localStorage.removeItem(TEMPLATE_REGION_UPLOAD_STORAGE_KEY)
  }
}

export const useTemplateRegionStore = defineStore('template-regions', () => {
  const sessionId = ref<number | null>(null)
  const workspace = ref<RegionWorkspace | null>(null)
  const editorState = ref<EditorState>({ revision: 0, regions: [] })
  const loadState = ref<RegionLoadState>('idle')
  const saveState = ref<RegionSaveState>('idle')
  const uploadState = ref<TemplateUploadState>('idle')
  const assignmentState = ref<TemplateAssignmentState>('idle')
  const pendingUploadToken = ref<string | null>(null)
  const errorMessage = ref('')
  const scoringConfigured = ref(false)
  const editorReady = ref(false)
  const editingConfirmed = ref(false)
  let generation = 0
  let savedRevision = 0
  let saveTimer: ReturnType<typeof setTimeout> | undefined
  let saveInFlight = false

  const readOnly = computed(() => workspace.value?.template_ready === true && !editingConfirmed.value)
  const snapshotPending = computed(() => workspace.value?.template.regions_snapshot_pending === true)
  const draftChoiceRequired = computed(() => workspace.value?.draft.status === 'compatible'
    && !editorReady.value)
  const hasUnsavedWork = computed(() => uploadState.value === 'uploading'
    || uploadState.value === 'unknown'
    || assignmentState.value === 'saving'
    || (editorReady.value && !readOnly.value && editorState.value.revision > savedRevision))

  function resetForSession(id: number): number {
    const restoredUploadToken = readPersistedUpload(id)
    clearTimeout(saveTimer)
    saveTimer = undefined
    generation += 1
    sessionId.value = id
    workspace.value = null
    editorState.value = { revision: 0, regions: [] }
    loadState.value = 'loading'
    saveState.value = 'idle'
    uploadState.value = restoredUploadToken === null ? 'idle' : 'unknown'
    assignmentState.value = 'idle'
    pendingUploadToken.value = restoredUploadToken
    errorMessage.value = restoredUploadToken === null
      ? ''
      : '上传结果尚未确认，请先核对本次上传，避免重复提交。'
    scoringConfigured.value = false
    editorReady.value = false
    editingConfirmed.value = false
    savedRevision = 0
    return generation
  }

  function applyWorkspace(value: RegionWorkspace): void {
    workspace.value = value
    editingConfirmed.value = false
    const blockedDraft = ['compatible', 'incompatible', 'corrupt'].includes(value.draft.status)
    editorReady.value = !blockedDraft
    const regions = value.formal_regions
    savedRevision = 0
    editorState.value = {
      revision: savedRevision,
      active_page: 'front',
      regions: structuredClone(regions),
    }
    loadState.value = 'ready'
  }

  async function load(id: number): Promise<void> {
    const requestGeneration = resetForSession(id)
    try {
      const readiness = await fetchRegionReadiness(id)
      if (requestGeneration !== generation || sessionId.value !== id) return
      scoringConfigured.value = readiness.scoring_configured
      if (!readiness.template_present) {
        loadState.value = 'ready'
        return
      }
      const response = await fetchRegionWorkspace(id)
      if (requestGeneration === generation && sessionId.value === id) applyWorkspace(response)
    } catch {
      if (requestGeneration === generation && sessionId.value === id) {
        loadState.value = 'error'
        errorMessage.value = '样卷工作区暂时无法读取，当前数据没有改变。'
      }
    }
  }

  function updateEditor(value: EditorState): void {
    if (!editorReady.value || readOnly.value || saveState.value === 'conflict') return
    editorState.value = structuredClone(value)
    saveState.value = 'idle'
    clearTimeout(saveTimer)
    saveTimer = setTimeout(() => { void flushDraft() }, 500)
  }

  async function flushDraft(): Promise<void> {
    clearTimeout(saveTimer)
    saveTimer = undefined
    const id = sessionId.value
    const current = workspace.value
    if (id === null || current === null || readOnly.value || saveInFlight
      || editorState.value.revision <= savedRevision || saveState.value === 'conflict') return
    const requestGeneration = generation
    const sent = structuredClone(toRaw(editorState.value))
    saveInFlight = true
    saveState.value = 'saving'
    try {
      const response = await saveRegionDraft(id, {
        revision: sent.revision,
        regions: sent.regions as Region[],
        expected_template_fingerprint: current.template.template_fingerprint,
        expected_revision: savedRevision,
      })
      if (requestGeneration !== generation || sessionId.value !== id) return
      savedRevision = response.draft?.revision ?? sent.revision
      saveState.value = 'saved'
    } catch (error) {
      if (requestGeneration !== generation || sessionId.value !== id) return
      saveState.value = error instanceof ApiError && error.status === 409 ? 'conflict' : 'error'
      errorMessage.value = saveState.value === 'conflict'
        ? '草稿已在另一处变化。为避免覆盖，自动保存已停止，请刷新后继续。'
        : '草稿暂时没有保存，画框仍保留在当前页面。'
    } finally {
      saveInFlight = false
      if (requestGeneration === generation && editorState.value.revision > savedRevision
        && saveState.value !== 'error') {
        saveTimer = setTimeout(() => { void flushDraft() }, 0)
      }
    }
  }

  async function upload(file: File, firstPageRole: PageRole): Promise<void> {
    const id = sessionId.value
    if (id === null || !scoringConfigured.value
      || uploadState.value === 'uploading' || uploadState.value === 'unknown') return
    const token = createClientRequestToken()
    pendingUploadToken.value = token
    persistUpload({ sessionId: id, requestToken: token })
    uploadState.value = 'uploading'
    errorMessage.value = ''
    try {
      await uploadTemplate(id, file, firstPageRole, token)
      clearPersistedUpload(id, token)
      pendingUploadToken.value = null
      uploadState.value = 'idle'
      await load(id)
    } catch (error) {
      if (isAmbiguousWriteError(error) || (error instanceof ApiError && error.kind === 'server')) {
        uploadState.value = 'unknown'
        errorMessage.value = '上传结果尚未确认，请先核对本次上传，避免重复提交。'
      } else {
        clearPersistedUpload(id, token)
        pendingUploadToken.value = null
        uploadState.value = 'error'
        errorMessage.value = '样卷没有上传成功，原有样卷没有改变。'
      }
    }
  }

  async function assignFirstPageRole(firstPageRole: PageRole): Promise<void> {
    const id = sessionId.value
    const current = workspace.value
    if (id === null || current === null || assignmentState.value === 'saving'
      || uploadState.value === 'uploading' || uploadState.value === 'unknown') return
    await flushDraft()
    if (saveInFlight || ['error', 'conflict'].includes(saveState.value)) {
      errorMessage.value = saveInFlight
        ? '草稿仍在保存，请稍后再交换当前样卷正反面。'
        : '请先解决草稿保存问题，再交换当前样卷正反面。'
      return
    }
    const previousActivePage = editorState.value.active_page ?? 'front'
    assignmentState.value = 'saving'
    errorMessage.value = ''
    try {
      const result = await assignTemplatePageRole(
        id,
        firstPageRole,
        current.template.template_fingerprint,
      )
      if (result.draft_sync_pending) {
        assignmentState.value = 'error'
        errorMessage.value = '正反面已经交换，但草稿同步尚未完成。请再次点击“交换当前正反面”完成恢复。'
        return
      }
      const roleChangedForClient = (
        result.template.first_page_role !== current.template.first_page_role
      )
      await load(id)
      if (loadState.value !== 'ready' || workspace.value === null) {
        assignmentState.value = 'error'
        errorMessage.value = '正反面已经更新，请重新读取页面查看结果。'
        return
      }
      if (workspace.value.draft.status === 'compatible') continueDraft()
      if (roleChangedForClient && editorReady.value) {
        showPage(previousActivePage === 'front' ? 'back' : 'front')
      }
      assignmentState.value = 'idle'
      errorMessage.value = ''
    } catch (error) {
      assignmentState.value = 'error'
      errorMessage.value = isAmbiguousWriteError(error)
        ? '正反面交换结果尚未确认；再次点击同一按钮会安全核对，不会重复翻转。'
        : error instanceof ApiError && error.code === 'template_page_assignment_draft_conflict'
          ? '现有草稿与样卷不一致，请先处理旧草稿，再交换当前样卷正反面。'
          : error instanceof ApiError && error.code === 'template_page_assignment_unsupported'
            ? '这份旧式样卷不能直接交换，请重新上传 PDF 并指定第一页对应关系。'
            : error instanceof ApiError && error.code === 'template_page_assignment_changed'
              ? '样卷已被其他操作更新。系统没有重复写入，请重新读取页面后再决定是否交换。'
              : error instanceof ApiError && error.code === 'answer_region_lock_timeout'
                ? '当前样卷正在被另一项操作使用。系统没有重复写入，请稍后再试。'
                : '当前样卷正反面没有更新，系统没有自动重试。请稍后再试。'
    }
  }

  async function reconcileUpload(): Promise<void> {
    const id = sessionId.value
    const token = pendingUploadToken.value
    if (id === null || token === null) return
    try {
      const submission = await fetchTemplateSubmission(id, token)
      if (submission.status === 'succeeded') {
        clearPersistedUpload(id, token)
        pendingUploadToken.value = null; uploadState.value = 'idle'; await load(id)
      } else if (submission.status === 'failed' || submission.status === 'replaced') {
        clearPersistedUpload(id, token)
        pendingUploadToken.value = null; uploadState.value = 'error'
      } else if (submission.status === 'abandoned') {
        clearPersistedUpload(id, token)
        pendingUploadToken.value = null; uploadState.value = 'idle'
        errorMessage.value = '服务确认这次上传未生效，可以重新提交。'
      } else if (submission.status === 'processing') {
        await abandonInactiveUpload(id, token)
      }
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) return
      await abandonInactiveUpload(id, token)
    }
  }

  async function abandonInactiveUpload(id: number, token: string): Promise<void> {
    try {
      await abandonTemplateSubmission(id, token)
      clearPersistedUpload(id, token)
      pendingUploadToken.value = null
      uploadState.value = 'idle'
      errorMessage.value = '服务确认未收到这次上传，可以重新提交。'
    } catch {
      errorMessage.value = '上传仍在处理中，请稍后再次核对。'
    }
  }

  async function discardDraft(): Promise<void> {
    if (sessionId.value === null) return
    const id = sessionId.value
    await discardRegionDraft(id)
    await load(id)
  }

  function continueDraft(): void {
    const current = workspace.value
    if (current?.draft.status !== 'compatible') return
    savedRevision = current.draft.revision
    editorState.value = { revision: savedRevision, active_page: 'front',
      regions: structuredClone(toRaw(current.draft.regions)) }
    editorReady.value = true
    saveState.value = 'saved'
  }

  async function restartFromFormal(): Promise<void> {
    await discardDraft()
  }

  async function startEditingConfirmed(): Promise<void> {
    const current = workspace.value
    if (!current?.template_ready) return
    editingConfirmed.value = true
    editorReady.value = true
    savedRevision = 0
    editorState.value = { revision: 1, active_page: 'front', regions:
      structuredClone(toRaw(current.formal_regions)).map((region) => ({
        ...region, is_confirmed: false,
      })) }
    await flushDraft()
  }

  function showPage(page: PageRole): void {
    if (!editorReady.value) return
    editorState.value = { ...editorState.value, active_page: page }
  }

  async function commit(): Promise<RegionCommitResponse | null> {
    const id = sessionId.value
    const current = workspace.value
    if (id === null || current === null || saveState.value === 'conflict') return null
    await flushDraft()
    if (['error', 'conflict'].includes(saveState.value)) return null
    let result: RegionCommitResponse
    try {
      result = await commitRegions(id, {
        regions: editorState.value.regions as Region[],
        image_sizes: {
          front: [current.template.pages.front.width, current.template.pages.front.height],
          back: [current.template.pages.back.width, current.template.pages.back.height],
        },
        template_matches: true,
        expected_template_fingerprint: current.template.template_fingerprint,
      })
    } catch (error) {
      errorMessage.value = error instanceof ApiError && error.code === 'answer_region_lock_timeout'
        ? '当前考试正在被另一项操作使用，请稍后重试完成标定。'
        : '题框暂时无法确认，草稿仍已保留，请稍后重试。'
      return null
    }
    if (result.committed) await load(id)
    else {
      workspace.value = { ...current, issues: result.issues }
      editorState.value = { ...editorState.value, drawer_open: true }
    }
    return result
  }

  async function retrySnapshot(): Promise<void> {
    if (sessionId.value === null || workspace.value === null) return
    const id = sessionId.value
    await retryRegionSnapshot(id, workspace.value.template.template_fingerprint)
    await load(id)
  }

  return { sessionId, workspace, editorState, loadState, saveState, uploadState,
    assignmentState,
    pendingUploadToken, errorMessage, scoringConfigured, editorReady, readOnly,
    snapshotPending, draftChoiceRequired, hasUnsavedWork, load, updateEditor,
    flushDraft, upload, assignFirstPageRole, reconcileUpload, discardDraft, continueDraft,
    restartFromFormal, startEditingConfirmed, showPage, commit, retrySnapshot }
})
