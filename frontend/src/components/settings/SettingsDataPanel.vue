<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppDialog from '../design-system/AppDialog.vue'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import StatusBadge from '../design-system/StatusBadge.vue'
import { opsApi, storageApi, type StorageOverview, type StorageSession, type SessionOriginals, type OriginalsState } from '../../api/ops'
import { ApiError } from '../../api/errors'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'
import { useOpsStore } from '../../stores/ops'
import { useJobStore } from '../../stores/jobs'

const ops = useOpsStore()
const jobs = useJobStore()
const storage = ref<StorageOverview | null>(null)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const selected = ref<number[]>([])
const selectedBackup = ref('')
const backupBlock = ref<HTMLElement | null>(null)
const dialog = ref<'release' | 'clear' | 'legacy' | 'restore' | null>(null)
const targets = ref<Array<{ row: StorageSession; snapshot: SessionOriginals }>>([])
const phrase = ref('')
const busy = ref(false)
const dialogError = ref('')
const progress = ref('')
const controller = new AbortController()
let finalizedJobVersion = ''
const colors = ['var(--settings-space-originals)', 'var(--settings-space-annotations)', 'var(--settings-space-bank)', 'var(--settings-space-reports)', 'var(--settings-space-backups)', 'var(--settings-space-databases)', 'var(--color-border-strong)']
const stateLabels: Record<OriginalsState, string> = { complete: '完整', scans_released: '已释放扫描文件', cleared: '已清除', clearing: '清理未完成' }
const reasons: Record<string, string> = { before_exam: '考试前', before_update: '更新前', before_import: '导入前', before_restore: '恢复前', manual: '手动备份', after_exam: '考试后', unknown: '未记录' }
const zipBackups = computed(() => ops.backups.filter(item => item.kind === 'zip'))
const latest = computed(() => zipBackups.value[0])
const selectedRows = computed(() => storage.value?.sessions.filter(row => selected.value.includes(row.session_id)) ?? [])
const selectableRows = computed(() => storage.value?.sessions.filter(row => row.can_release_scans || row.can_clear) ?? [])
const canReleaseBatch = computed(() => selectedRows.value.length > 0 && selectedRows.value.every(row => row.can_release_scans))
const canClearBatch = computed(() => selectedRows.value.length > 0 && selectedRows.value.every(row => row.can_clear))
const backupRunning = computed(() => ops.preflightLoading || ops.submitting || Boolean(ops.activeJob && !TERMINAL_JOB_STATUSES.has(ops.activeJob.status)))
const backupSucceeded = computed(() => ops.activeJob?.job_type === 'ops_backup' && ops.activeJob.status === 'succeeded')
const outcome = computed(() => {
  const messages = { prepared: '恢复已准备好，重启应用后生效。', restart_required: '恢复已准备好，重启应用后生效。', applying: '正在应用恢复，现在不能撤销，请等待结果。', applied: '恢复已生效。', rolled_back: '恢复未生效，系统已回退并保留原有数据。', failed: '恢复失败，未确认生效，请先刷新状态。', cancelled: '待执行恢复已撤销。' }
  return ops.operationState ? messages[ops.operationState.status] : ''
})
const dialogTitle = computed(() => {
  const name = targets.value.length === 1 ? `「${targets.value[0]!.row.name}」` : `所选 ${targets.value.length} 场考试`
  return dialog.value === 'release' ? `释放${name}的扫描文件？` : dialog.value === 'clear' ? `清除${name}的学生原卷` : dialog.value === 'legacy' ? '清理旧批注图？' : '恢复所选备份'
})
const freedEstimate = computed(() => targets.value.reduce((sum, t) => sum + (dialog.value === 'release' ? t.snapshot.release_bytes : t.snapshot.clear_bytes), 0))
function bytes(value: number) { if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`; if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`; if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`; return `${value} B` }
function time(value: string | null | undefined) { return value ? value.replace('T', ' ').replace(/\.\d+.*$/, '').slice(0, 16) : '暂无' }
function message(reason: unknown) { return reason instanceof ApiError ? reason.message : reason instanceof Error ? reason.message : '操作暂时无法完成' }
async function refreshStorage() {
  loading.value = true
  try {
    const value = await storageApi.getStorage(controller.signal)
    if (controller.signal.aborted) return
    storage.value = value
    selected.value = selected.value.filter(id => value.sessions.some(row => row.session_id === id && (row.can_clear || row.can_release_scans)))
    error.value = ''
  } catch (reason) { if (!controller.signal.aborted) error.value = message(reason) }
  finally { loading.value = false }
}
function toggleAll(event: Event) { selected.value = (event.target as HTMLInputElement).checked ? selectableRows.value.map(row => row.session_id) : [] }
function closeDialog(value = false) {
  if (!value && !busy.value && !ops.submitting) {
    dialog.value = null; phrase.value = ''; dialogError.value = ''
    if (ops.preflight?.operation === 'restore') ops.resetFlow()
  }
}
async function beginCleanup(mode: 'release' | 'clear', rows: StorageSession[]) {
  if (busy.value || rows.length === 0) return
  busy.value = true; dialogError.value = ''; error.value = ''; phrase.value = ''; targets.value = []
  try {
    for (const row of rows) targets.value.push({ row, snapshot: await storageApi.getOriginals(row.session_id) })
    dialog.value = mode
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
async function refreshCleanup() {
  if (busy.value) return
  await refreshStorage()
  if (dialog.value === 'release' || dialog.value === 'clear') await beginCleanup(dialog.value, targets.value.map(t => t.row))
}
async function finalizeTerminalJob() {
  const job = ops.activeJob
  if (!job || !TERMINAL_JOB_STATUSES.has(job.status)) return
  const version = `${job.id}:${job.status}:${job.updated_at}`
  if (version === finalizedJobVersion) return
  finalizedJobVersion = version
  if (job.status === 'succeeded' && job.job_type.endsWith('_prepare')) await ops.recoverTrackedOperation(opsApi)
  if (job.status === 'succeeded' && job.job_type === 'ops_backup') {
    await ops.refreshBackups(opsApi)
    await refreshStorage()
    for (const target of targets.value) target.snapshot = await storageApi.getOriginals(target.row.session_id)
  }
}
async function backup() {
  if (ops.hasBlockingOperation || busy.value) return
  await ops.startPreflight({ operation: 'backup', reason: 'manual', scopes: ['grading'] }, opsApi)
  if (ops.preflight?.operation === 'backup') await ops.submitConfirmed(opsApi)
  await finalizeTerminalJob()
}
async function beginRestore() {
  if (!selectedBackup.value) return
  phrase.value = ''; dialogError.value = ''
  await ops.startPreflight({ operation: 'restore', backup_filename: selectedBackup.value }, opsApi)
  if (ops.preflight?.operation === 'restore') dialog.value = 'restore'
}
async function submitDialog() {
  if (busy.value || ops.submitting || (dialog.value === 'clear' && phrase.value !== '确认清除') || (dialog.value === 'restore' && phrase.value !== '确认恢复')) return
  if (dialog.value === 'restore') {
    await ops.submitConfirmed(opsApi)
    closeDialog()
    await finalizeTerminalJob()
    return
  }
  busy.value = true; dialogError.value = ''; notice.value = ''
  let freed = 0
  try {
    if (dialog.value === 'legacy') {
      freed = (await storageApi.clearLegacy()).freed_bytes
    } else {
      for (let index = 0; index < targets.value.length; index++) {
        const target = targets.value[index]!
        progress.value = `正在清理 ${index + 1} / ${targets.value.length} 场：${target.row.name}`
        try {
          const snapshot = await storageApi.getOriginals(target.row.session_id)
          if (snapshot.revision !== target.snapshot.revision && !(dialog.value === 'clear' && snapshot.originals_state === 'clearing')) throw new Error('考试状态已变化，请刷新后重新确认。')
          const result = dialog.value === 'release'
            ? await storageApi.releaseScans(target.row.session_id, snapshot.revision)
            : await storageApi.clearOriginals(target.row.session_id, snapshot.revision, phrase.value)
          freed += result.freed_bytes
          if (result.kept_unrendered) notice.value += `有 ${result.kept_unrendered} 个文件还没拆成页面，已保留。`
        } catch (reason) { throw new Error(`${target.row.name}：${message(reason)}`) }
      }
    }
    notice.value = `已腾出 ${bytes(freed)}。${notice.value}`
    dialog.value = null; selected.value = []
    await refreshStorage()
  } catch (reason) { dialogError.value = `${message(reason)}${freed ? ` 已腾出 ${bytes(freed)}。` : ''}请刷新后查看状态；不会自动重发。` }
  finally { busy.value = false; progress.value = '' }
}
async function downloadResult() {
  try {
    const response = await ops.downloadActive(opsApi)
    const url = URL.createObjectURL(response.blob)
    const link = document.createElement('a')
    link.href = url; link.download = /filename="?([^";]+)"?/i.exec(response.contentDisposition ?? '')?.[1] ?? '阅卷系统备份.zip'
    link.click(); URL.revokeObjectURL(url)
  } catch (reason) { error.value = message(reason) }
}
watch(() => `${ops.activeJob?.id}:${ops.activeJob?.status}:${ops.activeJob?.updated_at}`, () => { void finalizeTerminalJob().catch(reason => { error.value = message(reason) }) })
onMounted(async () => { await Promise.all([refreshStorage(), (async () => { await jobs.initialize(); await Promise.all([ops.refreshBackups(opsApi), ops.recoverTrackedOperation(opsApi)]) })()]); await finalizeTerminalJob() })
onBeforeUnmount(() => controller.abort())
</script>

<template>
  <section class="settings-data" aria-label="数据与空间">
    <div v-if="outcome" class="settings-outcome" role="status"><span>{{ outcome }}</span><AppButton v-if="ops.operationState && ['prepared', 'restart_required'].includes(ops.operationState.status)" variant="secondary" :disabled="ops.operationLoading" @click="ops.cancelCurrent(opsApi)">撤销</AppButton></div>
    <FeedbackBanner v-if="error" role="alert" tone="error" :description="error"><AppButton variant="ghost" size="small" :disabled="loading" @click="refreshStorage">刷新</AppButton></FeedbackBanner>
    <FeedbackBanner v-if="notice" role="status" tone="success" :description="notice" />
    <section class="settings-panel">
      <header class="settings-panel__heading"><h2>空间占用</h2></header>
      <div class="settings-panel__body">
        <p v-if="storage?.unreadable_count" class="settings-note">有 {{ storage.unreadable_count }} 处文件暂时无法读取，空间统计未包含这些文件。</p>
        <p v-if="!storage" class="settings-note">{{ loading ? '正在统计空间…' : '空间占用暂时无法读取' }}</p>
        <template v-else>
          <p class="settings-space-total">本机数据共 {{ bytes(storage.total_bytes) }}</p>
          <div class="settings-space-bar" aria-hidden="true"><i v-for="(category, index) in storage.categories" :key="category.key" :style="{ width: `${storage.total_bytes ? category.bytes / storage.total_bytes * 100 : 0}%`, background: colors[index] }" /></div>
          <table class="settings-space-legend"><tbody><tr v-for="(category, index) in storage.categories" :key="category.key"><td><i :style="{ background: colors[index] }" />{{ category.label }}</td><td>{{ bytes(category.bytes) }}</td></tr></tbody></table>
          <div v-if="storage.legacy_annotations.bytes" class="settings-inline settings-legacy"><span class="settings-note">旧批注图 {{ bytes(storage.legacy_annotations.bytes) }} · 可随时重新生成</span><AppButton variant="ghost" :disabled="busy" @click="dialog = 'legacy'">清理</AppButton></div>
        </template>
      </div>
    </section>
    <section class="settings-panel">
      <header class="settings-panel__heading"><h2>考试原卷</h2></header>
      <div class="settings-table-note">清除前建议先备份。<AppButton variant="ghost" size="small" @click="backupBlock?.scrollIntoView({ behavior: 'smooth', block: 'center' })">立即备份</AppButton></div>
      <div v-if="selectedRows.length" class="settings-selection-bar"><span>已选 {{ selectedRows.length }} 场 · 可腾出 {{ bytes(selectedRows.reduce((sum, row) => sum + row.clear_bytes, 0)) }}</span><div><AppButton variant="secondary" :disabled="!canReleaseBatch || busy" @click="beginCleanup('release', selectedRows)">释放扫描文件</AppButton><AppButton variant="danger" class="settings-danger-outline" :disabled="!canClearBatch || busy" @click="beginCleanup('clear', selectedRows)">清除原卷</AppButton></div></div>
      <div class="settings-table-scroll"><table class="settings-table settings-originals-table">
        <thead><tr><th><input type="checkbox" aria-label="全选可清理的考试" :checked="selectableRows.length > 0 && selected.length === selectableRows.length" :disabled="busy || selectableRows.length === 0" @change="toggleAll"></th><th>考试</th><th>状态</th><th>扫描文件</th><th>页面与批注图</th><th>原卷</th><th>操作</th></tr></thead>
        <tbody><tr v-for="row in storage?.sessions" :key="row.session_id">
          <td><input v-model="selected" type="checkbox" :value="row.session_id" :aria-label="`选择 ${row.name}`" :disabled="busy || !(row.can_release_scans || row.can_clear)"></td>
          <td><strong>{{ row.name }}</strong><small>{{ time(row.created_at).slice(0, 10) }}</small></td><td><StatusBadge :label="row.status_label" :tone="row.status_label === '已完成' ? 'success' : 'neutral'" /></td>
          <td class="settings-num">{{ row.scan_bytes ? bytes(row.scan_bytes) : '—' }}</td><td class="settings-num">{{ row.page_bytes ? bytes(row.page_bytes) : '—' }}</td><td><StatusBadge :tone="row.originals_state === 'clearing' ? 'warning' : row.originals_state === 'complete' ? 'success' : 'neutral'" :label="stateLabels[row.originals_state]" /></td>
          <td><div class="settings-row-actions"><AppButton v-if="row.scan_bytes > 0 && row.originals_state !== 'cleared' && row.originals_state !== 'clearing'" variant="secondary" :disabled="!row.can_release_scans || busy" @click="beginCleanup('release', [row])">释放扫描文件</AppButton><AppButton v-if="row.originals_state !== 'cleared'" variant="danger" class="settings-danger-outline" :disabled="!row.can_clear || busy" @click="beginCleanup('clear', [row])">{{ row.originals_state === 'clearing' ? '继续清理' : '清除原卷' }}</AppButton></div><small v-if="row.blocked_reason">{{ row.blocked_reason }}</small></td>
        </tr></tbody>
      </table></div>
      <StatePanel v-if="storage && !storage.sessions.length" kind="empty" compact title="还没有考试原卷" />
    </section>
    <section ref="backupBlock" class="settings-panel">
      <header class="settings-panel__heading"><h2>备份</h2></header>
      <div class="settings-panel__body settings-inline">
        <span>{{ backupSucceeded ? `备份完成 · ${bytes(latest?.size_bytes ?? 0)}` : `最近备份：${time(latest?.created_at)}${latest ? ` · ${bytes(latest.size_bytes)}` : ''}` }}</span>
        <AppButton variant="primary" :disabled="ops.hasBlockingOperation || busy || ops.resultUnknown" @click="backup">立即备份</AppButton>
        <template v-if="backupRunning"><progress class="settings-progress" :value="ops.activeJob?.progress ?? undefined" max="1" /><span class="settings-note">{{ ops.activeJob?.detail || '正在准备…' }}</span></template>
        <AppButton v-if="backupSucceeded" variant="secondary" @click="downloadResult">下载</AppButton>
      </div>
      <FeedbackBanner v-if="ops.activeJob?.status === 'failed'" role="alert" tone="error" :description="ops.activeJob.job_type.endsWith('_prepare') ? '恢复准备失败，业务数据尚未应用；请刷新备份清单后重新预检。' : '备份失败，没有生成可下载结果，请刷新状态后重试。'" />
      <FeedbackBanner v-if="ops.actionError" role="alert" tone="error">{{ ops.actionError.message }}；{{ ops.actionError.impact }}</FeedbackBanner>
      <FeedbackBanner v-if="ops.resultUnknown" tone="error" description="提交结果未知，请刷新状态，不要重复操作。"><AppButton variant="ghost" size="small" @click="jobs.initialize().then(() => ops.initialize(opsApi))">刷新状态</AppButton></FeedbackBanner>
    </section>
    <section class="settings-panel">
      <header class="settings-panel__heading"><h2>恢复</h2><AppButton variant="danger" class="settings-danger-outline" data-testid="preflight-restore" :disabled="!selectedBackup || ops.hasBlockingOperation || ops.resultUnknown" @click="beginRestore">恢复所选备份</AppButton></header>
      <table class="settings-table"><thead><tr><th></th><th>时间</th><th>原因</th><th>大小</th></tr></thead><tbody>
        <tr v-for="item in zipBackups" :key="item.filename"><td><input v-model="selectedBackup" type="radio" name="restore-backup" :value="item.filename" :aria-label="`选择 ${time(item.created_at)} 的备份`"></td><td>{{ time(item.created_at) }}</td><td>{{ reasons[item.reason] ?? '未记录' }}</td><td>{{ bytes(item.size_bytes) }}</td></tr>
      </tbody></table>
      <FeedbackBanner v-if="ops.backupsError" role="alert" tone="error" :description="ops.backupsError.message"><AppButton variant="ghost" size="small" @click="ops.refreshBackups(opsApi)">刷新</AppButton></FeedbackBanner><StatePanel v-else-if="!zipBackups.length" kind="empty" compact title="当前没有可恢复的备份" />
    </section>
    <AppDialog :open="dialog !== null" :title="dialogTitle" class="settings-ops-dialog" :dismissible="!(busy || ops.submitting)" @update:open="closeDialog">
        <p v-if="dialog === 'legacy'">清理后，查看批注卷时会重新生成。</p>
        <p v-if="dialog === 'release'">可腾出 {{ bytes(freedEstimate) }}。分数、查看原卷、AI 继续批改都不受影响；之后不能再重新扫描归卷。</p>
        <template v-if="dialog === 'clear'">
          <div class="settings-keep-columns"><div><strong>会保留</strong><ul><li v-for="item in ['分数和排名', '每题得分与扣分原因', '识别出的作答内容', '成绩报告', '掌握度和训练记录', '老师改分']" :key="item">{{ item }}</li></ul></div><div><strong>之后无法</strong><ul><li v-for="item in ['查看原卷和批注图', 'AI 重新批改', '重新扫描归卷', '导出批注原卷', '个人报告里的作答截图']" :key="item">{{ item }}</li></ul></div></div>
          <p>可腾出 {{ bytes(freedEstimate) }}</p>
          <div v-for="target in targets" :key="target.row.session_id" class="settings-backup-hint" :class="{ 'is-covered': target.snapshot.backup_covers_originals }">
            <span>{{ targets.length > 1 ? `${target.row.name} · ` : '' }}{{ target.snapshot.latest_backup_at ? `最近一次备份：${time(target.snapshot.latest_backup_at)}，${target.snapshot.backup_covers_originals ? '包含这场考试的原卷。' : '早于这场考试的批改或扫描文件释放，不能确认备份里包含这些原卷。'}` : '尚无可用备份，建议先备份。' }}</span>
            <AppButton v-if="!target.snapshot.backup_covers_originals" variant="secondary" :disabled="ops.hasBlockingOperation || busy || ops.resultUnknown" @click="backup">先备份</AppButton>
          </div>
        </template>
        <template v-if="dialog === 'restore'"><p>用这份备份覆盖当前同名数据。重启应用后才生效，重启前可以撤销。</p><p class="settings-note">{{ ops.preflight?.summary.file_count ?? '—' }} 个文件 · {{ bytes(ops.preflight?.summary.total_expanded_bytes ?? ops.preflight?.summary.total_size_bytes ?? 0) }}</p></template>
        <label v-if="dialog === 'clear' || dialog === 'restore'" class="settings-confirm-line"><span>输入「{{ dialog === 'clear' ? '确认清除' : '确认恢复' }}」继续</span><input v-model="phrase" class="app-input" data-testid="confirmation-phrase" autocomplete="off" :aria-label="dialog === 'clear' ? '输入确认清除' : '输入确认恢复'" :disabled="busy || ops.submitting"></label>
        <p v-if="busy" class="settings-inline" role="status"><progress class="settings-progress" />{{ progress || '正在清理…' }}</p>
        <FeedbackBanner v-if="dialogError" role="alert" tone="error" :description="dialogError"><AppButton variant="ghost" size="small" :disabled="busy" @click="refreshCleanup">刷新</AppButton></FeedbackBanner>
        <FeedbackBanner v-if="ops.actionError && dialog === 'clear'" role="alert" tone="error" :description="ops.actionError.message" />
        <template #footer><AppButton variant="secondary" :disabled="busy || ops.submitting" @click="closeDialog()">取消</AppButton><AppButton :variant="dialog === 'clear' || dialog === 'restore' ? 'danger' : 'primary'" data-testid="confirm-operation" :disabled="busy || ops.submitting || !!dialogError || (dialog === 'clear' && phrase !== '确认清除') || (dialog === 'restore' && phrase !== '确认恢复')" @click="submitDialog">{{ dialog === 'release' ? '释放' : dialog === 'clear' ? '清除原卷' : dialog === 'legacy' ? '清理' : '恢复' }}</AppButton></template>
    </AppDialog>
  </section>
</template>
