import { computed, ref, toRaw } from 'vue'
import { defineStore } from 'pinia'

import { isAmbiguousWriteError, ApiError } from '../api/errors'
import { createClientRequestToken } from '../api/config-workspace'
import {
  abandonTemplateSubmission,
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

export const useTemplateRegionStore = defineStore('template-regions', () => {
  const sessionId = ref<number | null>(null)
  const workspace = ref<RegionWorkspace | null>(null)
  const editorState = ref<EditorState>({ revision: 0, regions: [] })
  const loadState = ref<RegionLoadState>('idle')
  const saveState = ref<RegionSaveState>('idle')
  const uploadState = ref<TemplateUploadState>('idle')
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
    || (editorReady.value && !readOnly.value && editorState.value.revision > savedRevision))

  function resetForSession(id: number): number {
    clearTimeout(saveTimer)
    saveTimer = undefined
    generation += 1
    sessionId.value = id
    workspace.value = null
    editorState.value = { revision: 0, regions: [] }
    loadState.value = 'loading'
    saveState.value = 'idle'
    uploadState.value = 'idle'
    pendingUploadToken.value = null
    errorMessage.value = ''
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
    uploadState.value = 'uploading'
    errorMessage.value = ''
    try {
      await uploadTemplate(id, file, firstPageRole, token)
      pendingUploadToken.value = null
      uploadState.value = 'idle'
      await load(id)
    } catch (error) {
      if (isAmbiguousWriteError(error)) {
        uploadState.value = 'unknown'
        errorMessage.value = '上传结果尚未确认，请先核对本次上传，避免重复提交。'
      } else {
        pendingUploadToken.value = null
        uploadState.value = 'error'
        errorMessage.value = '样卷没有上传成功，原有样卷没有改变。'
      }
    }
  }

  async function reconcileUpload(): Promise<void> {
    const id = sessionId.value
    const token = pendingUploadToken.value
    if (id === null || token === null) return
    try {
      const submission = await fetchTemplateSubmission(id, token)
      if (submission.status === 'succeeded') {
        pendingUploadToken.value = null; uploadState.value = 'idle'; await load(id)
      } else if (submission.status === 'failed' || submission.status === 'replaced') {
        pendingUploadToken.value = null; uploadState.value = 'error'
      }
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) return
      try {
        await abandonTemplateSubmission(id, token)
        pendingUploadToken.value = null
        uploadState.value = 'idle'
        errorMessage.value = '服务确认未收到这次上传，可以重新提交。'
      } catch { /* keep the guard when arrival is still uncertain */ }
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
    const result = await commitRegions(id, {
      regions: editorState.value.regions as Region[],
      image_sizes: {
        front: [current.template.pages.front.width, current.template.pages.front.height],
        back: [current.template.pages.back.width, current.template.pages.back.height],
      },
      template_matches: true,
      expected_template_fingerprint: current.template.template_fingerprint,
    })
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
    pendingUploadToken, errorMessage, scoringConfigured, editorReady, readOnly,
    snapshotPending, draftChoiceRequired, hasUnsavedWork, load, updateEditor,
    flushDraft, upload, reconcileUpload, discardDraft, continueDraft,
    restartFromFormal, startEditingConfirmed, showPage, commit, retrySnapshot }
})
