<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useSessionStore } from '../../stores/session'

const route = useRoute()
const sessionStore = useSessionStore()
const pageTitle = computed(() => String(route.meta.title ?? ''))

function selectSession(event: Event): void {
  const value = (event.currentTarget as HTMLSelectElement).value
  sessionStore.selectSession(value === '' ? null : Number(value))
}

function retrySessions(): void {
  void sessionStore.initialize()
}
</script>

<template>
  <header class="app-topbar" data-testid="app-topbar">
    <a class="app-topbar__skip-link" href="#main-workspace">跳到主要工作区</a>

    <div class="app-topbar__identity">
      <span>AI 阅卷系统</span>
      <strong>{{ pageTitle }}</strong>
    </div>

    <div class="app-topbar__session">
      <label for="current-session">当前考试</label>
      <select
        id="current-session"
        :value="sessionStore.selectedSessionId ?? ''"
        :disabled="sessionStore.loadState === 'loading' || sessionStore.loadState === 'error'"
        @change="selectSession"
      >
        <option value="">未选择</option>
        <option v-for="session in sessionStore.sessions" :key="session.id" :value="session.id">
          {{ session.name }}
        </option>
      </select>
    </div>

    <div v-if="sessionStore.loadState === 'loading'" class="app-topbar__status" role="status">
      正在读取考试列表
    </div>
    <div v-else-if="sessionStore.loadState === 'error'" class="app-topbar__status" role="alert">
      <span>考试列表加载失败。</span>
      <button type="button" @click="retrySessions">重新加载考试列表</button>
    </div>
  </header>
</template>
