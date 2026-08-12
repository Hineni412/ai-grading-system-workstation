<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  fetchPendingSessionCleanups,
  fetchSessionDeletionImpact,
  permanentlyDeleteSession,
  type SessionDeletionImpact,
  type SessionPendingCleanup,
} from '../../api/sessions'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import { useSessionStore } from '../../stores/session'

type ActionState = 'idle' | 'loading' | 'ready' | 'working' | 'error' | 'unknown' | 'done'

const sessionStore = useSessionStore()
const state = ref<ActionState>('idle')
const impact = ref<SessionDeletionImpact | null>(null)
const message = ref('')
const pendingCleanups = ref<SessionPendingCleanup[]>([])
const deletionRecordTotal = computed(() => Object.values(impact.value?.permanent_counts ?? {})
  .reduce((total, count) => total + count, 0))

watch(() => sessionStore.currentSession?.id ?? null, () => {
  state.value = 'idle'
  impact.value = null
  message.value = ''
})

onMounted(async () => {
  try { pendingCleanups.value = await fetchPendingSessionCleanups() } catch { pendingCleanups.value = [] }
})

function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '考试尚未删除，请重新核对后再试。'
  if (error.code === 'session_delete_active_work') return '这场考试仍有生成、同步或批改任务，请先完成或取消任务。'
  if (error.code === 'session_delete_revision_conflict') return '考试内容在确认期间发生变化，请重新查看影响。'
  if (error.code === 'session_permanent_delete_training_snapshot_exists') return '训练任务仍引用这场考试，系统已阻止删除，避免破坏训练材料。'
  if (error.code === 'session_permanent_delete_storage_incomplete') return '部分文件正被占用。关闭文件后可再次点击删除。'
  return '考试尚未删除，请稍后重试。'
}

async function reviewDeletion(): Promise<void> {
  const current = sessionStore.currentSession
  if (!current) return
  state.value = 'loading'
  message.value = ''
  try {
    impact.value = await fetchSessionDeletionImpact(current.id)
    state.value = 'ready'
    if (!impact.value.can_permanently_delete) {
      message.value = impact.value.blocking_training_tasks.length
        ? '训练任务仍引用这场考试，当前不能删除。'
        : '这场考试仍有活动任务，当前不能删除。'
    }
  } catch {
    state.value = 'error'
    message.value = '暂时无法读取删除范围，没有删除任何内容。'
  }
}

async function submitDeletion(): Promise<void> {
  const reviewed = impact.value
  if (!reviewed?.can_permanently_delete || state.value === 'working') return
  if (!window.confirm(`确认彻底删除“${reviewed.session.name}”吗？此操作无法恢复。`)) return
  state.value = 'working'
  message.value = ''
  try {
    const result = await permanentlyDeleteSession(
      reviewed.session.id,
      reviewed.revision,
      reviewed.permanent_delete_phrase,
    )
    sessionStore.clearSelection()
    await sessionStore.initialize()
    impact.value = null
    state.value = 'done'
    if (result.storage_cleanup_pending) {
      pendingCleanups.value = await fetchPendingSessionCleanups()
      message.value = '主要数据已永久删除；文件清理尚未完成，关闭占用文件后可重试。'
    } else message.value = `“${reviewed.session.name}”已彻底删除。`
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      await sessionStore.initialize()
      if (sessionStore.loadState === 'ready') {
        impact.value = null
        if (!sessionStore.sessions.some(item => item.id === reviewed.session.id)) {
          try { pendingCleanups.value = await fetchPendingSessionCleanups() } catch { /* list can be retried after remount */ }
          state.value = 'done'
          message.value = `已重新核对：“${reviewed.session.name}”已彻底删除。`
        } else {
          state.value = 'error'
          message.value = '已重新核对：考试尚未删除，可以再次查看影响后重试。'
        }
        return
      }
      state.value = 'unknown'
      message.value = '删除结果暂时无法确认，且考试列表暂时无法读取。恢复连接后请刷新核对；当前不会重复提交。'
      return
    }
    impact.value = null
    state.value = 'error'
    message.value = errorMessage(error)
  }
}

async function retryPendingCleanup(pending: SessionPendingCleanup): Promise<void> {
  state.value = 'working'
  try {
    const result = await permanentlyDeleteSession(pending.session_id, '0'.repeat(64), '恢复文件清理')
    pendingCleanups.value = result.storage_cleanup_pending
      ? await fetchPendingSessionCleanups()
      : pendingCleanups.value.filter(item => item.session_id !== pending.session_id)
    state.value = 'done'
    message.value = result.storage_cleanup_pending ? '仍有文件被占用，请关闭后重试。' : '遗留文件清理已完成。'
  } catch {
    state.value = 'error'
    message.value = '遗留文件尚未清理完成。关闭占用文件后可再次重试。'
  }
}
</script>

<template>
  <section class="session-lifecycle" aria-labelledby="session-lifecycle-title">
    <div class="session-lifecycle__intro">
      <p class="session-lifecycle__eyebrow">考试管理</p>
      <h2 id="session-lifecycle-title">彻底删除考试</h2>
      <p>确认后会直接永久删除本场考试，且无法恢复。</p>
    </div>

    <article v-if="sessionStore.currentSession" class="session-lifecycle__card session-lifecycle__card--danger">
      <div>
        <strong>{{ sessionStore.currentSession.name }}</strong>
        <p>先查看影响范围，再弹出一次最终确认。</p>
      </div>
      <button v-if="state === 'idle' || state === 'error'" type="button" class="session-lifecycle__danger-outline" @click="reviewDeletion">查看删除影响</button>
      <span v-else-if="state === 'loading'" role="status">正在核对删除范围…</span>
      <div v-if="impact && state !== 'loading'" class="session-lifecycle__permanent">
        <div class="session-lifecycle__impact-grid">
          <span><b>{{ impact.permanent_counts.answer_sheets ?? 0 }}</b> 份学生答卷</span>
          <span><b>{{ impact.permanent_counts.grading_results ?? 0 }}</b> 份批改结果</span>
          <span><b>{{ impact.storage_counts.owned_files ?? 0 }}</b> 个考试文件</span>
          <span><b>{{ deletionRecordTotal }}</b> 条考试记录</span>
        </div>
        <p>学生名单、已入库试题和标签会保留；本场考试配置、答卷、成绩和知识图谱贡献会永久删除。</p>
        <button v-if="impact.can_permanently_delete && state !== 'done' && state !== 'unknown'" type="button" class="session-lifecycle__danger" :disabled="state === 'working'" @click="submitDeletion">{{ state === 'working' ? '正在彻底删除…' : '彻底删除这场考试' }}</button>
      </div>
    </article>
    <p v-else>请先选择要管理的考试。</p>

    <p v-if="message" class="session-lifecycle__message" role="status">{{ message }}</p>

    <div v-if="pendingCleanups.length" class="session-lifecycle__cleanup-list">
      <div v-for="pending in pendingCleanups" :key="pending.session_id">
        <span>考试 #{{ pending.session_id }} · {{ pending.deleted_files }} 个文件待清理</span>
        <button type="button" class="session-lifecycle__danger-outline" :disabled="state === 'working'" @click="retryPendingCleanup(pending)">重试文件清理</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.session-lifecycle { display: grid; gap: var(--space-4); margin-block-start: var(--space-7); padding-block-start: var(--space-5); border-block-start: var(--border-width) solid var(--border); }
.session-lifecycle p, .session-lifecycle h2 { margin: 0; }
.session-lifecycle__intro h2 { margin-block: var(--space-1) var(--space-2); font-size: var(--font-size-h2); }
.session-lifecycle__intro p, .session-lifecycle__card p { color: var(--color-text-secondary); }
.session-lifecycle__eyebrow { color: var(--color-accent) !important; font-size: var(--font-size-caption); font-weight: var(--font-weight-semibold); }
.session-lifecycle__card { display: grid; grid-template-columns: minmax(240px, 1fr) minmax(340px, 1.25fr); align-items: start; gap: var(--space-4) var(--space-6); padding: var(--space-4); border: var(--border-width) solid color-mix(in srgb, var(--color-danger) 42%, var(--border)); border-radius: var(--radius-panel); background: var(--card); }
.session-lifecycle__permanent { grid-column: 1 / -1; display: grid; gap: var(--space-3); padding-block-start: var(--space-3); border-block-start: var(--border-width) solid var(--border); }
.session-lifecycle__impact-grid { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: var(--space-2); }
.session-lifecycle__impact-grid span { padding: var(--space-3); border-radius: var(--radius-control); background: var(--secondary); color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.session-lifecycle__impact-grid b { display: block; color: var(--color-text-primary); font-size: var(--font-size-body); }
.session-lifecycle button { min-height: var(--control-height-default); width: fit-content; padding-inline: var(--space-4); border-radius: var(--radius-control); cursor: pointer; font-weight: var(--font-weight-semibold); }
.session-lifecycle__danger-outline { border: var(--border-width) solid var(--color-danger); background: var(--card); color: var(--color-danger); }
.session-lifecycle__danger-outline:hover:not(:disabled) { background: var(--color-danger-subtle); }
.session-lifecycle__danger { border: var(--border-width) solid var(--color-danger); background: var(--color-danger); color: var(--destructive-foreground); }
.session-lifecycle__danger:hover:not(:disabled) { background: color-mix(in srgb, var(--color-danger) 88%, var(--color-text-primary)); }
.session-lifecycle__message { grid-column: 1 / -1; color: var(--color-danger) !important; }
.session-lifecycle__cleanup-list { display: grid; gap: var(--space-2); }
.session-lifecycle__cleanup-list > div { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
@media (max-width: 1100px) { .session-lifecycle__card { grid-template-columns: 1fr; } .session-lifecycle__impact-grid { grid-template-columns: repeat(2, minmax(120px, 1fr)); } }
</style>
