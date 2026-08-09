<script setup lang="ts">
import { ref } from 'vue'

import { ApiError } from '../../api/errors'
import {
  fetchSessionDeletionImpact,
  permanentlyDeleteSession,
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
    return '考试内容刚刚发生变化，请重新点击删除后再确认。'
  }
  return '考试尚未删除，请稍后重试。'
}

async function remove(): Promise<void> {
  if (working.value) return
  working.value = true
  try {
    const impact = await fetchSessionDeletionImpact(props.sessionId)
    if (!impact.can_permanently_delete) {
      window.alert('这场考试仍有生成、同步或批改任务未结束，请先完成或取消任务。')
      return
    }
    const confirmed = window.confirm(
      `确认彻底删除“${props.sessionName}”吗？\n\n这会永久删除本场考试的配置、答卷、成绩和知识图谱贡献，且无法恢复。学生名单和已经入库的题库试题会保留。`,
    )
    if (!confirmed) return
    try {
      await permanentlyDeleteSession(
        props.sessionId,
        impact.revision,
        impact.permanent_delete_phrase,
      )
    } catch (error) {
      if (!isAmbiguousWriteError(error)) throw error
      await sessionStore.initialize()
      if (sessionStore.sessions.some(item => item.id === props.sessionId)) throw error
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
    :label="`彻底删除考试：${props.sessionName}`"
    icon="trash"
    variant="secondary"
    :disabled="working"
    @click.stop="remove"
  />
</template>
