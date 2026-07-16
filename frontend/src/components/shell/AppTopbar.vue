<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { navigationItems } from '../../navigation'
import { useConfigWorkspaceStore } from '../../stores/config-workspace'
import { useSessionStore } from '../../stores/session'

const route = useRoute()
const sessionStore = useSessionStore()
const configStore = useConfigWorkspaceStore()
const pageTitle = computed(() => String(route.meta.title ?? ''))

function selectSession(event: Event): void {
  const selector = event.currentTarget as HTMLSelectElement
  const nextSessionId = selector.value === '' ? null : Number(selector.value)
  const changesSession = nextSessionId !== configStore.sessionId
  const restoreSelection = () => {
    selector.value = sessionStore.selectedSessionId === null
      ? ''
      : String(sessionStore.selectedSessionId)
  }
  if (changesSession && configStore.hasPendingSubmission) {
    window.alert('上一次上传或生成的结果仍在核对。为避免重复处理，请先回到考试配置完成核对，再切换考试。')
    restoreSelection()
    return
  }
  if (changesSession && configStore.hasDirtyEditor) {
    const discard = window.confirm('当前评分依据有未保存修改。切换考试会丢弃这些修改，是否继续？')
    if (!discard) {
      restoreSelection()
      return
    }
    configStore.discardEditorDraft()
  }
  if (!configStore.selectSession(nextSessionId)) return
  sessionStore.selectSession(nextSessionId)
  if (nextSessionId !== null) void configStore.loadSelectedSessionWorkspace(nextSessionId)
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

    <nav class="app-topbar__navigation" data-testid="app-navigation" aria-label="主要导航">
      <RouterLink
        v-for="item in navigationItems"
        :key="item.id"
        :to="item.path"
        :aria-current="route.name === item.id ? 'page' : undefined"
      >
        {{ item.label }}
      </RouterLink>
    </nav>

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
