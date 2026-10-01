<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { opsApi } from '../../api/ops'
import { useOpsStore } from '../../stores/ops'
import AppButton from '../design-system/AppButton.vue'
import StatusBadge from '../design-system/StatusBadge.vue'
import AiDiagnosticsPanel from './AiDiagnosticsPanel.vue'
const props = defineProps<{ scrollToLog?: boolean }>()
const ops = useOpsStore()
const copied = ref(false)
const copyError = ref('')
const log = ref<HTMLElement | null>(null)
const labels: Record<string, string> = { microsoft_word: 'Microsoft Word', libreoffice: 'LibreOffice', pdflatex: 'PDF 排版工具', data: '数据文件夹', grading: '阅卷数据库', question_bank: '题库数据库' }
const pending = computed(() => ops.selfCheck?.databases.reduce((n, item) => n + item.pending_migrations, 0) ?? 0)
const checks = computed(() => {
  const snapshot = ops.selfCheck
  if (!snapshot) return []
  return [
    { label: '数据文件夹', value: snapshot.directories.every(item => item.status === 'ok') ? '正常' : '需检查', bad: snapshot.directories.some(item => item.status !== 'ok') },
    ...snapshot.databases.map(item => ({ label: labels[item.key] ?? item.key, value: item.integrity === 'ok' ? '完整' : '需检查', bad: item.integrity !== 'ok' })),
    { label: '数据库版本', value: pending.value === 0 ? '已是最新' : `有 ${pending.value} 项待更新`, bad: pending.value > 0 },
    ...snapshot.tools.map(item => ({ label: labels[item.key] ?? item.key, value: item.available ? '可用' : '未找到', bad: !item.available })),
    { label: 'AI 服务', value: snapshot.api_configured ? '已配置' : '未配置', bad: !snapshot.api_configured },
  ]
})
const firstIssue = computed(() => {
  const first = checks.value.find(item => item.bad)
  return first ? `${first.value === '未找到' ? '未找到' : ''}${first.label}${first.value === '未找到' ? '' : `：${first.value}`}` : '请展开检查项查看详情。'
})
async function copyDiagnostic() {
  try { await navigator.clipboard.writeText(ops.diagnosticText); copied.value = true; copyError.value = '' }
  catch { copyError.value = '复制未成功，请再试一次。' }
}
async function showLog() { await nextTick(); if (props.scrollToLog) log.value?.scrollIntoView({ block: 'start' }) }
watch(() => props.scrollToLog, showLog)
onMounted(async () => { await ops.refreshSelfCheck(opsApi); await showLog() })
</script>
<template>
  <section aria-label="系统状态">
    <div class="settings-panel"><div class="settings-panel__body">
      <div v-if="ops.selfCheck" class="settings-system-line">
        <StatusBadge :tone="ops.selfCheck.status === 'ok' ? 'success' : 'warning'" :label="ops.selfCheck.status === 'ok' ? '一切正常' : `有 ${Math.max(ops.selfCheck.warnings.length, checks.filter(item => item.bad).length)} 项需要注意`" />
        <span v-if="ops.selfCheck.status !== 'ok'">{{ firstIssue }}</span>
        <span class="settings-version">版本 {{ ops.selfCheck.version }}</span>
        <AppButton variant="secondary" data-testid="copy-diagnostic" :disabled="!ops.diagnosticText" @click="copyDiagnostic">复制排查信息</AppButton>
      </div>
      <p v-else class="settings-note">正在读取系统状态…</p>
      <p v-if="copied" class="settings-feedback is-success" role="status">已复制（不含密钥和学生信息）</p>
      <p v-if="copyError || ops.selfCheckError" class="settings-feedback is-error" role="alert">{{ copyError || ops.selfCheckError?.message }} <AppButton variant="ghost" @click="ops.refreshSelfCheck(opsApi)">刷新</AppButton></p>
      <details class="settings-disclosure"><summary>查看全部检查项</summary><div class="settings-check-grid"><div v-for="check in checks" :key="check.label" :class="{ 'is-warning': check.bad }"><span>{{ check.label }}</span><span>{{ check.value }}</span></div></div></details>
    </div></div>
    <section id="ai-call-log" ref="log" class="settings-panel settings-log-panel">
      <header class="settings-panel__heading"><h2>AI 调用记录</h2><span class="settings-note">记录只保存在本机。</span></header>
      <AiDiagnosticsPanel />
    </section>
  </section>
</template>
