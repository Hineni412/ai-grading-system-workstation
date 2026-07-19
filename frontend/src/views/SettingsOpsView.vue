<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { opsApi, type OpsOperation, type OpsPreflightRequest } from '../api/ops'
import { TERMINAL_JOB_STATUSES } from '../api/jobs'
import { useJobStore } from '../stores/jobs'
import { useOpsStore } from '../stores/ops'

const ops = useOpsStore()
const jobs = useJobStore()

const confirmationPhrase = ref('')
const copied = ref(false)
const selectedBackup = ref('')
const backupReason = ref<'before_exam' | 'before_update' | 'before_import' | 'before_restore' | 'manual' | 'after_exam'>('manual')
const migrationTarget = ref<'grading' | 'question_bank' | 'all'>('all')
const exportScope = ref<'lean' | 'full'>('lean')
let finalizedJobVersion = ''

const OPERATION_LABELS: Record<OpsOperation, string> = {
  backup: '创建备份',
  restore: '恢复备份',
  migration: '数据库迁移',
  transfer_import: '导入数据包',
  transfer_export: '导出数据包',
}

const CONFIRMATION_PHRASES: Record<OpsOperation, string> = {
  backup: '确认备份',
  restore: '确认恢复',
  migration: '确认迁移',
  transfer_import: '确认导入',
  transfer_export: '确认导出',
}

const DIRECTORY_LABELS: Record<string, string> = {
  data: '数据目录',
  databases: '数据库目录',
  backups: '备份目录',
  logs: '日志目录',
  reports: '报告目录',
  outputs: '输出目录',
}

const DATABASE_LABELS: Record<string, string> = {
  grading: '阅卷数据库',
  question_bank: '题库数据库',
}

const TOOL_LABELS: Record<string, string> = {
  microsoft_word: 'Microsoft Word',
  libreoffice: 'LibreOffice',
  pdflatex: 'PDFLaTeX',
}

const requiredPhrase = computed(() => {
  const operation = ops.preflight?.operation
  return operation ? CONFIRMATION_PHRASES[operation] : ''
})

const canConfirm = computed(() =>
  Boolean(ops.preflight)
  && confirmationPhrase.value === requiredPhrase.value
  && !ops.submitting,
)

const zipBackups = computed(() => ops.backups.filter((item) => item.kind === 'zip'))

const operationOutcome = computed(() => {
  const status = ops.operationState?.status
  if (status === 'prepared' || status === 'restart_required') {
    return {
      tone: 'warning',
      title: '准备完成，尚未应用',
      detail: '系统将在下次启动应用前执行。在真正应用前，你仍可以撤销。',
    }
  }
  if (status === 'applying') {
    return {
      tone: 'warning',
      title: '正在应用操作',
      detail: '现在不能撤销。请保持应用运行，等待系统给出最终结果。',
    }
  }
  if (status === 'applied') {
    return {
      tone: 'success',
      title: '操作已经应用',
      detail: '系统已确认本次离线操作生效。',
    }
  }
  if (status === 'rolled_back') {
    return {
      tone: 'danger',
      title: '操作未生效，系统已回退',
      detail: '原有数据已保留。请查看诊断信息后再决定是否重试。',
    }
  }
  if (status === 'failed') {
    return {
      tone: 'danger',
      title: '操作失败，未确认生效',
      detail: '请先停止重复操作，并刷新系统状态。',
    }
  }
  if (status === 'cancelled') {
    return {
      tone: 'info',
      title: '待执行操作已撤销',
      detail: '本次准备内容不会在下次启动时应用。',
    }
  }
  return null
})

function statusText(status: string): string {
  if (status === 'ok') return '正常'
  if (status === 'warning') return '需注意'
  return '不可用'
}

function bytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function dateTime(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN')
}

function jobTitle(status: string): string {
  if (status === 'succeeded') return '任务处理完成'
  if (status === 'failed') return '任务执行失败'
  if (status === 'cancelled') return '任务已取消'
  if (status === 'paused') return '任务已暂停'
  return '任务正在处理'
}

function jobDetail(status: string, detail: string): string {
  if (status === 'failed') return '本次任务没有生成可下载结果，也不会自动重试。请刷新状态后重新预检。'
  if (status === 'cancelled') return '本次任务已停止，没有生成新的运维结果。'
  return detail || '系统正在记录并执行已确认的操作。'
}

async function refreshLedger(): Promise<void> {
  await Promise.all([ops.refreshSelfCheck(opsApi), ops.refreshBackups(opsApi)])
}

async function copyDiagnostic(): Promise<void> {
  if (!ops.diagnosticText) return
  await navigator.clipboard.writeText(ops.diagnosticText)
  copied.value = true
}

function begin(request: OpsPreflightRequest): void {
  confirmationPhrase.value = ''
  void ops.startPreflight(request, opsApi)
}

function preflightRestore(): void {
  if (!selectedBackup.value) return
  begin({ operation: 'restore', backup_filename: selectedBackup.value })
}

async function selectImport(event: Event): Promise<void> {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  confirmationPhrase.value = ''
  await ops.stageImport(file, opsApi)
}

async function confirmOperation(): Promise<void> {
  if (!canConfirm.value) return
  await ops.submitConfirmed(opsApi)
  confirmationPhrase.value = ''
  await finalizeTerminalJob()
}

async function finalizeTerminalJob(): Promise<void> {
  const job = ops.activeJob
  if (!job || !TERMINAL_JOB_STATUSES.has(job.status)) return
  const version = `${job.id}:${job.status}:${job.updated_at}`
  if (version === finalizedJobVersion) return
  finalizedJobVersion = version
  if (job.status === 'succeeded' && job.job_type.endsWith('_prepare')) {
    await ops.recoverTrackedOperation(opsApi)
  }
  if (job.status === 'succeeded' && job.job_type === 'ops_backup') {
    await ops.refreshBackups(opsApi)
  }
}

async function downloadResult(): Promise<void> {
  const response = await ops.downloadActive(opsApi)
  const url = URL.createObjectURL(response.blob)
  const link = document.createElement('a')
  const disposition = response.contentDisposition ?? ''
  const filename = /filename="?([^";]+)"?/i.exec(disposition)?.[1] ?? '阅卷系统导出.zip'
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

watch(
  () => `${ops.activeJob?.id ?? ''}:${ops.activeJob?.status ?? ''}:${ops.activeJob?.updated_at ?? ''}`,
  () => void finalizeTerminalJob(),
)

onMounted(async () => {
  await jobs.initialize()
  await ops.initialize(opsApi)
  if (!selectedBackup.value) selectedBackup.value = zipBackups.value[0]?.filename ?? ''
})
</script>

<template>
  <section class="settings-ops">
    <header class="settings-ops__header">
      <div>
        <p class="settings-ops__eyebrow">System control ledger</p>
        <h1 tabindex="-1">设置与运维</h1>
        <p>先查看系统健康状态，再通过预检和明确确认执行受保护操作。</p>
      </div>
      <button class="ops-button is-secondary" type="button" :disabled="ops.selfCheckLoading || ops.backupsLoading" @click="refreshLedger">
        {{ ops.selfCheckLoading || ops.backupsLoading ? '正在刷新…' : '刷新状态' }}
      </button>
    </header>

    <div class="settings-ops__layout">
      <main class="ops-ledger">
        <section class="ops-section" aria-labelledby="system-ledger-title">
          <div class="ops-section__heading">
            <div>
              <p class="settings-ops__eyebrow">Read-only status</p>
              <h2 id="system-ledger-title">系统状态账本</h2>
            </div>
            <button
              class="ops-button is-secondary"
              type="button"
              data-testid="copy-diagnostic"
              :disabled="!ops.diagnosticText"
              @click="copyDiagnostic"
            >
              复制脱敏诊断
            </button>
          </div>

          <p v-if="copied" class="ops-feedback is-success" role="status">已复制脱敏诊断</p>
          <p v-if="ops.selfCheckError" class="ops-feedback is-warning" role="alert">
            {{ ops.selfCheckError.message }}；{{ ops.selfCheckError.impact }}
          </p>

          <template v-if="ops.selfCheck">
            <div class="ops-overview">
              <div>
                <span>系统版本</span>
                <strong>{{ ops.selfCheck.version }}</strong>
              </div>
              <div>
                <span>整体状态</span>
                <strong :class="`is-${ops.selfCheck.status}`">{{ statusText(ops.selfCheck.status) }}</strong>
              </div>
              <div>
                <span>接口配置</span>
                <strong :class="ops.selfCheck.api_configured ? 'is-ok' : 'is-warning'">
                  {{ ops.selfCheck.api_configured ? 'API 配置已完成' : 'API 配置未完成' }}
                </strong>
              </div>
            </div>

            <div class="ops-check-groups">
              <div>
                <h3>数据目录</h3>
                <ul class="ops-status-list">
                  <li v-for="item in ops.selfCheck.directories" :key="item.key">
                    <span>{{ DIRECTORY_LABELS[item.key] ?? item.key }}</span>
                    <strong :class="`is-${item.status}`">{{ statusText(item.status) }}</strong>
                  </li>
                </ul>
              </div>
              <div>
                <h3>数据库</h3>
                <ul class="ops-status-list">
                  <li v-for="item in ops.selfCheck.databases" :key="item.key">
                    <span>{{ DATABASE_LABELS[item.key] ?? item.key }}</span>
                    <strong :class="`is-${item.status}`">
                      {{ item.integrity === 'ok' ? '完整' : '需检查' }} · 待迁移 {{ item.pending_migrations }}
                    </strong>
                  </li>
                </ul>
              </div>
              <div>
                <h3>外部工具</h3>
                <ul class="ops-status-list">
                  <li v-for="item in ops.selfCheck.tools" :key="item.key">
                    <span>{{ TOOL_LABELS[item.key] ?? item.key }}</span>
                    <strong :class="item.available ? 'is-ok' : 'is-warning'">
                      {{ item.available ? '可用' : '不可用' }}
                    </strong>
                  </li>
                </ul>
              </div>
            </div>
          </template>
        </section>

        <section class="ops-section" aria-labelledby="backup-ledger-title">
          <div class="ops-section__heading">
            <div>
              <p class="settings-ops__eyebrow">Recovery inventory</p>
              <h2 id="backup-ledger-title">备份清单</h2>
            </div>
            <span>{{ ops.backups.length }} 项</span>
          </div>
          <p v-if="ops.backupsError" class="ops-feedback is-warning" role="alert">
            {{ ops.backupsError.message }}；{{ ops.backupsError.impact }}
          </p>
          <div class="ops-table-wrap">
            <table>
              <thead><tr><th>类型</th><th>文件</th><th>时间</th><th>大小</th></tr></thead>
              <tbody>
                <tr v-for="item in ops.backups" :key="`${item.kind}:${item.filename}`">
                  <td>{{ item.kind === 'zip' ? '完整备份' : '数据库快照' }}</td>
                  <td>{{ item.filename }}</td>
                  <td>{{ dateTime(item.created_at) }}</td>
                  <td>{{ bytes(item.size_bytes) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="ops-section" aria-labelledby="operations-title">
          <div class="ops-section__heading">
            <div>
              <p class="settings-ops__eyebrow">Protected operations</p>
              <h2 id="operations-title">受保护操作</h2>
            </div>
            <span>所有操作均需预检</span>
          </div>

          <article class="ops-action">
            <div><h3>创建备份</h3><p>将当前数据制作成可下载的完整备份。</p></div>
            <label>备份原因
              <select v-model="backupReason">
                <option value="manual">手动备份</option>
                <option value="before_exam">考试前</option>
                <option value="after_exam">考试后</option>
                <option value="before_update">更新前</option>
                <option value="before_import">导入前</option>
              </select>
            </label>
            <button class="ops-button is-secondary" type="button" @click="begin({ operation: 'backup', reason: backupReason })">开始预检</button>
          </article>

          <article class="ops-action is-danger">
            <div><h3>恢复备份</h3><p>准备恢复完整 ZIP 备份；重启前不会改动现有数据。</p></div>
            <fieldset>
              <legend>选择完整备份</legend>
              <label v-for="item in zipBackups" :key="item.filename" class="ops-radio">
                <input v-model="selectedBackup" type="radio" name="restore-backup" :value="item.filename">
                <span>{{ item.filename }}</span>
              </label>
            </fieldset>
            <button class="ops-button is-danger" type="button" data-testid="preflight-restore" :disabled="!selectedBackup" @click="preflightRestore">检查恢复影响</button>
          </article>

          <article class="ops-action is-danger">
            <div><h3>数据库迁移</h3><p>检查并准备数据库结构更新；在重启应用后执行。</p></div>
            <label>迁移范围
              <select v-model="migrationTarget">
                <option value="all">全部数据库</option>
                <option value="grading">阅卷数据库</option>
                <option value="question_bank">题库数据库</option>
              </select>
            </label>
            <button class="ops-button is-danger" type="button" @click="begin({ operation: 'migration', target: migrationTarget })">检查迁移影响</button>
          </article>

          <article class="ops-action">
            <div><h3>导出数据包</h3><p>制作便于迁移的数据包；敏感配置不会写入数据包。</p></div>
            <label>导出范围
              <select v-model="exportScope">
                <option value="lean">精简数据</option>
                <option value="full">完整数据</option>
              </select>
            </label>
            <button class="ops-button is-secondary" type="button" @click="begin({ operation: 'transfer_export', scope: exportScope })">开始预检</button>
          </article>

          <article class="ops-action is-danger">
            <div><h3>导入数据包</h3><p>先上传 ZIP 数据包并检查内容；通过后才可准备导入。</p></div>
            <label class="ops-file">
              <input type="file" accept=".zip,application/zip" :disabled="ops.uploadLoading" @change="selectImport">
              <span>{{ ops.uploadLoading ? '正在安全检查文件…' : '选择 ZIP 数据包' }}</span>
              <strong v-if="ops.importUpload">{{ ops.importUpload.filename }}</strong>
            </label>
            <button
              class="ops-button is-danger"
              type="button"
              data-testid="preflight-import"
              :disabled="!ops.importUpload"
              @click="ops.importUpload && begin({ operation: 'transfer_import', upload_id: ops.importUpload.upload_id })"
            >
              检查导入影响
            </button>
          </article>
        </section>
      </main>

      <aside class="ops-gate" data-testid="ops-safety-gate" aria-labelledby="ops-gate-title">
        <p class="settings-ops__eyebrow">Single safety gate</p>
        <h2 id="ops-gate-title">安全闸门</h2>

        <ol class="ops-gate__steps">
          <li :class="{ 'is-current': !ops.preflight && !ops.activeJob && !ops.operationState }"><span>1</span>选择操作</li>
          <li :class="{ 'is-current': Boolean(ops.preflight) }"><span>2</span>核对预检</li>
          <li :class="{ 'is-current': Boolean(ops.activeJob) && !ops.operationState }"><span>3</span>等待任务</li>
          <li :class="{ 'is-current': Boolean(ops.operationState) }"><span>4</span>确认结果</li>
        </ol>

        <p v-if="!ops.preflight && !ops.activeJob && !ops.operationState" class="ops-gate__empty">
          从左侧选择一项操作。预检只展示影响，不会直接执行。
        </p>

        <div v-if="ops.preflight" class="ops-gate__preflight">
          <span class="ops-state is-success">预检完成</span>
          <h3>{{ OPERATION_LABELS[ops.preflight.operation] }}</h3>
          <dl>
            <template v-if="ops.preflight.summary.file_count !== null"><dt>文件</dt><dd>{{ ops.preflight.summary.file_count }} 个</dd></template>
            <template v-if="ops.preflight.summary.database_count !== null"><dt>数据库</dt><dd>{{ ops.preflight.summary.database_count }} 个</dd></template>
            <template v-if="ops.preflight.summary.pending_migrations !== null"><dt>待迁移</dt><dd>{{ ops.preflight.summary.pending_migrations }} 项</dd></template>
            <template v-if="ops.preflight.summary.skipped_count !== null"><dt>跳过</dt><dd>{{ ops.preflight.summary.skipped_count }} 项</dd></template>
          </dl>
          <p v-if="ops.preflight.requires_restart" class="ops-feedback is-warning">
            本次操作需要重启应用后才会生效
          </p>
          <p class="ops-confirm-copy">输入“<strong>{{ requiredPhrase }}</strong>”以确认本次操作：</p>
          <input
            v-model="confirmationPhrase"
            data-testid="confirmation-phrase"
            autocomplete="off"
            :aria-label="`输入${requiredPhrase}`"
          >
          <button
            class="ops-button is-danger is-full"
            type="button"
            data-testid="confirm-operation"
            :disabled="!canConfirm"
            @click="confirmOperation"
          >
            {{ ops.submitting ? '正在提交…' : requiredPhrase }}
          </button>
        </div>

        <div v-if="ops.activeJob && !ops.operationState" class="ops-gate__job" aria-live="polite">
          <span class="ops-state is-info">运维任务 #{{ ops.activeJob.id }}</span>
          <h3>{{ jobTitle(ops.activeJob.status) }}</h3>
          <progress :value="ops.activeJob.progress" max="1">
            {{ Math.round(ops.activeJob.progress * 100) }}%
          </progress>
          <p>{{ jobDetail(ops.activeJob.status, ops.activeJob.detail) }}</p>
          <button
            v-if="!TERMINAL_JOB_STATUSES.has(ops.activeJob.status)"
            class="ops-button is-secondary is-full"
            type="button"
            @click="ops.cancelCurrent(opsApi)"
          >
            请求取消任务
          </button>
          <button
            v-if="ops.activeJob.status === 'succeeded' && ['ops_backup', 'ops_transfer_export'].includes(ops.activeJob.job_type)"
            class="ops-button is-primary is-full"
            type="button"
            @click="downloadResult"
          >
            下载结果
          </button>
        </div>

        <div v-if="operationOutcome" :class="['ops-gate__outcome', `is-${operationOutcome.tone}`]" aria-live="polite">
          <span class="ops-state" :class="`is-${operationOutcome.tone}`">离线操作状态</span>
          <h3>{{ operationOutcome.title }}</h3>
          <p>{{ operationOutcome.detail }}</p>
          <button
            v-if="ops.operationState && ['prepared', 'restart_required'].includes(ops.operationState.status)"
            class="ops-button is-danger is-full"
            type="button"
            data-testid="cancel-prepared-operation"
            :disabled="ops.operationLoading"
            @click="ops.cancelCurrent(opsApi)"
          >
            撤销待重启操作
          </button>
          <button
            v-if="ops.operationState && !['prepared', 'restart_required', 'applying'].includes(ops.operationState.status)"
            class="ops-button is-secondary is-full"
            type="button"
            @click="ops.resetFlow"
          >
            完成并关闭
          </button>
        </div>

        <div v-if="ops.actionError" class="ops-feedback is-danger" role="alert">
          <strong>{{ ops.actionError.message }}</strong>
          <span>{{ ops.actionError.impact }}</span>
          <small v-if="ops.actionError.requestId">请求编号：{{ ops.actionError.requestId }}</small>
        </div>
        <p v-if="ops.resultUnknown" class="ops-feedback is-danger">
          不要重复提交。请先刷新状态，确认系统是否已收到操作。
        </p>
      </aside>
    </div>
  </section>
</template>
