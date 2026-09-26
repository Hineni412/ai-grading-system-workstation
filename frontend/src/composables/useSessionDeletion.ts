import { computed, onMounted, ref } from 'vue'

import {
  fetchPendingSessionCleanups,
  fetchSessionDeletionImpact,
  permanentlyDeleteSession,
  type SessionDeletionImpact,
  type SessionPendingCleanup,
} from '../api/sessions'
import { ApiError, isAmbiguousWriteError } from '../api/errors'
import { useSessionStore } from '../stores/session'

export type SessionDeletionState =
  'idle' | 'loading' | 'ready' | 'working' | 'error' | 'unknown' | 'done'

/* 彻底删除某场考试的核对流程（按 session id 参数化，供考试管理抽屉的行内展开使用）：
   先只读拉取影响 → 一次不可逆确认 → 删除；结果含糊时重读列表判定，
   绝不重发删除请求；只有删掉的恰是当前考试时才清空选择。 */
export function useSessionDeletion() {
  const sessionStore = useSessionStore()
  const expandedId = ref<number | null>(null)
  const state = ref<SessionDeletionState>('idle')
  const impact = ref<SessionDeletionImpact | null>(null)
  const rowMessage = ref('')
  const notice = ref('')
  const pendingCleanups = ref<SessionPendingCleanup[]>([])
  const cleanupBusy = ref(false)

  const deletionRecordTotal = computed(() => Object.values(impact.value?.permanent_counts ?? {})
    .reduce((total, count) => total + count, 0))

  async function refreshPendingCleanups(): Promise<void> {
    try {
      pendingCleanups.value = await fetchPendingSessionCleanups()
    } catch {
      pendingCleanups.value = []
    }
  }

  function collapse(): void {
    expandedId.value = null
    impact.value = null
    rowMessage.value = ''
    state.value = 'idle'
  }

  function errorMessage(error: unknown): string {
    if (!(error instanceof ApiError)) return '考试尚未删除，请重新核对后再试。'
    if (error.code === 'session_delete_active_work') return '这场考试仍有生成、同步或批改任务，请先完成或取消任务。'
    if (error.code === 'session_delete_revision_conflict') return '考试内容在确认期间发生变化，请重新查看影响。'
    if (error.code === 'session_permanent_delete_training_snapshot_exists') return '训练任务仍引用这场考试，系统已阻止删除，避免破坏训练材料。'
    if (error.code === 'session_permanent_delete_storage_incomplete') return '部分文件正被占用。关闭文件后可再次点击删除。'
    return '考试尚未删除，请稍后重试。'
  }

  async function review(sessionId: number): Promise<void> {
    expandedId.value = sessionId
    impact.value = null
    rowMessage.value = ''
    state.value = 'loading'
    try {
      impact.value = await fetchSessionDeletionImpact(sessionId)
      state.value = 'ready'
      if (!impact.value.can_permanently_delete) {
        rowMessage.value = impact.value.blocking_training_tasks.length
          ? '训练任务仍引用这场考试，当前不能删除。'
          : '这场考试仍有活动任务，当前不能删除。'
      }
    } catch {
      impact.value = null
      state.value = 'error'
      rowMessage.value = '暂时无法读取删除范围，没有删除任何内容。'
    }
  }

  async function toggle(sessionId: number): Promise<void> {
    if (expandedId.value === sessionId) {
      collapse()
      return
    }
    await review(sessionId)
  }

  async function submitDeletion(): Promise<void> {
    const reviewed = impact.value
    if (!reviewed?.can_permanently_delete || state.value === 'working') return
    if (!window.confirm(`确认彻底删除“${reviewed.session.name}”吗？此操作无法恢复。`)) return
    state.value = 'working'
    rowMessage.value = ''
    try {
      const result = await permanentlyDeleteSession(
        reviewed.session.id,
        reviewed.revision,
        reviewed.permanent_delete_phrase,
      )
      if (sessionStore.selectedSessionId === reviewed.session.id) {
        sessionStore.clearSelection()
      }
      await sessionStore.initialize()
      collapse()
      state.value = 'done'
      if (result.storage_cleanup_pending) {
        await refreshPendingCleanups()
        notice.value = '主要数据已永久删除；文件清理尚未完成，关闭占用文件后可重试。'
      } else {
        notice.value = `“${reviewed.session.name}”已彻底删除。`
      }
    } catch (error) {
      if (isAmbiguousWriteError(error)) {
        await sessionStore.initialize()
        if (sessionStore.loadState === 'ready') {
          impact.value = null
          if (!sessionStore.sessions.some(item => item.id === reviewed.session.id)) {
            collapse()
            state.value = 'done'
            try {
              pendingCleanups.value = await fetchPendingSessionCleanups()
            } catch { /* 列表可在下次打开时重试 */ }
            notice.value = `已重新核对：“${reviewed.session.name}”已彻底删除。`
          } else {
            state.value = 'error'
            rowMessage.value = '已重新核对：考试尚未删除，可以再次查看影响后重试。'
          }
          return
        }
        collapse()
        state.value = 'unknown'
        notice.value = '删除结果暂时无法确认，且考试列表暂时无法读取。恢复连接后请刷新核对；当前不会重复提交。'
        return
      }
      impact.value = null
      state.value = 'error'
      rowMessage.value = errorMessage(error)
    }
  }

  async function retryPendingCleanup(pending: SessionPendingCleanup): Promise<void> {
    cleanupBusy.value = true
    try {
      const result = await permanentlyDeleteSession(
        pending.session_id,
        '0'.repeat(64),
        '恢复文件清理',
      )
      pendingCleanups.value = result.storage_cleanup_pending
        ? await fetchPendingSessionCleanups()
        : pendingCleanups.value.filter(item => item.session_id !== pending.session_id)
      notice.value = result.storage_cleanup_pending
        ? '仍有文件被占用，请关闭后重试。'
        : '遗留文件清理已完成。'
    } catch {
      notice.value = '遗留文件尚未清理完成。关闭占用文件后可再次重试。'
    } finally {
      cleanupBusy.value = false
    }
  }

  onMounted(() => {
    void refreshPendingCleanups()
  })

  return {
    expandedId,
    state,
    impact,
    rowMessage,
    notice,
    pendingCleanups,
    cleanupBusy,
    deletionRecordTotal,
    refreshPendingCleanups,
    review,
    toggle,
    collapse,
    submitDeletion,
    retryPendingCleanup,
  }
}
