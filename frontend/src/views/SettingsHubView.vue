<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ModelProfilesView from './ModelProfilesView.vue'
import SettingsOpsView from './SettingsOpsView.vue'

type SettingsSection = 'models' | 'maintenance'

const route = useRoute()
const router = useRouter()
const modelView = ref<InstanceType<typeof ModelProfilesView> | null>(null)
const section = computed<SettingsSection>(() => (
  route.query.section === 'maintenance' ? 'maintenance' : 'models'
))

async function selectSection(next: SettingsSection): Promise<void> {
  if (next === section.value) return
  if (
    section.value === 'models'
    && modelView.value?.hasUnsavedChanges
    && !globalThis.confirm('模型设置还有未保存修改。离开后会丢失这些修改，是否继续？')
  ) return
  await router.replace({ query: { ...route.query, section: next } })
}

watch(
  () => route.query.section,
  value => {
    if (value === 'models' || value === 'maintenance') return
    void router.replace({ query: { ...route.query, section: 'models' } })
  },
  { immediate: true },
)
</script>

<template>
  <main class="settings-hub">
    <header class="settings-hub__header">
      <p>设置</p>
      <h1 tabindex="-1">模型、备份与维护</h1>
      <span>在一个页面中安排 AI 任务使用的模型，并保护本机数据。</span>
    </header>
    <nav class="settings-hub__tabs" aria-label="设置页面">
      <button type="button" :aria-current="section === 'models' ? 'page' : undefined" @click="selectSection('models')">模型与 AI 任务</button>
      <button type="button" :aria-current="section === 'maintenance' ? 'page' : undefined" @click="selectSection('maintenance')">备份与维护</button>
    </nav>
    <ModelProfilesView v-if="section === 'models'" ref="modelView" />
    <SettingsOpsView v-else />
  </main>
</template>

<style scoped>
.settings-hub{min-height:100%;padding:clamp(18px,2.5vw,34px);background:var(--color-bg-app)}.settings-hub__header{display:grid;gap:5px;max-width:var(--content-max-width);margin:0 auto var(--space-4)}.settings-hub__header p,.settings-hub__header h1,.settings-hub__header span{margin:0}.settings-hub__header p{color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}.settings-hub__header h1{font-size:var(--font-size-h1)}.settings-hub__header span{color:var(--color-text-secondary)}.settings-hub__tabs{display:flex;max-width:var(--content-max-width);margin:0 auto var(--space-4);border-bottom:1px solid var(--color-border-default)}.settings-hub__tabs button{min-height:44px;padding:0 var(--space-4);border:0;border-bottom:3px solid transparent;background:transparent;color:var(--color-text-secondary);font:inherit;font-weight:650}.settings-hub__tabs button[aria-current="page"]{border-bottom-color:var(--color-accent);color:var(--color-accent-active)}
</style>
