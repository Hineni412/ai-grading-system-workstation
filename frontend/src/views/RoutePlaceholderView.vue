<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useSessionStore } from '../stores/session'

const route = useRoute()
const sessionStore = useSessionStore()

const title = computed(() => String(route.meta.title))
const description = computed(() => String(route.meta.description))
const currentSessionName = computed(
  () => sessionStore.currentSession?.name ?? '未选择当前考试',
)
</script>

<template>
  <main class="route-placeholder">
    <header>
      <h1 tabindex="-1">{{ title }}</h1>
      <p>{{ description }}</p>
    </header>
    <p>此工作区将在后续执行包中迁移。当前应用外壳和考试上下文已经可用。</p>
    <p>当前考试：{{ currentSessionName }}</p>
  </main>
</template>
