<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  archiveSession,
  fetchArchivedSessions,
  fetchSessionDeletionImpact,
  permanentlyDeleteSession,
  restoreArchivedSession,
  type SessionDeletionImpact,
  type SessionSummary,
} from '../../api/sessions'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import { useSessionStore } from '../../stores/session'

type ActionState = 'idle' | 'loading' | 'ready' | 'working' | 'error' | 'unknown' | 'done'

const sessionStore = useSessionStore()
const archiveState = ref<ActionState>('idle')
const archiveImpact = ref<SessionDeletionImpact | null>(null)
const archiveConfirmation = ref('')
const archiveMessage = ref('')

const archivedSessions = ref<SessionSummary[]>([])
const archivedState = ref<'loading' | 'ready' | 'error'>('loading')
const selectedArchivedId = ref<number | null>(null)
const permanentState = ref<ActionState>('idle')
const permanentImpact = ref<SessionDeletionImpact | null>(null)
const permanentConfirmation = ref('')
const permanentMessage = ref('')

const selectedArchived = computed(() => (
  archivedSessions.value.find((session) => session.id === selectedArchivedId.value) ?? null
))
const canArchive = computed(() => (
  archiveState.value === 'ready'
  && archiveImpact.value?.can_archive === true
  && archiveConfirmation.value === archiveImpact.value.session.name
))
const canPermanentlyDelete = computed(() => (
  permanentState.value === 'ready'
  && permanentImpact.value?.can_permanently_delete === true
  && permanentConfirmation.value === permanentImpact.value.permanent_delete_phrase
))
const deletionRecordTotal = computed(() => (
  Object.values(permanentImpact.value?.permanent_counts ?? {})
    .reduce((total, count) => total + count, 0)
))

watch(
  () => sessionStore.currentSession?.id ?? null,
  () => {
    archiveState.value = 'idle'
    archiveImpact.value = null
    archiveConfirmation.value = ''
    archiveMessage.value = ''
  },
)

watch(selectedArchivedId, () => {
  permanentState.value = 'idle'
  permanentImpact.value = null
  permanentConfirmation.value = ''
  permanentMessage.value = ''
})

onMounted(() => {
  void loadArchivedSessions()
})

async function loadArchivedSessions(preferredId: number | null = null): Promise<void> {
  archivedState.value = 'loading'
  try {
    archivedSessions.value = await fetchArchivedSessions()
    const candidate = preferredId ?? selectedArchivedId.value
    selectedArchivedId.value = archivedSessions.value.some((item) => item.id === candidate)
      ? candidate
      : archivedSessions.value[0]?.id ?? null
    archivedState.value = 'ready'
  } catch {
    archivedState.value = 'error'
  }
}

async function reviewArchive(): Promise<void> {
  const sessionId = sessionStore.currentSession?.id
  if (sessionId === undefined) return
  archiveState.value = 'loading'
  archiveImpact.value = null
  archiveConfirmation.value = ''
  archiveMessage.value = ''
  try {
    const reviewed = await fetchSessionDeletionImpact(sessionId)
    if (sessionStore.currentSession?.id !== sessionId) return
    archiveImpact.value = reviewed
    archiveState.value = 'ready'
    if (!reviewed.can_archive) {
      archiveMessage.value = reviewed.active_jobs || reviewed.active_grading_runs
        ? '这场考试仍有生成、同步或批改任务未结束，请先完成或取消任务。'
        : '这场考试当前不能归档，请刷新后重试。'
    }
  } catch {
    archiveState.value = 'error'
    archiveMessage.value = '暂时无法核对这场考试的状态，没有执行归档。'
  }
}

function actionErrorMessage(error: unknown, permanent: boolean): string {
  if (!(error instanceof ApiError)) {
    return permanent ? '考试尚未永久删除，请重新核对后再试。' : '考试尚未归档，请重新核对后再试。'
  }
  if (error.code === 'session_delete_active_work') {
    return '这场考试刚刚开始了新的任务，请先完成或取消任务。'
  }
  if (error.code === 'session_delete_revision_conflict') {
    return '考试内容在确认期间发生变化，请重新查看影响。'
  }
  if (error.code === 'session_delete_confirmation_mismatch'
    || error.code === 'session_permanent_delete_confirmation_mismatch') {
    return '确认文字与当前考试不一致，请按页面提示重新输入。'
  }
  if (error.code === 'session_permanent_delete_requires_archive') {
    return '必须先归档考试，才能永久删除。'
  }
  if (error.code === 'session_permanent_delete_training_snapshot_exists') {
    return '已有训练任务保存了这场考试的诊断快照。为避免误删训练材料，当前已阻止永久删除。'
  }
  if (error.code === 'session_permanent_delete_storage_incomplete') {
    return '部分答卷或结果文件正被其他程序占用。考试仍保留在归档区；关闭占用文件后可安全重试。'
  }
  if (error.code === 'session_not_found') {
    return '没有找到这场考试，列表可能已经更新。'
  }
  return permanent ? '考试尚未永久删除，请稍后重试。' : '考试尚未归档，请稍后重试。'
}

async function submitArchive(): Promise<void> {
  if (!canArchive.value || archiveImpact.value === null) return
  const reviewed = archiveImpact.value
  archiveState.value = 'working'
  archiveMessage.value = ''
  try {
    await archiveSession(
      reviewed.session.id,
      reviewed.revision,
      archiveConfirmation.value,
    )
  } catch (error) {
    if (!isAmbiguousWriteError(error)) {
      archiveState.value = 'error'
      archiveMessage.value = actionErrorMessage(error, false)
      return
    }
    try {
      const reconciled = await fetchSessionDeletionImpact(reviewed.session.id)
      if (!reconciled.session.is_deleted) {
        archiveImpact.value = reconciled
        archiveState.value = 'error'
        archiveMessage.value = '已核对：考试尚未归档，可以重新确认。'
        return
      }
    } catch {
      archiveState.value = 'unknown'
      archiveMessage.value = '请求结果暂时无法确认。恢复连接后请刷新归档列表核对。'
      return
    }
  }
  sessionStore.clearSelection()
  await Promise.all([
    sessionStore.initialize(),
    loadArchivedSessions(reviewed.session.id),
  ])
  archiveState.value = 'done'
  archiveMessage.value = `“${reviewed.session.name}”已归档，所有考试数据仍保留并可恢复。`
}

async function reviewPermanentDeletion(): Promise<void> {
  const session = selectedArchived.value
  if (session === null) return
  permanentState.value = 'loading'
  permanentImpact.value = null
  permanentConfirmation.value = ''
  permanentMessage.value = ''
  try {
    const reviewed = await fetchSessionDeletionImpact(session.id)
    if (selectedArchivedId.value !== session.id) return
    permanentImpact.value = reviewed
    permanentState.value = 'ready'
    if (reviewed.blocking_training_tasks.length) {
      permanentMessage.value = '已有训练任务保存了这场考试的诊断快照。当前先阻止永久删除，避免误删训练材料。'
    } else if (!reviewed.can_permanently_delete) {
      permanentMessage.value = '这场考试仍有活动任务，暂时不能永久删除。'
    }
  } catch {
    permanentState.value = 'error'
    permanentMessage.value = '暂时无法读取永久删除清单，没有删除任何内容。'
  }
}

async function submitPermanentDeletion(): Promise<void> {
  if (!canPermanentlyDelete.value || permanentImpact.value === null) return
  const reviewed = permanentImpact.value
  permanentState.value = 'working'
  permanentMessage.value = ''
  try {
    await permanentlyDeleteSession(
      reviewed.session.id,
      reviewed.revision,
      permanentConfirmation.value,
    )
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      try {
        const latest = await fetchArchivedSessions()
        if (!latest.some((session) => session.id === reviewed.session.id)) {
          archivedSessions.value = latest
          selectedArchivedId.value = latest[0]?.id ?? null
          permanentState.value = 'done'
          permanentMessage.value = `“${reviewed.session.name}”已永久删除。`
          return
        }
      } catch {
        permanentState.value = 'unknown'
        permanentMessage.value = '请求结果暂时无法确认。恢复连接后请刷新归档列表；不要重复建立同名测试考试。'
        return
      }
    }
    permanentState.value = 'error'
    permanentMessage.value = actionErrorMessage(error, true)
    return
  }
  await loadArchivedSessions()
  permanentState.value = 'done'
  permanentMessage.value = `“${reviewed.session.name}”的答卷、批改结果和知识图谱贡献已永久删除。`
}

async function restoreSelectedArchive(): Promise<void> {
  const session = selectedArchived.value
  if (session === null || permanentState.value === 'working') return
  permanentState.value = 'working'
  permanentMessage.value = ''
  try {
    await restoreArchivedSession(session.id)
    await Promise.all([sessionStore.initialize(), loadArchivedSessions()])
    permanentState.value = 'done'
    permanentMessage.value = `“${session.name}”已恢复，可重新进入原流程。`
  } catch (error) {
    permanentState.value = 'error'
    permanentMessage.value = actionErrorMessage(error, false)
  }
}
</script>

<template>
  <section class="session-lifecycle" aria-labelledby="session-lifecycle-title">
    <div class="session-lifecycle__intro">
      <p class="session-lifecycle__eyebrow">考试管理</p>
      <h2 id="session-lifecycle-title">归档与永久删除</h2>
      <p>归档可以恢复；永久删除只用于确认不再需要的测试考试。</p>
    </div>

    <article v-if="sessionStore.currentSession" class="session-lifecycle__card">
      <div>
        <strong>归档当前考试</strong>
        <p>隐藏这场考试，但保留配置、答卷、成绩和知识图谱数据，之后可以恢复。</p>
      </div>
      <button
        v-if="archiveState === 'idle' || archiveState === 'error'"
        type="button"
        class="session-lifecycle__secondary"
        @click="reviewArchive"
      >查看归档影响</button>
      <span v-else-if="archiveState === 'loading'" role="status">正在核对考试状态…</span>
      <div v-if="archiveImpact && archiveState !== 'loading'" class="session-lifecycle__confirm">
        <label>
          <span>输入完整考试名称“{{ archiveImpact.session.name }}”确认归档</span>
          <input v-model="archiveConfirmation" autocomplete="off" :disabled="archiveState === 'working'">
        </label>
        <button
          type="button"
          class="session-lifecycle__archive"
          :disabled="!canArchive || archiveState === 'working'"
          @click="submitArchive"
        >{{ archiveState === 'working' ? '正在归档…' : '确认归档' }}</button>
      </div>
      <p v-if="archiveMessage" class="session-lifecycle__message" role="status">{{ archiveMessage }}</p>
    </article>

    <article class="session-lifecycle__card session-lifecycle__card--danger">
      <div>
        <strong>已归档考试</strong>
        <p>永久删除后无法从系统恢复。学生名单、已入库试题和标签、题库共享源卷会保留。</p>
      </div>
      <span v-if="archivedState === 'loading'" role="status">正在读取归档列表…</span>
      <button
        v-else-if="archivedState === 'error'"
        type="button"
        class="session-lifecycle__secondary"
        @click="loadArchivedSessions()"
      >重新读取归档列表</button>
      <p v-else-if="archivedSessions.length === 0" class="session-lifecycle__empty">目前没有已归档考试。</p>
      <template v-else>
        <label class="session-lifecycle__picker">
          <span>选择已归档考试</span>
          <select v-model="selectedArchivedId">
            <option v-for="session in archivedSessions" :key="session.id" :value="session.id">
              {{ session.name }}
            </option>
          </select>
        </label>
        <div class="session-lifecycle__actions">
          <button
            type="button"
            class="session-lifecycle__secondary"
            :disabled="permanentState === 'working'"
            @click="restoreSelectedArchive"
          >恢复考试</button>
          <button
            v-if="permanentState === 'idle' || permanentState === 'error'"
            type="button"
            class="session-lifecycle__danger-outline"
            @click="reviewPermanentDeletion"
          >查看永久删除清单</button>
        </div>
      </template>

      <span v-if="permanentState === 'loading'" role="status">正在核对删除范围…</span>
      <div v-if="permanentImpact && permanentState !== 'loading'" class="session-lifecycle__permanent">
        <div class="session-lifecycle__impact-grid">
          <span><b>{{ permanentImpact.permanent_counts.answer_sheets ?? 0 }}</b> 份学生答卷</span>
          <span><b>{{ permanentImpact.permanent_counts.grading_results ?? 0 }}</b> 份批改结果</span>
          <span><b>{{ permanentImpact.permanent_counts.grading_details ?? 0 }}</b> 条评分明细</span>
          <span><b>{{ permanentImpact.storage_counts.owned_files ?? 0 }}</b> 个考试文件</span>
          <span><b>{{ deletionRecordTotal }}</b> 条考试记录</span>
        </div>
        <p class="session-lifecycle__scope">
          同时删除这场考试对知识图谱的贡献及题库来源关联；不删除学生名单、题库试题与标签。
          全库历史备份和全局 AI 调用日志不属于单场考试删除范围。
        </p>
        <p v-if="permanentImpact.blocking_training_tasks.length" class="session-lifecycle__blocker">
          阻断：训练任务 {{ permanentImpact.blocking_training_tasks.join('、') }} 含有本场考试诊断快照。
        </p>
        <label v-else-if="permanentImpact.can_permanently_delete">
          <span>输入“{{ permanentImpact.permanent_delete_phrase }}”确认</span>
          <input v-model="permanentConfirmation" autocomplete="off" :disabled="permanentState === 'working'">
        </label>
        <button
          v-if="permanentImpact.can_permanently_delete"
          type="button"
          class="session-lifecycle__danger"
          :disabled="!canPermanentlyDelete || permanentState === 'working'"
          @click="submitPermanentDeletion"
        >{{ permanentState === 'working' ? '正在永久删除…' : '永久删除这场考试' }}</button>
      </div>
      <p v-if="permanentMessage" class="session-lifecycle__message" role="status">{{ permanentMessage }}</p>
    </article>
  </section>
</template>

<style scoped>
.session-lifecycle {
  display: grid;
  gap: var(--space-4);
  margin-block-start: var(--space-7);
  padding-block-start: var(--space-5);
  border-block-start: var(--border-width) solid var(--color-border-default);
}
.session-lifecycle p,
.session-lifecycle h2 { margin: 0; }
.session-lifecycle__intro h2 { margin-block: var(--space-1) var(--space-2); font-size: var(--font-size-h2); }
.session-lifecycle__intro p { color: var(--color-text-secondary); }
.session-lifecycle__eyebrow {
  color: var(--color-accent) !important;
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
}
.session-lifecycle__card {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) minmax(340px, 1.25fr);
  align-items: start;
  gap: var(--space-4) var(--space-6);
  padding: var(--space-4);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-card);
  background: var(--color-bg-surface);
}
.session-lifecycle__card--danger { border-color: color-mix(in srgb, var(--color-danger) 42%, var(--color-border-default)); }
.session-lifecycle__card p { margin-block-start: var(--space-1); color: var(--color-text-secondary); }
.session-lifecycle__confirm,
.session-lifecycle__permanent,
.session-lifecycle__picker,
.session-lifecycle__confirm label,
.session-lifecycle__permanent label {
  display: grid;
  gap: var(--space-2);
}
.session-lifecycle__picker { grid-column: 1 / -1; }
.session-lifecycle__picker select,
.session-lifecycle input {
  min-height: var(--control-height-default);
  padding-inline: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}
.session-lifecycle__actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.session-lifecycle button {
  min-height: var(--control-height-default);
  width: fit-content;
  padding-inline: var(--space-4);
  border-radius: var(--radius-control);
  cursor: pointer;
  font-weight: var(--font-weight-semibold);
}
.session-lifecycle button:disabled { cursor: not-allowed; opacity: var(--opacity-disabled); }
.session-lifecycle__secondary {
  border: var(--border-width) solid var(--color-border-default);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}
.session-lifecycle__archive {
  border: var(--border-width) solid var(--color-accent);
  background: var(--color-accent);
  color: #fff;
}
.session-lifecycle__danger-outline {
  border: var(--border-width) solid var(--color-danger);
  background: var(--color-bg-surface);
  color: var(--color-danger);
}
.session-lifecycle__danger {
  border: var(--border-width) solid var(--color-danger);
  background: var(--color-danger);
  color: var(--color-bg-surface);
}
.session-lifecycle__permanent { grid-column: 1 / -1; padding-block-start: var(--space-3); border-block-start: var(--border-width) solid var(--color-border-default); }
.session-lifecycle__impact-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  gap: var(--space-2);
}
.session-lifecycle__impact-grid span {
  padding: var(--space-3);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}
.session-lifecycle__impact-grid b { display: block; color: var(--color-text-primary); font-size: var(--font-size-body); }
.session-lifecycle__scope { font-size: var(--font-size-dense); }
.session-lifecycle__blocker,
.session-lifecycle__message { grid-column: 1 / -1; color: var(--color-danger) !important; }
.session-lifecycle__empty { grid-column: 1 / -1; }
@media (max-width: 1100px) {
  .session-lifecycle__card { grid-template-columns: 1fr; }
  .session-lifecycle__impact-grid { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
}
</style>
