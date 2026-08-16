<script setup lang="ts">
import {
  computed,
  defineAsyncComponent,
  ref,
  watch,
  type ComponentPublicInstance,
} from 'vue'
import { useRoute, useRouter } from 'vue-router'

const ModelProfilesView = defineAsyncComponent(
  () => import('./ModelProfilesView.vue'),
)
const SettingsOpsView = defineAsyncComponent(
  () => import('./SettingsOpsView.vue'),
)
const AiDiagnosticsPanel = defineAsyncComponent(
  () => import('../components/settings/AiDiagnosticsPanel.vue'),
)

type SettingsSection = 'ai' | 'ai-trace' | 'backup' | 'maintenance'
type ModelProfilesViewInstance = ComponentPublicInstance & {
  hasUnsavedChanges: boolean
}
const route = useRoute()
const router = useRouter()
const modelView = ref<ModelProfilesViewInstance | null>(null)
const section = computed<SettingsSection>(() => {
  const value = route.query.section
  if (value === 'backup' || value === 'maintenance' || value === 'ai-trace') return value
  return 'ai'
})

async function selectSection(next: SettingsSection): Promise<void> {
  if (next === section.value) return
  if (section.value === 'ai' && modelView.value?.hasUnsavedChanges && !globalThis.confirm('AI 服务还有未保存修改。离开后会丢失这些修改，是否继续？')) return
  await router.replace({ query: { ...route.query, section: next } })
}

watch(() => route.query.section, value => {
  if (value === 'ai' || value === 'ai-trace' || value === 'backup' || value === 'maintenance') return
  void router.replace({ query: { ...route.query, section: 'ai' } })
}, { immediate: true })
</script>

<template>
  <main class="settings-hub">
    <header class="settings-hub__header">
      <div><p>设置</p><h1 tabindex="-1">设置中心</h1><span>日常状态和主要操作在前，高级配置需要时再展开。</span></div>
    </header>
    <div class="settings-hub__ledger">
      <nav class="settings-hub__menu" aria-label="设置页面">
        <button type="button" :aria-current="section === 'ai' ? 'page' : undefined" @click="selectSection('ai')"><span>AI 服务</span><small>工作模型与站点</small></button>
        <button type="button" :aria-current="section === 'ai-trace' ? 'page' : undefined" @click="selectSection('ai-trace')"><span>AI 调用记录</span><small>发送、返回与解析</small></button>
        <button type="button" :aria-current="section === 'backup' ? 'page' : undefined" @click="selectSection('backup')"><span>备份与恢复</span><small>保护本机数据</small></button>
        <button type="button" :aria-current="section === 'maintenance' ? 'page' : undefined" @click="selectSection('maintenance')"><span>检查与维护</span><small>系统状态与排查</small></button>
      </nav>
      <section class="settings-hub__content">
        <header class="settings-hub__section-heading">
          <p>{{ section === 'ai' ? '日常使用' : section === 'ai-trace' ? '排查记录' : section === 'backup' ? '数据保护' : '本机状态' }}</p>
          <h2>{{ section === 'ai' ? 'AI 服务' : section === 'ai-trace' ? 'AI 调用记录' : section === 'backup' ? '备份与恢复' : '检查与维护' }}</h2>
          <span>{{ section === 'ai' ? '先确认四类工作使用哪个模型；站点和密钥放在高级设置中。' : section === 'ai-trace' ? '按用途和工作台查看每次发送、返回与解析。' : section === 'backup' ? '先选备份范围，再核对影响；恢复不会悄悄执行。' : '先看系统是否正常，需要时再复制排查信息。' }}</span>
        </header>
        <ModelProfilesView v-if="section === 'ai'" ref="modelView" compact />
        <AiDiagnosticsPanel v-else-if="section === 'ai-trace'" />
        <SettingsOpsView v-else embedded :section="section === 'maintenance' ? 'maintenance' : 'backup'" />
      </section>
    </div>
  </main>
</template>

<style scoped>
.settings-hub{min-height:100%;padding:clamp(18px,2.5vw,34px);background:var(--color-bg-app)}.settings-hub__header{max-width:var(--content-max-width);margin:0 auto var(--space-4)}.settings-hub__header div,.settings-hub__section-heading{display:grid;gap:5px}.settings-hub__header p,.settings-hub__header h1,.settings-hub__header span,.settings-hub__section-heading p,.settings-hub__section-heading h2,.settings-hub__section-heading span{margin:0}.settings-hub__header p,.settings-hub__section-heading p{color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}.settings-hub__header h1{font-size:var(--font-size-h1)}.settings-hub__header span,.settings-hub__section-heading span{color:var(--color-text-secondary)}.settings-hub__ledger{display:grid;grid-template-columns:230px minmax(0,1fr);max-width:var(--content-max-width);margin:auto;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);overflow:hidden}.settings-hub__menu{display:flex;flex-direction:column;gap:6px;padding:var(--space-4);border-inline-end:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.settings-hub__menu button{display:grid;gap:3px;min-height:72px;padding:12px 14px;border:0;border-inline-start:4px solid transparent;border-radius:var(--radius-control);background:transparent;color:var(--color-text-primary);font:inherit;text-align:left}.settings-hub__menu button span{font-weight:700}.settings-hub__menu button small{color:var(--color-text-secondary)}.settings-hub__menu button[aria-current="page"]{border-inline-start-color:var(--color-accent);background:var(--card)}.settings-hub__content{min-width:0;padding:clamp(18px,2vw,30px)}.settings-hub__section-heading{padding-bottom:var(--space-4);border-bottom:1px solid var(--color-border-default)}.settings-hub__section-heading h2{font-size:var(--font-size-h2)}:deep(.model-profiles-view),:deep(.settings-ops){padding:var(--space-4) 0 0;background:transparent}:deep(.model-task-routing){margin-top:0}:deep(.model-profiles-advanced-shell){margin-top:var(--space-4);padding:var(--space-3);border:1px solid var(--border);border-radius:var(--radius)}:deep(.model-profiles-advanced-shell>summary){cursor:pointer;color:var(--color-accent-active);font-weight:700}:deep(.settings-ops__layout){grid-template-columns:minmax(0,1fr)}@media(max-width:820px){.settings-hub__ledger{grid-template-columns:1fr}.settings-hub__menu{display:grid;grid-template-columns:repeat(2,1fr);border-inline-end:0;border-bottom:1px solid var(--color-border-default)}.settings-hub__menu button{min-height:58px}}@media(max-width:620px){.settings-hub__menu{grid-template-columns:1fr}.settings-hub__content{padding:var(--space-4)}}
.settings-hub__header{width:100%;max-width:none;margin-inline:0}.settings-hub__ledger{width:100%;max-width:none;margin-inline:0}
</style>
