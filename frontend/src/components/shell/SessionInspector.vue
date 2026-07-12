<script setup lang="ts">
import StatePanel from '../design-system/StatePanel.vue'
import { useSessionStore } from '../../stores/session'

defineEmits<{
  retry: []
}>()

const sessionStore = useSessionStore()
</script>

<template>
  <aside
    id="session-inspector"
    class="session-inspector"
    data-testid="session-inspector"
    aria-label="当前考试检查器"
  >
    <StatePanel
      v-if="sessionStore.loadState === 'idle' || sessionStore.loadState === 'loading'"
      kind="loading"
      title="正在加载考试列表"
      description="读取完成后可以选择当前考试。"
    />
    <StatePanel
      v-else-if="sessionStore.loadState === 'error'"
      kind="error"
      title="考试列表加载失败"
      :description="sessionStore.errorMessage"
      retry-label="重新加载考试列表"
      @retry="$emit('retry')"
    />
    <StatePanel
      v-else-if="!sessionStore.currentSession"
      kind="empty"
      title="未选择当前考试"
      description="请从顶部的当前考试列表中选择一项。"
    />
    <section v-else class="session-inspector__current">
      <h2>{{ sessionStore.currentSession.name }}</h2>
      <p>{{ sessionStore.currentSession.status }}</p>
      <p>后续业务页面将继续使用这一考试上下文。</p>
    </section>
  </aside>
</template>
