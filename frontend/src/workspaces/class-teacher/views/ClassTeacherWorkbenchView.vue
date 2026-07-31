<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import type { WorkPlan } from '../api/actions'
import {
  vaultApi,
  type VaultBackup,
  type VaultRestorePreview,
  type VaultStatus,
} from '../api/vault'
import ActionLedgerPanel from '../components/ActionLedgerPanel.vue'
import PlanningInboxPanel from '../components/PlanningInboxPanel.vue'
import CollectionInboxPanel from '../components/CollectionInboxPanel.vue'
import SopWorkspacePanel from '../components/SopWorkspacePanel.vue'
import SupportWorkspacePanel from '../components/SupportWorkspacePanel.vue'

const vaultStatus = ref<VaultStatus | null>(null)
const loading = ref(true)
const busy = ref(false)
const message = ref('')
const errorMessage = ref('')
const sessionToken = ref('')
const recoveryKey = ref('')
const recoveryAcknowledged = ref(false)
const showRecoveryForm = ref(false)

const password = ref('')
const passwordConfirmation = ref('')
const recoveryInput = ref('')
const recoveryNewPassword = ref('')
const recoveryNewPasswordConfirmation = ref('')
const currentPassword = ref('')
const changedPassword = ref('')
const changedPasswordConfirmation = ref('')
const backupPassword = ref('')
const backups = ref<VaultBackup[]>([])
const selectedBackup = ref('')
const restoreSecret = ref('')
const restoreSecretKind = ref<'password' | 'recovery_key'>('password')
const restorePreview = ref<VaultRestorePreview | null>(null)
const restoreConfirmation = ref('')
const ledgerRefreshKey = ref(0)
const collectionRefreshKey = ref(0)
const collectionRefreshReason = ref<'action_saved' | 'subject_deleted'>('action_saved')
const supportActionPlans = ref<WorkPlan[] | null>(null)

let idleHandle: ReturnType<typeof setTimeout> | null = null
let lastServerTouch = 0
const idleEvents = ['pointerdown', 'keydown', 'scroll'] as const

const initialized = computed(() => vaultStatus.value?.initialized === true)
const unlocked = computed(() => initialized.value && Boolean(sessionToken.value))
const passwordsMatch = computed(() => (
  password.value.length >= 12
  && password.value === passwordConfirmation.value
))
const recoveryPasswordsMatch = computed(() => (
  recoveryNewPassword.value.length >= 12
  && recoveryNewPassword.value === recoveryNewPasswordConfirmation.value
))
const changedPasswordsMatch = computed(() => (
  changedPassword.value.length >= 12
  && changedPassword.value === changedPasswordConfirmation.value
))
const canConfirmRestore = computed(() => (
  restorePreview.value !== null
  && restoreConfirmation.value === '确认恢复班主任工作台'
))

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return '当前操作没有完成，现有数据没有改变。请刷新状态后再试。'
}

function clearSensitiveState(): void {
  sessionToken.value = ''
  password.value = ''
  passwordConfirmation.value = ''
  currentPassword.value = ''
  changedPassword.value = ''
  changedPasswordConfirmation.value = ''
  backupPassword.value = ''
  restoreSecret.value = ''
  restorePreview.value = null
  restoreConfirmation.value = ''
  backups.value = []
  supportActionPlans.value = null
}

function clearNotices(): void {
  errorMessage.value = ''
  message.value = ''
}

function refreshCollectionActionContext(
  reason: 'action_saved' | 'subject_deleted',
): void {
  collectionRefreshReason.value = reason
  collectionRefreshKey.value += 1
}

function handleSupportConfirmation(
  result: 'action_created' | 'no_action' | 'subject_deleted',
): void {
  ledgerRefreshKey.value += 1
  if (result === 'action_created') refreshCollectionActionContext('action_saved')
  if (result === 'subject_deleted') refreshCollectionActionContext('subject_deleted')
}

function resetIdleTimer(): void {
  if (!unlocked.value) return
  if (idleHandle) clearTimeout(idleHandle)
  idleHandle = setTimeout(() => {
    void lockVault('已闲置 5 分钟，班主任工作台已自动锁定。')
  }, vaultStatus.value?.idle_timeout_seconds
    ? vaultStatus.value.idle_timeout_seconds * 1000
    : 300_000)
}

async function refreshStatus(): Promise<void> {
  try {
    const status = await vaultApi.status(sessionToken.value || undefined)
    vaultStatus.value = status
    if (status.locked && sessionToken.value) clearSensitiveState()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    loading.value = false
  }
}

async function initializeVault(): Promise<void> {
  if (!passwordsMatch.value) return
  clearNotices()
  busy.value = true
  try {
    const result = await vaultApi.initialize(password.value)
    sessionToken.value = result.session_token
    recoveryKey.value = result.recovery_key
    vaultStatus.value = {
      initialized: true,
      locked: false,
      idle_timeout_seconds: result.idle_timeout_seconds,
      retry_after_seconds: 0,
      format_version: 1,
    }
    password.value = ''
    passwordConfirmation.value = ''
    resetIdleTimer()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
  }
}

async function unlockVault(): Promise<void> {
  clearNotices()
  busy.value = true
  try {
    const result = await vaultApi.unlock(password.value)
    sessionToken.value = result.session_token
    if (result.recovery_key) recoveryKey.value = result.recovery_key
    password.value = ''
    await Promise.all([refreshStatus(), refreshBackups()])
    resetIdleTimer()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
  }
}

async function recoverVault(): Promise<void> {
  if (!recoveryPasswordsMatch.value) return
  clearNotices()
  busy.value = true
  try {
    const result = await vaultApi.recover(
      recoveryInput.value,
      recoveryNewPassword.value,
    )
    sessionToken.value = result.session_token
    recoveryInput.value = ''
    recoveryNewPassword.value = ''
    recoveryNewPasswordConfirmation.value = ''
    showRecoveryForm.value = false
    await Promise.all([refreshStatus(), refreshBackups()])
    message.value = '密码已重新设置，保险箱已解锁。'
    resetIdleTimer()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
  }
}

async function lockVault(reason = '班主任工作台已锁定。'): Promise<void> {
  const token = sessionToken.value
  clearSensitiveState()
  clearNotices()
  if (idleHandle) clearTimeout(idleHandle)
  try {
    if (token) await vaultApi.lock(token)
  } catch {
    // The server-side session also expires independently.
  }
  message.value = reason
  await refreshStatus()
}

async function finishRecoveryKeyStep(): Promise<void> {
  if (!recoveryAcknowledged.value) return
  if (sessionToken.value) {
    try {
      await vaultApi.acknowledgeRecoveryKey(sessionToken.value)
    } catch (error) {
      errorMessage.value = errorText(error)
      return
    }
  }
  recoveryKey.value = ''
  recoveryAcknowledged.value = false
  message.value = '恢复密钥已从页面清除。保险箱可以开始使用。'
  void refreshBackups()
}

async function changePassword(): Promise<void> {
  if (!changedPasswordsMatch.value || !sessionToken.value) return
  clearNotices()
  busy.value = true
  try {
    await vaultApi.changePassword(
      sessionToken.value,
      currentPassword.value,
      changedPassword.value,
    )
    clearSensitiveState()
    message.value = '密码已修改。为保护数据，请使用新密码重新解锁。'
    await refreshStatus()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
  }
}

async function refreshBackups(): Promise<void> {
  if (!sessionToken.value) return
  try {
    backups.value = await vaultApi.listBackups(sessionToken.value)
    if (
      selectedBackup.value
      && !backups.value.some((item) => item.file_name === selectedBackup.value)
    ) selectedBackup.value = ''
  } catch (error) {
    if (error instanceof ApiError && error.code === 'vault_locked') {
      await lockVault()
      return
    }
    errorMessage.value = errorText(error)
  }
}

async function createBackup(): Promise<void> {
  if (!sessionToken.value || backupPassword.value.length < 12) return
  clearNotices()
  busy.value = true
  try {
    const result = await vaultApi.createBackup(
      sessionToken.value,
      backupPassword.value,
    )
    backupPassword.value = ''
    message.value = `专用加密备份已创建：${result.file_name}`
    await refreshBackups()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
    resetIdleTimer()
  }
}

async function previewRestore(): Promise<void> {
  if (!sessionToken.value || !selectedBackup.value || !restoreSecret.value) return
  clearNotices()
  busy.value = true
  try {
    restorePreview.value = await vaultApi.previewRestore(
      sessionToken.value,
      selectedBackup.value,
      restoreSecret.value,
      restoreSecretKind.value,
    )
    restoreSecret.value = ''
    restoreConfirmation.value = ''
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
    resetIdleTimer()
  }
}

async function confirmRestore(): Promise<void> {
  if (!sessionToken.value || !restorePreview.value || !canConfirmRestore.value) return
  clearNotices()
  busy.value = true
  try {
    await vaultApi.confirmRestore(
      sessionToken.value,
      restorePreview.value.preview_token,
    )
    clearSensitiveState()
    message.value = '专用备份已完整恢复。工作台已锁定，请重新解锁。'
    await refreshStatus()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
  }
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

function onActivity(): void {
  resetIdleTimer()
  if (!sessionToken.value || Date.now() - lastServerTouch < 30_000) return
  lastServerTouch = Date.now()
  void vaultApi.touch(sessionToken.value).catch((error: unknown) => {
    if (error instanceof ApiError && error.code === 'vault_locked') {
      void lockVault()
    }
  })
}

function onPageHide(): void {
  const token = sessionToken.value
  clearSensitiveState()
  if (token) void vaultApi.lock(token)
}

onMounted(() => {
  void refreshStatus()
  for (const event of idleEvents) window.addEventListener(event, onActivity, { passive: true })
  window.addEventListener('pagehide', onPageHide)
})

onBeforeUnmount(() => {
  if (idleHandle) clearTimeout(idleHandle)
  for (const event of idleEvents) window.removeEventListener(event, onActivity)
  window.removeEventListener('pagehide', onPageHide)
  onPageHide()
})
</script>

<template>
  <main class="class-teacher">
    <header class="workspace-heading">
      <div>
        <p class="workspace-heading__eyebrow">仅在本机 · 默认锁定</p>
        <h1>班主任工作台</h1>
        <p>学生相关内容只在保险箱解锁期间可见。首版外部模型保持关闭。</p>
      </div>
      <div
        class="vault-state"
        :data-state="unlocked ? 'open' : 'locked'"
        role="status"
      >
        <span aria-hidden="true">{{ unlocked ? '●' : '◆' }}</span>
        {{ unlocked ? '保险箱已解锁' : '保险箱已锁定' }}
      </div>
    </header>

    <p v-if="message" class="notice notice--success" role="status">{{ message }}</p>
    <p v-if="errorMessage" class="notice notice--danger" role="alert">{{ errorMessage }}</p>

    <section v-if="loading" class="vault-sheet vault-sheet--loading" aria-live="polite">
      正在确认保险箱状态…
    </section>

    <section v-else-if="recoveryKey" class="vault-sheet vault-sheet--recovery">
      <div class="seal-rail" aria-hidden="true">
        <span>一次显示</span>
      </div>
      <div class="vault-sheet__content">
        <p class="section-kicker">初始化完成</p>
        <h2>现在离线保存恢复密钥</h2>
        <p>
          这是忘记密码后唯一的恢复方式。请打印或抄写后与电脑分开保管；
          页面关闭后不会再次显示，系统也没有万能密码。
        </p>
        <output class="recovery-code" aria-label="离线恢复密钥">{{ recoveryKey }}</output>
        <label class="check-row">
          <input v-model="recoveryAcknowledged" type="checkbox">
          <span>我已将恢复密钥离线保存，并理解密码和恢复密钥同时丢失时数据无法恢复。</span>
        </label>
        <button
          class="button button--primary"
          type="button"
          :disabled="!recoveryAcknowledged"
          @click="finishRecoveryKeyStep"
        >
          已安全保存，清除页面密钥
        </button>
      </div>
    </section>

    <section v-else-if="!initialized" class="vault-sheet vault-sheet--setup">
      <div class="seal-rail" aria-hidden="true">
        <span>首次设置</span>
      </div>
      <div class="vault-intro">
        <p class="section-kicker">先保护，再记录</p>
        <h2>建立本机加密保险箱</h2>
        <p>
          初始化后才会创建班主任专用数据库。姓名、事务正文、观察、成绩和草稿不会以明文保存。
        </p>
        <dl class="protection-list">
          <div><dt>闲置锁定</dt><dd>5 分钟</dd></div>
          <div><dt>普通备份</dt><dd>始终排除</dd></div>
          <div><dt>外部模型</dt><dd>关闭</dd></div>
        </dl>
      </div>
      <form class="vault-form" @submit.prevent="initializeVault">
        <h3>设置模块密码</h3>
        <p class="field-help">至少 12 个字符，建议使用容易记住、别人难猜的长句。</p>
        <label>
          <span>新密码</span>
          <input v-model="password" type="password" autocomplete="new-password">
        </label>
        <label>
          <span>再次输入新密码</span>
          <input v-model="passwordConfirmation" type="password" autocomplete="new-password">
        </label>
        <p v-if="passwordConfirmation && !passwordsMatch" class="field-error">
          两次密码需要一致，且至少 12 个字符。
        </p>
        <button
          class="button button--primary"
          type="submit"
          :disabled="busy || !passwordsMatch"
        >
          {{ busy ? '正在建立保险箱…' : '建立加密保险箱' }}
        </button>
      </form>
    </section>

    <section v-else-if="!unlocked" class="vault-sheet vault-sheet--locked">
      <div class="seal-rail" aria-hidden="true">
        <span>已封存</span>
      </div>
      <div class="vault-intro">
        <p class="section-kicker">受保护空间</p>
        <h2>输入模块密码解锁</h2>
        <p>
          页面刷新、应用重启或闲置 5 分钟后都会回到锁定状态。
          连续输入错误只会延迟重试，不会删除数据。
        </p>
        <p v-if="vaultStatus?.retry_after_seconds" class="notice notice--warning">
          请在 {{ vaultStatus.retry_after_seconds }} 秒后再试。
        </p>
      </div>
      <form v-if="!showRecoveryForm" class="vault-form" @submit.prevent="unlockVault">
        <label>
          <span>模块密码</span>
          <input v-model="password" type="password" autocomplete="current-password" autofocus>
        </label>
        <button class="button button--primary" type="submit" :disabled="busy || !password">
          {{ busy ? '正在校验…' : '解锁工作台' }}
        </button>
        <button class="button button--text" type="button" @click="showRecoveryForm = true">
          忘记密码，使用离线恢复密钥
        </button>
      </form>
      <form v-else class="vault-form" @submit.prevent="recoverVault">
        <h3>使用离线恢复密钥</h3>
        <label>
          <span>恢复密钥</span>
          <input v-model="recoveryInput" type="password" autocomplete="off">
        </label>
        <label>
          <span>设置新密码</span>
          <input v-model="recoveryNewPassword" type="password" autocomplete="new-password">
        </label>
        <label>
          <span>再次输入新密码</span>
          <input
            v-model="recoveryNewPasswordConfirmation"
            type="password"
            autocomplete="new-password"
          >
        </label>
        <button
          class="button button--primary"
          type="submit"
          :disabled="busy || !recoveryInput || !recoveryPasswordsMatch"
        >
          校验密钥并设置新密码
        </button>
        <button class="button button--text" type="button" @click="showRecoveryForm = false">
          返回密码解锁
        </button>
      </form>
    </section>

    <template v-else>
      <div class="vault-toolbar">
        <div>
          <strong>安全存储底座已就绪</strong>
          <span>当前没有学生、事务或成绩正文。</span>
        </div>
        <button class="button button--secondary" type="button" @click="lockVault()">
          立即锁定
        </button>
      </div>

      <PlanningInboxPanel
        :session-token="sessionToken"
        @activity="resetIdleTimer"
        @confirmed="ledgerRefreshKey += 1"
        @error="errorMessage = $event"
        @locked="lockVault()"
      />

      <CollectionInboxPanel
        :session-token="sessionToken"
        :action-refresh-key="collectionRefreshKey"
        :action-refresh-reason="collectionRefreshReason"
        @activity="resetIdleTimer"
        @confirmed="ledgerRefreshKey += 1"
        @error="errorMessage = $event"
        @locked="lockVault($event)"
      />

      <SopWorkspacePanel
        :session-token="sessionToken"
        @activity="resetIdleTimer"
        @confirmed="ledgerRefreshKey += 1"
        @error="errorMessage = $event"
        @locked="lockVault()"
      />

      <SupportWorkspacePanel
        :session-token="sessionToken"
        :action-plans="supportActionPlans"
        @activity="resetIdleTimer"
        @confirmed="handleSupportConfirmation"
        @error="errorMessage = $event"
        @locked="lockVault($event)"
      />

      <ActionLedgerPanel
        :key="ledgerRefreshKey"
        :session-token="sessionToken"
        @activity="resetIdleTimer"
        @changed="refreshCollectionActionContext('action_saved')"
        @plans-changed="supportActionPlans = $event"
        @error="errorMessage = $event"
        @locked="lockVault($event)"
      />

      <div class="vault-operations">
        <section class="operation-section">
          <div class="operation-section__heading">
            <div>
              <p class="section-kicker">专用备份</p>
              <h2>创建认证加密备份</h2>
            </div>
            <span class="status-mark">普通备份不包含此处数据</span>
          </div>
          <p>
            备份密码独立于模块密码。旧备份仍需创建时的备份密码或离线恢复密钥。
          </p>
          <form class="inline-form" @submit.prevent="createBackup">
            <label>
              <span>本次备份密码</span>
              <input v-model="backupPassword" type="password" autocomplete="new-password">
            </label>
            <button
              class="button button--primary"
              type="submit"
              :disabled="busy || backupPassword.length < 12"
            >
              创建专用加密备份
            </button>
          </form>
          <div v-if="backups.length" class="backup-ledger">
            <h3>本机专用备份</h3>
            <ul>
              <li v-for="item in backups" :key="item.backup_id">
                <span>{{ formatDate(item.created_at) }}</span>
                <span>{{ formatBytes(item.size_bytes) }}</span>
                <code>{{ item.file_name }}</code>
              </li>
            </ul>
          </div>
        </section>

        <section class="operation-section">
          <div class="operation-section__heading">
            <div>
              <p class="section-kicker">两步恢复</p>
              <h2>先验证，再完整替换</h2>
            </div>
            <span class="status-mark status-mark--warning">不会自动合并</span>
          </div>
          <p>验证失败不会改变现有保险箱。来自另一实例的备份只能完整替换。</p>
          <form class="restore-form" @submit.prevent="previewRestore">
            <label>
              <span>选择备份</span>
              <select v-model="selectedBackup">
                <option value="">请选择</option>
                <option
                  v-for="item in backups"
                  :key="item.backup_id"
                  :value="item.file_name"
                >
                  {{ formatDate(item.created_at) }} · {{ formatBytes(item.size_bytes) }}
                </option>
              </select>
            </label>
            <fieldset>
              <legend>验证方式</legend>
              <label class="radio-row">
                <input v-model="restoreSecretKind" type="radio" value="password">
                备份密码
              </label>
              <label class="radio-row">
                <input v-model="restoreSecretKind" type="radio" value="recovery_key">
                离线恢复密钥
              </label>
            </fieldset>
            <label>
              <span>{{ restoreSecretKind === 'password' ? '备份密码' : '离线恢复密钥' }}</span>
              <input v-model="restoreSecret" type="password" autocomplete="off">
            </label>
            <button
              class="button button--secondary"
              type="submit"
              :disabled="busy || !selectedBackup || !restoreSecret"
            >
              验证并生成恢复预览
            </button>
          </form>
          <div v-if="restorePreview" class="restore-preview">
            <h3>恢复预览已验证</h3>
            <dl>
              <div><dt>创建时间</dt><dd>{{ formatDate(restorePreview.created_at) }}</dd></div>
              <div>
                <dt>替换方式</dt>
                <dd>{{ restorePreview.requires_complete_replacement ? '其他实例，必须完整替换' : '当前实例，完整替换' }}</dd>
              </div>
            </dl>
            <label>
              <span>输入“确认恢复班主任工作台”</span>
              <input v-model="restoreConfirmation" autocomplete="off">
            </label>
            <button
              class="button button--danger"
              type="button"
              :disabled="busy || !canConfirmRestore"
              @click="confirmRestore"
            >
              完整替换并锁定
            </button>
          </div>
        </section>

        <section class="operation-section">
          <div class="operation-section__heading">
            <div>
              <p class="section-kicker">访问保护</p>
              <h2>修改模块密码</h2>
            </div>
          </div>
          <p>修改密码只重新保护保险箱主密钥，不会逐条改写将来的学生记录。</p>
          <form class="vault-form vault-form--compact" @submit.prevent="changePassword">
            <label>
              <span>当前密码</span>
              <input v-model="currentPassword" type="password" autocomplete="current-password">
            </label>
            <label>
              <span>新密码</span>
              <input v-model="changedPassword" type="password" autocomplete="new-password">
            </label>
            <label>
              <span>再次输入新密码</span>
              <input
                v-model="changedPasswordConfirmation"
                type="password"
                autocomplete="new-password"
              >
            </label>
            <button
              class="button button--secondary"
              type="submit"
              :disabled="busy || !currentPassword || !changedPasswordsMatch"
            >
              修改密码并锁定
            </button>
          </form>
        </section>
      </div>
    </template>
  </main>
</template>

<style scoped>
.class-teacher {
  width: min(100%, var(--content-max-width));
  margin: 0 auto;
  padding: var(--space-7);
}

.workspace-heading,
.vault-toolbar,
.operation-section__heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-5);
}

.workspace-heading {
  margin-bottom: var(--space-6);
}

.workspace-heading h1,
.vault-sheet h2,
.operation-section h2 {
  margin: 0;
  color: var(--color-text-primary);
}

.workspace-heading h1 {
  font-size: var(--font-size-h1);
  line-height: var(--line-height-tight);
}

.workspace-heading p,
.vault-sheet p,
.operation-section p,
.vault-toolbar span {
  color: var(--color-text-secondary);
  line-height: var(--line-height-body);
}

.workspace-heading p {
  margin: var(--space-1) 0 0;
}

.workspace-heading__eyebrow,
.section-kicker {
  margin: 0 0 var(--space-1) !important;
  color: var(--color-accent-active) !important;
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.vault-state {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-height-default);
  padding: 0 var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-medium);
}

.vault-state[data-state="open"] {
  border-color: var(--color-success);
  background: var(--color-success-subtle);
  color: var(--color-success);
}

.notice {
  margin: 0 0 var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-inline-start: var(--border-selected-width) solid;
  border-radius: var(--radius-control);
  line-height: var(--line-height-body);
}

.notice--success {
  border-color: var(--color-success);
  background: var(--color-success-subtle);
  color: var(--color-text-primary);
}

.notice--danger {
  border-color: var(--color-danger);
  background: var(--color-danger-subtle);
  color: var(--color-text-primary);
}

.notice--warning {
  border-color: var(--color-warning);
  background: var(--color-warning-subtle);
}

.vault-sheet {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 420px);
  min-height: 410px;
  overflow: hidden;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.vault-sheet--loading {
  display: block;
  min-height: 180px;
  padding: var(--space-7);
  color: var(--color-text-secondary);
}

.vault-sheet--recovery {
  display: block;
}

.seal-rail {
  position: absolute;
  inset: 0 auto 0 0;
  display: flex;
  width: 44px;
  align-items: center;
  justify-content: center;
  border-inline-end: var(--border-width) solid var(--color-border-default);
  background: var(--color-accent-subtle);
  color: var(--color-accent-active);
}

.seal-rail span {
  writing-mode: vertical-rl;
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  letter-spacing: 0.18em;
}

.vault-intro,
.vault-sheet__content {
  padding: var(--space-8) var(--space-8) var(--space-8) calc(var(--space-8) + 44px);
}

.vault-sheet__content {
  max-width: 760px;
}

.vault-sheet h2,
.operation-section h2 {
  font-size: var(--font-size-h2);
  line-height: var(--line-height-tight);
}

.protection-list {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
  margin: var(--space-7) 0 0;
  padding-top: var(--space-5);
  border-top: var(--border-width) solid var(--color-border-subtle);
}

.protection-list div {
  display: grid;
  gap: var(--space-1);
}

.protection-list dt {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.protection-list dd {
  margin: 0;
  color: var(--color-text-primary);
  font-weight: var(--font-weight-semibold);
}

.vault-form {
  display: flex;
  flex-direction: column;
  align-self: center;
  gap: var(--space-4);
  margin: var(--space-7);
  padding: var(--space-6);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-subtle);
}

.vault-form h3,
.backup-ledger h3,
.restore-preview h3 {
  margin: 0;
  font-size: var(--font-size-h3);
}

.vault-form--compact {
  max-width: 520px;
  margin: var(--space-5) 0 0;
}

.field-help,
.field-error {
  margin: calc(-1 * var(--space-2)) 0 0;
  font-size: var(--font-size-dense);
}

.field-error {
  color: var(--color-danger);
}

label:not(.check-row, .radio-row) {
  display: grid;
  gap: var(--space-2);
  color: var(--color-text-primary);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-medium);
}

input,
select {
  width: 100%;
  min-height: var(--control-height-large);
}

.button {
  min-height: var(--control-height-large);
  padding: 0 var(--space-4);
  border: var(--border-width) solid transparent;
  border-radius: var(--radius-control);
  font: inherit;
  font-weight: var(--font-weight-medium);
  cursor: pointer;
}

.button:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.button:disabled {
  cursor: not-allowed;
  opacity: var(--opacity-disabled);
}

.button--primary {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: var(--color-bg-surface);
}

.button--secondary {
  border-color: var(--color-border-strong);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.button--danger {
  border-color: var(--color-danger);
  background: var(--color-danger);
  color: var(--color-bg-surface);
}

.button--text {
  min-height: auto;
  padding: var(--space-1);
  background: transparent;
  color: var(--color-accent);
}

.check-row,
.radio-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  line-height: var(--line-height-body);
}

.check-row input,
.radio-row input {
  width: auto;
  min-height: auto;
  margin-top: 3px;
}

.recovery-code {
  display: block;
  overflow-wrap: anywhere;
  margin: var(--space-6) 0;
  padding: var(--space-5);
  border: var(--border-width) dashed var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-accent-subtle);
  color: var(--color-accent-active);
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: var(--font-size-h3);
  line-height: var(--line-height-relaxed);
  letter-spacing: 0.04em;
}

.vault-toolbar {
  align-items: center;
  margin-bottom: var(--space-5);
  padding: var(--space-4) var(--space-5);
  border-inline-start: var(--border-selected-width) solid var(--color-success);
  background: var(--color-success-subtle);
}

.vault-toolbar div {
  display: grid;
  gap: var(--space-1);
}

.vault-toolbar span {
  font-size: var(--font-size-dense);
}

.vault-operations {
  border-top: var(--border-width) solid var(--color-border-default);
}

.operation-section {
  padding: var(--space-7) 0;
  border-bottom: var(--border-width) solid var(--color-border-default);
}

.operation-section > p {
  max-width: 760px;
}

.status-mark {
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-tag);
  background: var(--color-success-subtle);
  color: var(--color-success);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
}

.status-mark--warning {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.inline-form {
  display: grid;
  grid-template-columns: minmax(260px, 420px) auto;
  align-items: end;
  gap: var(--space-3);
  margin-top: var(--space-5);
}

.backup-ledger {
  margin-top: var(--space-6);
}

.backup-ledger ul {
  margin: var(--space-3) 0 0;
  padding: 0;
  border-top: var(--border-width) solid var(--color-border-subtle);
  list-style: none;
}

.backup-ledger li {
  display: grid;
  grid-template-columns: 190px 90px minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-3) 0;
  border-bottom: var(--border-width) solid var(--color-border-subtle);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.backup-ledger code {
  overflow-wrap: anywhere;
  color: var(--color-text-primary);
}

.restore-form {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) minmax(200px, 0.7fr);
  gap: var(--space-4);
  max-width: 820px;
  margin-top: var(--space-5);
}

.restore-form fieldset {
  display: flex;
  gap: var(--space-4);
  margin: 0;
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
}

.restore-form legend {
  padding: 0 var(--space-1);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.restore-preview {
  max-width: 820px;
  margin-top: var(--space-5);
  padding: var(--space-5);
  border: var(--border-width) solid var(--color-warning);
  border-radius: var(--radius-panel);
  background: var(--color-warning-subtle);
}

.restore-preview dl {
  display: grid;
  gap: var(--space-2);
}

.restore-preview dl div {
  display: grid;
  grid-template-columns: 110px 1fr;
}

.restore-preview dt {
  color: var(--color-text-secondary);
}

.restore-preview dd {
  margin: 0;
  color: var(--color-text-primary);
}

.restore-preview .button {
  margin-top: var(--space-4);
}

@media (max-width: 820px) {
  .class-teacher {
    padding: var(--space-5);
  }

  .workspace-heading,
  .operation-section__heading {
    flex-direction: column;
  }

  .vault-sheet {
    grid-template-columns: 1fr;
  }

  .vault-intro {
    padding-bottom: var(--space-4);
  }

  .vault-form {
    margin-top: 0;
  }

  .protection-list,
  .inline-form,
  .restore-form {
    grid-template-columns: 1fr;
  }

  .backup-ledger li {
    grid-template-columns: 1fr;
    gap: var(--space-1);
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    transition-duration: var(--duration-reduced) !important;
  }
}
</style>
