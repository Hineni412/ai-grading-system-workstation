<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch, type ComponentPublicInstance } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import PageHeader from '../components/design-system/PageHeader.vue'
import { useConfirm } from '../composables/useConfirm'
import '../styles/settings.css'
const SettingsStudentsPanel = defineAsyncComponent(() => import('../components/settings/SettingsStudentsPanel.vue'))
const ModelProfilesView = defineAsyncComponent(() => import('./ModelProfilesView.vue'))
const AiDiagnosticsPanel = defineAsyncComponent(() => import('../components/settings/AiDiagnosticsPanel.vue'))
const SettingsDataPanel = defineAsyncComponent(() => import('../components/settings/SettingsDataPanel.vue'))
type Section = 'log' | 'ai' | 'students'
const sections = [
  { key: 'log', label: '调用记录' }, { key: 'ai', label: 'AI 与数据' },
  { key: 'students', label: '学生名单' },
] as const
const aliases: Record<string, Section> = { 'ai-trace': 'log', backup: 'ai', data: 'ai', maintenance: 'students', system: 'students', models: 'ai' }
const storageKey = 'ai-grading:settings-section:v3'
const route = useRoute()
const router = useRouter()
const modelView = ref<(ComponentPublicInstance & { hasUnsavedChanges: boolean }) | null>(null)
function valid(value: unknown): value is Section { return sections.some(s => s.key === value) }
function stored(): Section {
  try {
    const value = localStorage.getItem(storageKey)
    if (valid(value)) return value
    const alias = typeof value === 'string' ? aliases[value] : undefined
    if (alias) return alias
  } catch { /* unavailable */ }
  return 'log'
}
const section = computed(() => valid(route.query.section) ? route.query.section : stored())
const { confirm } = useConfirm()
onBeforeRouteUpdate(async to => {
  if (section.value === 'ai' && to.query.section !== 'ai' && modelView.value?.hasUnsavedChanges) {
    return confirm({
      title: '离开 AI 服务设置？',
      message: '还有未保存的修改，离开后会丢失。',
      confirmLabel: '离开',
      danger: true,
    })
  }
})
watch(() => [route.query.section, route.hash], ([value]) => {
  if (route.hash === '#ai-call-log') {
    void router.replace({ query: { ...route.query, section: 'log' }, hash: '' })
    return
  }
  if (valid(value)) {
    try { localStorage.setItem(storageKey, value) } catch { /* unavailable */ }
  } else {
    const alias = typeof value === 'string' ? aliases[value] : undefined
    void router.replace({ query: { ...route.query, section: alias ?? stored() }, hash: route.hash })
  }
}, { immediate: true })
</script>
<template>
  <section class="settings-hub" aria-label="设置">
    <PageHeader title="设置">
      <template #navigation>
        <nav role="tablist" class="page-tabs" aria-label="设置分类">
          <button v-for="tab in sections" :key="tab.key" type="button" role="tab" :class="{ 'is-active': section === tab.key }" :aria-selected="section === tab.key" :aria-current="section === tab.key ? 'page' : undefined" @click="router.replace({ query: { ...route.query, section: tab.key }, hash: '' })">{{ tab.label }}</button>
        </nav>
      </template>
    </PageHeader>
    <div class="settings-content" :class="{ 'settings-content--wide': section === 'log' || section === 'students' }">
      <AiDiagnosticsPanel v-if="section === 'log'" />
      <div v-else-if="section === 'ai'" class="settings-ai-data">
        <ModelProfilesView ref="modelView" />
        <SettingsDataPanel />
      </div>
      <SettingsStudentsPanel v-else-if="section === 'students'" />
    </div>
  </section>
</template>
