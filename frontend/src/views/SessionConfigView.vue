<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import ConfigStageRail from '../components/config/ConfigStageRail.vue'
import ConfigSourceUpload from '../components/config/ConfigSourceUpload.vue'
import ConfigGenerationPanel from '../components/config/ConfigGenerationPanel.vue'
import ConfigSaveResult from '../components/config/ConfigSaveResult.vue'
import QuestionBlockReview from '../components/config/QuestionBlockReview.vue'
import RubricEditorTable from '../components/config/RubricEditorTable.vue'
import ScoringUnitEditor from '../components/config/ScoringUnitEditor.vue'
import SessionDraftPanel from '../components/config/SessionDraftPanel.vue'
import { ApiError, isAmbiguousWriteError, isAuthoritativeNotFoundError } from '../api/errors'
import {
  abandonConfigGenerationRequest,
  createClientRequestToken,
  fetchConfigEditor,
  fetchConfigGenerationJobByToken,
  refineConfigEditor,
  saveConfigEditor,
  type ConfigEditorCommand,
  type ConfigEditorRefineRequest,
  type ConfigEditorResponse,
  type ConfigEditorSaveRequest,
  type ConfigEditorSaveResponse,
  type ManualPartInput,
} from '../api/config-workspace'
import type { JobResponse } from '../api/jobs'
import { fetchRegionReadiness, type RegionReadiness } from '../api/template-regions'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useJobStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'

const props = withDefaults(defineProps<{
  editorSaver?: (sessionId: number, request: ConfigEditorSaveRequest) => Promise<ConfigEditorSaveResponse>
  editorLoader?: (sessionId: number) => Promise<ConfigEditorResponse>
  editorRefiner?: (sessionId: number, request: ConfigEditorRefineRequest) => Promise<JobResponse>
  generationLoader?: (sessionId: number, requestToken: string) => Promise<JobResponse>
  requestAbandoner?: (sessionId: number, requestToken: string) => Promise<void>
  templateReadinessLoader?: (sessionId: number) => Promise<RegionReadiness>
}>(), {
  editorSaver: saveConfigEditor,
  editorLoader: fetchConfigEditor,
  editorRefiner: refineConfigEditor,
  generationLoader: fetchConfigGenerationJobByToken,
  requestAbandoner: abandonConfigGenerationRequest,
  templateReadinessLoader: fetchRegionReadiness,
})

const sessionStore = useSessionStore()
const configStore = useConfigWorkspaceStore()
const jobStore = useJobStore()
const selectedScoringQuestion = ref('')
const refining = ref(false)
const refineError = ref('')
const rubricInputValid = ref(true)
const templatePresent = ref(false)
const templateReady = ref(false)
let templateLoadGeneration = 0

const saving = computed(() => configStore.saveStatus === 'saving')
const saveUnknown = computed(() => configStore.saveStatus === 'unknown')
const submissionPending = computed(() => configStore.hasPendingSubmission)
const configJobActive = computed(() => {
  const current = configStore.jobId === null ? null : jobStore.jobs[configStore.jobId]
  return current?.job_type === 'config_generation'
    && current.payload.session_id === configStore.sessionId
    && !['succeeded', 'failed', 'cancelled'].includes(current.status)
})
const editorIssues = computed(() => [
  ...(configStore.editor?.issues ?? []),
  ...configStore.serverIssues,
])
const blockingIssues = computed(() => editorIssues.value
  .some((issue) => issue.severity === 'error'))
const saveBlocked = computed(() => configStore.effectiveTotalScore !== 100
  || blockingIssues.value || !rubricInputValid.value)
const scoringQuestions = computed(() => [...new Set(
  configStore.effectiveEditorRows.map((row) => row.question_id),
)])
const activeScoringQuestion = computed(() => {
  if (scoringQuestions.value.includes(selectedScoringQuestion.value)) return selectedScoringQuestion.value
  return scoringQuestions.value[0] ?? ''
})
const activeParts = computed<ManualPartInput[]>(() => {
  const byPart = new Map<string, ManualPartInput>()
  for (const row of configStore.effectiveEditorRows) {
    if (row.question_id !== activeScoringQuestion.value) continue
    const current = byPart.get(row.part_id)
    if (current) current.score += row.score
    else byPart.set(row.part_id, {
      part_id: row.part_id, score: row.score, core_goal: row.core_goal,
    })
  }
  return [...byPart.values()]
})
const templateAction = computed(() => templateReady.value ? '查看已确认版本'
  : templatePresent.value ? '继续标定' : '准备样卷')
const templateSummary = computed(() => templateReady.value
  ? '题框已确认，可查看正式版本。'
  : templatePresent.value ? '样卷已上传，题框标定尚未确认。'
    : '尚未上传样卷，请准备双页 PDF 后开始标定。')

watch(() => sessionStore.currentSession?.id ?? null, async (sessionId) => {
  const generation = ++templateLoadGeneration
  templatePresent.value = false
  templateReady.value = false
  if (sessionId === null) return
  try {
    const readiness = await props.templateReadinessLoader(sessionId)
    if (generation !== templateLoadGeneration
      || sessionStore.currentSession?.id !== sessionId) return
    templatePresent.value = readiness.template_present
    templateReady.value = readiness.template_ready
  } catch { /* keep the conservative not-started state without exposing details */ }
}, { immediate: true })

function confirmSourceUpload(): boolean {
  if (!configStore.hasDirtyEditor) return true
  return window.confirm('替换试卷会在新文件接收成功后清除尚未保存的评分依据修改。是否继续？')
}

function sameValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function editorReflectsRequest(
  response: ConfigEditorResponse,
  request: ConfigEditorSaveRequest,
): boolean {
  if (response.revision === request.revision || request.commands.length > 0) return false
  const rows = new Map(response.rows.map((row) => [row.row_id, row]))
  return request.edits.every((edit) => {
    const row = rows.get(edit.row_id)
    if (!row) return false
    return Object.entries(edit).every(([field, value]) => field === 'row_id'
      || sameValue(row[field as keyof typeof row], value))
  })
}

async function saveEditor(): Promise<void> {
  if (configStore.sessionId === null || !configStore.hasDirtyEditor || saveBlocked.value
    || configJobActive.value || !configStore.beginSave()) return
  const sessionId = configStore.sessionId
  const request = configStore.buildSaveRequest()
  const context = configStore.captureEditorContext()
  try {
    const response = await props.editorSaver(sessionId, request)
    if (!configStore.isEditorContextCurrent(context)) return
    configStore.replaceWithAuthoritativeEditor(response)
  } catch (error) {
    if (!configStore.isEditorContextCurrent(context)) return
    if (error instanceof ApiError
      && (error.code === 'config_revision_conflict' || error.status === 409)) {
      configStore.markConflict()
    } else if (isAmbiguousWriteError(error)) {
      try {
        const authoritative = await props.editorLoader(sessionId)
        if (!configStore.isEditorContextCurrent(context)) return
        if (editorReflectsRequest(authoritative, request)) {
          configStore.replaceWithReconciledEditor(authoritative)
        } else if (authoritative.revision === request.revision) {
          configStore.noteSaveFailed()
        } else {
          configStore.markConflict()
        }
      } catch {
        if (configStore.isEditorContextCurrent(context)) configStore.noteSaveUnknown()
      }
    } else if (configStore.recordServerIssues(error)) {
      configStore.noteSaveFailed()
    } else {
      configStore.noteSaveFailed()
    }
  }
}

async function reloadLatestEditor(): Promise<void> {
  const sessionId = configStore.sessionId
  if (sessionId === null) return
  const context = configStore.captureEditorContext()
  try {
    const response = await props.editorLoader(sessionId)
    if (configStore.isEditorContextCurrent(context)) configStore.setEditor(response)
  } catch {
    if (configStore.isEditorContextCurrent(context)) configStore.noteSaveFailed()
  }
}

function queueCommand(command: ConfigEditorCommand): void {
  configStore.addEditorCommand(command)
}

async function refineScoringUnits(command: ConfigEditorCommand): Promise<void> {
  if (configStore.sessionId === null || configStore.editor === null
    || configStore.hasDirtyEditor || refining.value || submissionPending.value
    || configJobActive.value) return
  const sessionId = configStore.sessionId
  const context = configStore.captureGenerationContext()
  const requestToken = createClientRequestToken()
  if (!configStore.markJobSubmissionPending(requestToken, 'refine')) return
  refining.value = true
  refineError.value = ''
  try {
    const job = await props.editorRefiner(sessionId, {
      revision: configStore.editor.revision,
      commands: [command],
      client_request_token: requestToken,
    })
    jobStore.track(job)
    configStore.attachJob(job.id, context)
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      refineError.value = 'AI 完善任务结果未知，正在核对这一次任务…'
      try {
        const reconciled = await props.generationLoader(sessionId, requestToken)
        jobStore.track(reconciled)
        configStore.attachJob(reconciled.id, context)
        refineError.value = ''
      } catch (reconciliationError) {
        if (isAuthoritativeNotFoundError(reconciliationError, 'config_generation_job_not_found')) {
          try {
            await props.requestAbandoner(sessionId, requestToken)
            configStore.clearGenerationSubmissionPending()
            refineError.value = '服务器确认未收到这次 AI 完善请求，可以重新提交。'
          } catch {
            refineError.value = '原 AI 完善请求可能仍在到达服务器，当前继续锁定。请稍后再次核对。'
          }
        } else {
          refineError.value = 'AI 完善任务结果仍无法确认。为避免重复生成，请在生成区重新核对。'
        }
      }
    } else {
      configStore.clearGenerationSubmissionPending()
      refineError.value = 'AI 完善任务未提交，当前评分依据没有改变。'
    }
  } finally {
    refining.value = false
  }
}
</script>

<template>
  <article class="session-config-view config-workspace">
    <header class="session-config-view__header">
      <div>
        <h1 tabindex="-1">考试配置</h1>
        <p>从考试草稿开始，按阶段准备试卷来源和评分依据。</p>
      </div>
      <span v-if="sessionStore.currentSession" class="session-config-view__current">
        当前考试：{{ sessionStore.currentSession.name }}
      </span>
    </header>

    <div v-if="sessionStore.loadState === 'loading'" class="session-config-view__state" role="status">
      正在读取考试列表…
    </div>
    <div v-else-if="sessionStore.loadState === 'error'" class="session-config-view__state" role="alert">
      <p>考试列表暂时无法读取，尚未改变任何考试。</p>
      <button type="button" @click="sessionStore.initialize()">重新加载考试列表</button>
    </div>
    <template v-else>
      <ConfigStageRail
        :phase="configStore.phase"
        :session-ready="sessionStore.currentSession !== null"
        :source-ready="configStore.sourceId !== null && configStore.sourceRevision !== null"
        :generation-submitted="configStore.jobId !== null"
        :editor-ready="configStore.editor?.configured === true"
        :template-present="templatePresent"
        :template-ready="templateReady"
      />
      <p v-if="sessionStore.sessions.length === 0" class="session-config-view__empty">
        还没有考试。创建草稿后，可以继续上传试卷并准备评分依据。
      </p>
      <SessionDraftPanel />
      <template v-if="sessionStore.currentSession">
        <ConfigSourceUpload
          :session-id="sessionStore.currentSession.id"
          :source="configStore.source"
          :before-upload="confirmSourceUpload"
          @uploaded="configStore.acceptUploadedSource"
        />
        <QuestionBlockReview
          v-if="configStore.source"
          :source="configStore.source"
          :decisions="configStore.decisions"
          @update:decisions="configStore.updateDecisions"
        />
        <ConfigGenerationPanel
          v-if="configStore.source || configStore.pendingJobRequestToken !== null || configStore.jobId !== null"
        />
        <section v-if="configStore.editor?.configured" class="config-editor" aria-label="评分依据工作区">
          <RubricEditorTable
            :rows="configStore.effectiveEditorRows"
            :total-score="configStore.effectiveTotalScore"
            :issues="editorIssues"
            :disabled="saving || refining || configJobActive || submissionPending"
            @edit="configStore.updateEditor"
            @validity="rubricInputValid = $event"
          />

          <details v-if="scoringQuestions.length" class="config-editor__units">
            <summary>调整评分单元</summary>
            <label class="config-editor__question-picker">
              <span>选择题号</span>
              <select v-model="selectedScoringQuestion" aria-label="评分单元题号">
                <option v-for="questionId in scoringQuestions" :key="questionId" :value="questionId">
                  {{ questionId }}
                </option>
              </select>
            </label>
            <ScoringUnitEditor
              v-if="activeScoringQuestion"
              :question-id="activeScoringQuestion"
              :parts="activeParts"
              :disabled="saving || refining || configJobActive || submissionPending"
              @command="queueCommand"
              @refine="refineScoringUnits"
            />
            <p v-if="configStore.hasDirtyEditor" class="config-editor__refine-note">
              如需 AI 完善，请先保存当前本地修改。
            </p>
            <p v-if="refineError" class="config-editor__refine-error" role="alert">{{ refineError }}</p>
          </details>

          <div class="config-editor__save-bar">
            <div>
              <strong>{{ configStore.hasDirtyEditor ? '有未保存修改' : '已与服务器版本同步' }}</strong>
              <span v-if="saveBlocked">需处理阻断问题并使总分为 100 后保存。</span>
              <span v-else>保存时会一次提交全部行修改与评分单元命令。</span>
            </div>
            <button
              type="button"
              name="保存评分依据"
              class="config-editor__save-primary"
              :disabled="!configStore.hasDirtyEditor || saveBlocked || saving || saveUnknown || refining || configJobActive || submissionPending"
              @click="saveEditor"
            >{{ saving ? '正在保存…' : '保存评分依据' }}</button>
          </div>
          <ConfigSaveResult
            :status="configStore.saveStatus === 'saving' ? 'idle' : configStore.saveStatus"
            :mapping-status="configStore.mappingStatus"
            @reload="reloadLatestEditor"
          />
          <div class="config-template-entry">
            <div>
              <strong>下一步：样卷题框</strong>
              <span>{{ templateSummary }}</span>
            </div>
            <a :href="`/sessions/${sessionStore.currentSession.id}/regions`">{{ templateAction }}</a>
          </div>
          <div class="config-template-entry">
            <div>
              <strong>批改执行</strong>
              <span>{{ templateReady ? '样卷题框已确认，可以上传整班答卷并开始扫描预检。' : '完成样卷题框确认后即可开始整班批改。' }}</span>
            </div>
            <a v-if="templateReady" :href="`/sessions/${sessionStore.currentSession.id}/grading-run`">进入批改执行</a>
          </div>
        </section>
      </template>
    </template>
  </article>
</template>

<style scoped>
.session-config-view__header { display: flex; align-items: end; justify-content: space-between; gap: var(--space-5); margin-block-end: var(--space-5); }
.session-config-view h1,
.session-config-view p { margin: 0; }
.session-config-view h1 { font-size: var(--font-size-h1); line-height: var(--line-height-tight); }
.session-config-view__header p { margin-block-start: var(--space-1); color: var(--color-text-secondary); }
.session-config-view__current { max-width: 45%; overflow: hidden; color: var(--color-text-secondary); font-size: var(--font-size-dense); text-overflow: ellipsis; white-space: nowrap; }
.session-config-view__state,
.session-config-view__empty { padding: var(--space-5); border-block: var(--border-width) solid var(--color-border-default); background: var(--color-bg-subtle); color: var(--color-text-secondary); }
.session-config-view__state button { min-height: var(--control-height-default); margin-block-start: var(--space-3); padding-inline: var(--space-3); border: var(--border-width) solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); }
.session-config-view__empty { border-block-start: 0; }
.config-template-entry { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); margin-block-start: var(--space-5); padding: var(--space-4); border-block: var(--border-width) solid var(--color-border-default); background: var(--color-bg-subtle); }
.config-template-entry strong, .config-template-entry span { display: block; }
.config-template-entry span { margin-block-start: var(--space-1); color: var(--color-text-secondary); }
.config-template-entry a { min-height: var(--control-height-default); padding: var(--space-2) var(--space-3); border-radius: var(--radius-control); background: var(--color-accent); color: white; text-decoration: none; }
</style>
