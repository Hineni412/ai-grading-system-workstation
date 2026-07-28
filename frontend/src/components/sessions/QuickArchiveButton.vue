<script setup lang="ts">
import { ref } from 'vue'

import { ApiError } from '../../api/errors'
import {
  archiveSession,
  fetchSessionDeletionImpact,
} from '../../api/sessions'
import { isAmbiguousWriteError } from '../../api/errors'
import { useSessionStore } from '../../stores/session'
import AppIconButton from '../design-system/AppIconButton.vue'

const props = withDefaults(defineProps<{
  sessionId: number
  sessionName: string
  compact?: boolean
}>(), {
  compact: false,
})

const emit = defineEmits<{
  archived: [sessionId: number]
}>()

const sessionStore = useSessionStore()
const working = ref(false)

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === 'session_delete_active_work') {
    return '这场考试仍有生成、同步或批改任务未结束，请先完成或取消任务。'
  }
  if (error instanceof ApiError && error.code === 'session_delete_revision_conflict') {
    return '考试内容刚刚发生变化，请重新点击归档后再确认。'
  }
  return '考试尚未归档，请稍后重试。'
}

async function archive(): Promise<void> {
  if (working.value) return
  working.value = true
  try {
    const impact = await fetchSessionDeletionImpact(props.sessionId)
    if (!impact.can_archive) {
      window.alert('这场考试仍有生成、同步或批改任务未结束，请先完成或取消任务。')
      return
    }
    const confirmed = window.confirm(
      `归档“${props.sessionName}”？\n\n归档只会把考试从日常列表中隐藏；考试配置、答卷、成绩和知识图谱数据都会保留，之后可以在考试管理中恢复。`,
    )
    if (!confirmed) return
    try {
      await archiveSession(props.sessionId, impact.revision, impact.session.name)
    } catch (error) {
      if (!isAmbiguousWriteError(error)) throw error
      const reconciled = await fetchSessionDeletionImpact(props.sessionId)
      if (!reconciled.session.is_deleted) throw error
    }
    if (sessionStore.selectedSessionId === props.sessionId) sessionStore.clearSelection()
    await sessionStore.initialize()
    emit('archived', props.sessionId)
  } catch (error) {
    window.alert(errorMessage(error))
  } finally {
    working.value = false
  }
}
</script>

<template>
  <AppIconButton
    class="quick-archive-button"
    :class="{ 'quick-archive-button--compact': props.compact }"
    :label="`归档考试：${props.sessionName}`"
    icon="archive"
    variant="secondary"
    :disabled="working"
    @click.stop="archive"
  />
</template>
