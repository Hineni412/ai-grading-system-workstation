<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch, type ComponentPublicInstance } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import PageHeader from '../components/design-system/PageHeader.vue'
import '../styles/settings.css'
const SettingsStudentsPanel = defineAsyncComponent(() => import('../components/settings/SettingsStudentsPanel.vue'))
const ModelProfilesView = defineAsyncComponent(() => import('./ModelProfilesView.vue'))
const SettingsDataPanel = defineAsyncComponent(() => import('../components/settings/SettingsDataPanel.vue'))
const SettingsSystemPanel = defineAsyncComponent(() => import('../components/settings/SettingsSystemPanel.vue'))
type Section = 'students' | 'ai' | 'data' | 'system'
const sections = [
  { key: 'students', label: '学生名单' }, { key: 'ai', label: 'AI 服务' },
  { key: 'data', label: '数据与空间' }, { key: 'system', label: '系统状态' },
] as const
const aliases: Record<string, Section> = { 'ai-trace': 'system', backup: 'data', maintenance: 'system', models: 'ai' }
const storageKey = 'ai-grading:settings-section:v2'
const route = useRoute()
const router = useRouter()
const modelView = ref<(ComponentPublicInstance & { hasUnsavedChanges: boolean }) | null>(null)
function valid(value: unknown): value is Section { return sections.some(s => s.key === value) }
function stored(): Section {
  try { const value = localStorage.getItem(storageKey); if (valid(value)) return value } catch { /* unavailable */ }
  return 'students'
}
const section = computed(() => valid(route.query.section) ? route.query.section : stored())
onBeforeRouteUpdate(to => {
  if (section.value === 'ai' && to.query.section !== 'ai' && modelView.value?.hasUnsavedChanges) {
    return globalThis.confirm('AI 服务还有未保存修改。离开后会丢失这些修改，是否继续？')
  }
})
watch(() => route.query.section, value => {
  if (valid(value)) {
    try { localStorage.setItem(storageKey, value) } catch { /* unavailable */ }
  } else {
    const alias = typeof value === 'string' ? aliases[value] : undefined
    void router.replace({ query: { ...route.query, section: alias ?? stored() }, hash: value === 'ai-trace' ? '#ai-call-log' : route.hash })
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
    <div class="settings-content" :class="{ 'settings-content--students': section === 'students' }">
      <SettingsStudentsPanel v-if="section === 'students'" />
      <ModelProfilesView v-else-if="section === 'ai'" ref="modelView" />
      <SettingsDataPanel v-else-if="section === 'data'" />
      <SettingsSystemPanel v-else :scroll-to-log="route.hash === '#ai-call-log'" />
    </div>
  </section>
</template>
