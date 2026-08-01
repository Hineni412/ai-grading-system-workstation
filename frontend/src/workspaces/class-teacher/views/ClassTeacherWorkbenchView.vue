<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import { vaultApi, type VaultStatus } from '../api/vault'
import SensitiveStudentWorkspace from '../components/SensitiveStudentWorkspace.vue'
import UnifiedWorkBoard from '../components/UnifiedWorkBoard.vue'

const vaultStatus = ref<VaultStatus | null>(null)
const loading = ref(true)
const busy = ref(false)
const message = ref('')
const errorMessage = ref('')
const sessionToken = ref('')
const recoveryKey = ref('')
const recoveryAcknowledged = ref(false)
const showRecoveryForm = ref(false)

const pin = ref('')
const pinConfirmation = ref('')
const password = ref('')
const passwordConfirmation = ref('')
const recoveryInput = ref('')
const recoveryNewPin = ref('')
const recoveryNewPinConfirmation = ref('')
const recoveryNewPassword = ref('')
const recoveryNewPasswordConfirmation = ref('')
const showPinUpgrade = ref(false)
const upgradeCurrentPassword = ref('')
const upgradePin = ref('')
const upgradePinConfirmation = ref('')

let idleHandle: ReturnType<typeof setTimeout> | null = null
let lastServerTouch = 0
const idleEvents = ['pointerdown', 'keydown', 'scroll'] as const

const initialized = computed(() => vaultStatus.value?.initialized === true)
const unlocked = computed(() => initialized.value && Boolean(sessionToken.value))
const usesPin = computed(() => (
  vaultStatus.value?.protection_mode === 'pin_dpapi_current_user_v2'
  || vaultStatus.value?.protection_mode === 'uninitialized'
))
const pinsMatch = computed(() => (
  /^\d{6}$/.test(pin.value) && pin.value === pinConfirmation.value
))
const recoveryPinsMatch = computed(() => (
  /^\d{6}$/.test(recoveryNewPin.value)
  && recoveryNewPin.value === recoveryNewPinConfirmation.value
))
const passwordsMatch = computed(() => (
  password.value.length >= 12
  && password.value === passwordConfirmation.value
))
const recoveryPasswordsMatch = computed(() => (
  recoveryNewPassword.value.length >= 12
  && recoveryNewPassword.value === recoveryNewPasswordConfirmation.value
))
const upgradePinsMatch = computed(() => (
  /^\d{6}$/.test(upgradePin.value)
  && upgradePin.value === upgradePinConfirmation.value
))
const canUnlock = computed(() => (
  usesPin.value ? /^\d{6}$/.test(pin.value) : Boolean(password.value)
))

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return '敏感保险箱没有完成本次操作，现有数据没有改变。'
}

function clearNotices(): void {
  message.value = ''
  errorMessage.value = ''
}

function clearSensitiveInputs(): void {
  pin.value = ''
  pinConfirmation.value = ''
  password.value = ''
  passwordConfirmation.value = ''
  recoveryInput.value = ''
  recoveryNewPin.value = ''
  recoveryNewPinConfirmation.value = ''
  recoveryNewPassword.value = ''
  recoveryNewPasswordConfirmation.value = ''
  upgradeCurrentPassword.value = ''
  upgradePin.value = ''
  upgradePinConfirmation.value = ''
}

function resetIdleTimer(): void {
  if (!unlocked.value) return
  if (idleHandle) clearTimeout(idleHandle)
  idleHandle = setTimeout(() => {
    void lockVault('已闲置 5 分钟，敏感保险箱已自动锁定。')
  }, (vaultStatus.value?.idle_timeout_seconds ?? 300) * 1000)
}

async function refreshStatus(): Promise<void> {
  try {
    const status = await vaultApi.status(sessionToken.value || undefined)
    vaultStatus.value = status
    if (status.locked && sessionToken.value) {
      sessionToken.value = ''
      clearSensitiveInputs()
    }
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    loading.value = false
  }
}

async function initializeVault(): Promise<void> {
  if (usesPin.value ? !pinsMatch.value : !passwordsMatch.value) return
  clearNotices()
  busy.value = true
  try {
    const pinMode = usesPin.value
    const result = pinMode
      ? await vaultApi.initializePin(pin.value)
      : await vaultApi.initialize(password.value)
    sessionToken.value = result.session_token
    recoveryKey.value = result.recovery_key
    vaultStatus.value = {
      initialized: true,
      locked: false,
      idle_timeout_seconds: result.idle_timeout_seconds,
      retry_after_seconds: 0,
      format_version: 1,
      protection_mode: pinMode ? 'pin_dpapi_current_user_v2' : 'legacy_password_v1',
      protection_state: pinMode ? 'active' : null,
      legacy_upgrade_available: !pinMode,
    }
    clearSensitiveInputs()
    resetIdleTimer()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
  }
}

async function unlockVault(): Promise<void> {
  if (!canUnlock.value) return
  clearNotices()
  busy.value = true
  try {
    const result = usesPin.value
      ? await vaultApi.unlockPin(pin.value)
      : await vaultApi.unlock(password.value)
    sessionToken.value = result.session_token
    if (result.recovery_key) recoveryKey.value = result.recovery_key
    clearSensitiveInputs()
    await refreshStatus()
    resetIdleTimer()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
  }
}

async function recoverVault(): Promise<void> {
  const replacementValid = usesPin.value
    ? recoveryPinsMatch.value
    : recoveryPasswordsMatch.value
  if (!recoveryInput.value || !replacementValid) return
  clearNotices()
  busy.value = true
  try {
    const result = usesPin.value
      ? await vaultApi.recoverPin(recoveryInput.value, recoveryNewPin.value)
      : await vaultApi.recover(recoveryInput.value, recoveryNewPassword.value)
    sessionToken.value = result.session_token
    showRecoveryForm.value = false
    clearSensitiveInputs()
    await refreshStatus()
    message.value = `${usesPin.value ? 'PIN' : '密码'}已重新设置，敏感保险箱已解锁。`
    resetIdleTimer()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
  }
}

async function finishRecoveryKeyStep(): Promise<void> {
  if (!recoveryAcknowledged.value || !sessionToken.value) return
  try {
    await vaultApi.acknowledgeRecoveryKey(sessionToken.value)
    recoveryKey.value = ''
    recoveryAcknowledged.value = false
    message.value = '恢复密钥已从页面清除。敏感区可以开始使用。'
  } catch (error) {
    errorMessage.value = errorText(error)
  }
}

async function upgradeLegacyPin(): Promise<void> {
  if (!sessionToken.value || !upgradeCurrentPassword.value || !upgradePinsMatch.value) return
  clearNotices()
  busy.value = true
  try {
    await vaultApi.upgradeLegacyToPin(
      sessionToken.value,
      upgradeCurrentPassword.value,
      upgradePin.value,
    )
    sessionToken.value = ''
    showPinUpgrade.value = false
    clearSensitiveInputs()
    if (idleHandle) clearTimeout(idleHandle)
    await refreshStatus()
    message.value = '已改为 6 位 PIN；敏感区已重新锁定，请使用新 PIN 解锁。'
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    busy.value = false
  }
}

async function lockVault(reason = '敏感保险箱已锁定，普通工作区仍可使用。'): Promise<void> {
  const token = sessionToken.value
  sessionToken.value = ''
  recoveryKey.value = ''
  clearSensitiveInputs()
  clearNotices()
  if (idleHandle) clearTimeout(idleHandle)
  try {
    if (token) await vaultApi.lock(token)
  } catch {
    // The server independently expires the same short-lived session.
  }
  message.value = reason
  await refreshStatus()
}

function onActivity(): void {
  resetIdleTimer()
  if (!sessionToken.value || Date.now() - lastServerTouch < 30_000) return
  lastServerTouch = Date.now()
  void vaultApi.touch(sessionToken.value).catch((error: unknown) => {
    if (error instanceof ApiError && error.code === 'vault_locked') void lockVault()
  })
}

function onPageHide(): void {
  const token = sessionToken.value
  sessionToken.value = ''
  recoveryKey.value = ''
  clearSensitiveInputs()
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
        <p class="workspace-heading__eyebrow">普通工作随时可用 · 学生事项单独保护</p>
        <h1>班主任工作台</h1>
        <p>先安排每天的事；需要具体学生信息时，再进入页面底部的敏感保险箱。</p>
      </div>
      <div class="vault-state" :data-state="unlocked ? 'open' : 'locked'" role="status">
        <span aria-hidden="true">{{ unlocked ? '●' : '◆' }}</span>
        {{ unlocked ? '敏感区已解锁' : '敏感区已锁定' }}
      </div>
    </header>

    <UnifiedWorkBoard />

    <p v-if="message" class="notice notice--success" role="status">{{ message }}</p>
    <p v-if="errorMessage" class="notice notice--danger" role="alert">{{ errorMessage }}</p>

    <div class="sensitive-divider">
      <div>
        <p class="section-kicker">班级名单与具体学生事项</p>
        <h2>进入本机敏感保险箱</h2>
        <p>解锁后可配置班级学生名单，或与 AI 讨论并形成经教师确认的学生摘要。</p>
      </div>
      <span>锁定时不会挂载或读取学生组件</span>
    </div>

    <section v-if="loading" class="vault-sheet vault-sheet--loading" aria-live="polite">
      正在确认敏感保险箱状态…
    </section>

    <section v-else-if="recoveryKey" class="vault-sheet vault-sheet--single">
      <p class="section-kicker">只显示这一次</p>
      <h2>请立即离线保存恢复密钥</h2>
      <p>忘记 PIN 或旧版密码时需要它。请打印或抄写，并与电脑分开保管。</p>
      <output class="recovery-code" aria-label="离线恢复密钥">{{ recoveryKey }}</output>
      <label class="check-row">
        <input v-model="recoveryAcknowledged" type="checkbox">
        <span>我已离线保存，并理解 PIN/密码和恢复密钥同时丢失时无法恢复。</span>
      </label>
      <button
        class="button button--primary"
        type="button"
        :disabled="!recoveryAcknowledged"
        @click="finishRecoveryKeyStep"
      >
        已安全保存，清除页面密钥
      </button>
    </section>

    <section v-else-if="!initialized" class="vault-sheet">
      <div class="vault-intro">
        <p class="section-kicker">首次设置</p>
        <h2>建立本机加密保险箱</h2>
        <p>新保险箱使用 6 位数字 PIN，并同时绑定当前 Windows 用户。普通备份始终排除这里。</p>
        <dl>
          <div><dt>闲置锁定</dt><dd>5 分钟</dd></div>
          <div><dt>普通备份</dt><dd>不包含</dd></div>
          <div><dt>真实模型</dt><dd>默认关闭</dd></div>
        </dl>
      </div>
      <form class="vault-form" @submit.prevent="initializeVault">
        <template v-if="usesPin">
          <h3>设置 6 位数字 PIN</h3>
          <label>
            <span>新 PIN</span>
            <input v-model="pin" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="new-password">
          </label>
          <label>
            <span>再次输入 PIN</span>
            <input v-model="pinConfirmation" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="new-password">
          </label>
          <p v-if="pinConfirmation && !pinsMatch" class="field-error">两次 PIN 需要一致，且必须正好是 6 位数字。</p>
        </template>
        <template v-else>
          <h3>设置旧版模块密码</h3>
          <label><span>新密码</span><input v-model="password" type="password" autocomplete="new-password"></label>
          <label><span>再次输入新密码</span><input v-model="passwordConfirmation" type="password" autocomplete="new-password"></label>
          <p v-if="passwordConfirmation && !passwordsMatch" class="field-error">两次密码需要一致，且至少 12 个字符。</p>
        </template>
        <button class="button button--primary" type="submit" :disabled="busy || (usesPin ? !pinsMatch : !passwordsMatch)">
          {{ busy ? '正在建立…' : '建立敏感保险箱' }}
        </button>
      </form>
    </section>

    <section v-else-if="!unlocked" class="vault-sheet">
      <div class="vault-intro">
        <p class="section-kicker">受保护空间</p>
        <h2>{{ usesPin ? '输入 6 位 PIN' : '输入旧版模块密码' }}</h2>
        <p>页面刷新、应用重启或闲置 5 分钟后会重新锁定；普通工作图不受影响。</p>
        <p v-if="!usesPin">这是此前创建的保险箱；解锁后可直接改为 6 位数字 PIN。</p>
        <p v-if="vaultStatus?.retry_after_seconds" class="notice notice--warning">
          请在 {{ vaultStatus.retry_after_seconds }} 秒后再试。
        </p>
      </div>
      <form v-if="!showRecoveryForm" class="vault-form" @submit.prevent="unlockVault">
        <label v-if="usesPin">
          <span>6 位 PIN</span>
          <input v-model="pin" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="current-password" autofocus>
        </label>
        <label v-else>
          <span>模块密码</span>
          <input v-model="password" type="password" autocomplete="current-password" autofocus>
        </label>
        <button class="button button--primary" type="submit" :disabled="busy || !canUnlock">
          {{ busy ? '正在校验…' : '解锁敏感区' }}
        </button>
        <button class="button button--text" type="button" @click="showRecoveryForm = true">
          {{ usesPin ? '忘记 PIN' : '忘记密码' }}，使用恢复密钥
        </button>
      </form>
      <form v-else class="vault-form" @submit.prevent="recoverVault">
        <h3>使用离线恢复密钥</h3>
        <label><span>恢复密钥</span><input v-model="recoveryInput" type="password" autocomplete="off"></label>
        <template v-if="usesPin">
          <label><span>设置新 PIN</span><input v-model="recoveryNewPin" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="new-password"></label>
          <label><span>再次输入新 PIN</span><input v-model="recoveryNewPinConfirmation" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="new-password"></label>
        </template>
        <template v-else>
          <label><span>设置新密码</span><input v-model="recoveryNewPassword" type="password" autocomplete="new-password"></label>
          <label><span>再次输入新密码</span><input v-model="recoveryNewPasswordConfirmation" type="password" autocomplete="new-password"></label>
        </template>
        <button class="button button--primary" type="submit" :disabled="busy || !recoveryInput || (usesPin ? !recoveryPinsMatch : !recoveryPasswordsMatch)">
          校验密钥并设置新{{ usesPin ? ' PIN' : '密码' }}
        </button>
        <button class="button button--text" type="button" @click="showRecoveryForm = false">返回解锁</button>
      </form>
    </section>

    <template v-else>
      <div class="vault-toolbar">
        <div>
          <strong>学生敏感区已解锁</strong>
          <span>真实模型仍默认关闭；每次发送前都必须核对匿名预览。</span>
        </div>
        <button class="button button--secondary" type="button" @click="lockVault()">立即锁定</button>
      </div>
      <section v-if="vaultStatus?.legacy_upgrade_available" class="pin-upgrade">
        <div>
          <p class="section-kicker">旧版保险箱</p>
          <h2>改为 6 位数字 PIN</h2>
          <p>学生记录不会改变。确认后敏感区会重新锁定，以后使用新 PIN 解锁。</p>
        </div>
        <button
          v-if="!showPinUpgrade"
          class="button button--secondary"
          type="button"
          @click="showPinUpgrade = true"
        >
          改为 6 位 PIN
        </button>
        <form v-else class="pin-upgrade__form" @submit.prevent="upgradeLegacyPin">
          <label>
            <span>再次输入当前旧密码</span>
            <input v-model="upgradeCurrentPassword" type="password" autocomplete="current-password">
          </label>
          <label>
            <span>新 PIN</span>
            <input v-model="upgradePin" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="new-password">
          </label>
          <label>
            <span>再次输入新 PIN</span>
            <input v-model="upgradePinConfirmation" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="new-password">
          </label>
          <p v-if="upgradePinConfirmation && !upgradePinsMatch" class="field-error">两次 PIN 需要一致，且必须正好是 6 位数字。</p>
          <div class="pin-upgrade__actions">
            <button class="button button--primary" type="submit" :disabled="busy || !upgradeCurrentPassword || !upgradePinsMatch">
              {{ busy ? '正在更改…' : '确认更改并锁定' }}
            </button>
            <button class="button button--text" type="button" @click="showPinUpgrade = false; clearSensitiveInputs()">取消</button>
          </div>
        </form>
      </section>
      <SensitiveStudentWorkspace
        :session-token="sessionToken"
        @activity="resetIdleTimer"
        @locked="lockVault($event)"
      />
    </template>
  </main>
</template>

<style scoped>
.class-teacher {
  width: min(100%, var(--content-max-width));
  margin: 0 auto;
  padding: var(--space-7);
  color: var(--color-text-primary);
}

.workspace-heading,
.sensitive-divider,
.vault-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-5);
}

.workspace-heading {
  margin-bottom: var(--space-7);
}

.workspace-heading h1,
.workspace-heading p,
.sensitive-divider h2,
.sensitive-divider p,
.vault-sheet h2,
.vault-sheet h3 {
  margin-top: 0;
}

.workspace-heading h1 {
  margin-bottom: var(--space-1);
  font-size: var(--font-size-h1);
}

.workspace-heading > div > p:last-child,
.vault-intro > p,
.vault-sheet--single > p {
  color: var(--color-text-secondary);
}

.workspace-heading__eyebrow,
.section-kicker {
  margin-bottom: var(--space-1);
  color: var(--color-accent-active);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  letter-spacing: .08em;
}

.vault-state {
  flex: 0 0 auto;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-tag);
  background: var(--color-warning-subtle);
  color: var(--color-warning);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-semibold);
}

.vault-state[data-state="open"] {
  background: var(--color-success-subtle);
  color: var(--color-success);
}

.pin-upgrade {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-5);
  margin: var(--space-4) 0;
  padding: var(--space-4) var(--space-5);
  border: var(--border-width) solid var(--color-warning);
  border-radius: var(--radius-panel);
  background: var(--color-warning-subtle);
}

.pin-upgrade h2,
.pin-upgrade p {
  margin-top: 0;
}

.pin-upgrade > div > p:last-child {
  margin-bottom: 0;
  color: var(--color-text-secondary);
}

.pin-upgrade__form {
  display: grid;
  grid-template-columns: repeat(3, minmax(150px, 1fr));
  align-items: end;
  gap: var(--space-3);
}

.pin-upgrade__form label {
  display: grid;
  gap: var(--space-1);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-medium);
}

.pin-upgrade__form input {
  min-height: var(--control-height-large);
}

.pin-upgrade__form .field-error,
.pin-upgrade__actions {
  grid-column: 1 / -1;
}

.pin-upgrade__actions {
  display: flex;
  gap: var(--space-2);
}

.notice {
  margin: 0 0 var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-control);
}

.notice--success {
  background: var(--color-success-subtle);
  color: var(--color-success);
}

.notice--danger {
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.notice--warning {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.sensitive-divider {
  margin: var(--space-8) 0 var(--space-4);
  padding-top: var(--space-5);
  border-top: var(--border-width) solid var(--color-border-default);
}

.sensitive-divider h2 {
  margin-bottom: 0;
  font-size: var(--font-size-h2);
}

.sensitive-divider > span {
  color: var(--color-text-muted);
  font-size: var(--font-size-dense);
}

.vault-sheet {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(320px, .8fr);
  overflow: hidden;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.vault-sheet--loading,
.vault-sheet--single {
  display: block;
  padding: var(--space-6);
}

.vault-intro {
  padding: var(--space-6);
}

.vault-intro dl {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
  margin: var(--space-5) 0 0;
  padding-top: var(--space-4);
  border-top: var(--border-width) solid var(--color-border-subtle);
}

.vault-intro dl div {
  display: grid;
  gap: var(--space-1);
}

.vault-intro dt {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.vault-intro dd {
  margin: 0;
  font-weight: var(--font-weight-semibold);
}

.vault-form {
  display: flex;
  flex-direction: column;
  align-self: center;
  gap: var(--space-4);
  margin: var(--space-5);
  padding: var(--space-5);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-subtle);
}

.vault-form label {
  display: grid;
  gap: var(--space-2);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-medium);
}

.vault-form input {
  width: 100%;
  min-height: var(--control-height-large);
}

.field-error {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--font-size-dense);
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

.button--text {
  min-height: auto;
  padding: var(--space-1);
  background: transparent;
  color: var(--color-accent);
}

.recovery-code {
  display: block;
  overflow-wrap: anywhere;
  margin: var(--space-4) 0;
  padding: var(--space-4);
  border: var(--border-width) dashed var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-accent-subtle);
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: var(--font-size-h3);
}

.check-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
  color: var(--color-text-secondary);
}

.vault-toolbar {
  margin-bottom: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-inline-start: 4px solid var(--color-success);
  background: var(--color-success-subtle);
}

.vault-toolbar div {
  display: grid;
  gap: var(--space-1);
}

.vault-toolbar span {
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

@media (max-width: 820px) {
  .class-teacher {
    padding: var(--space-5);
  }

  .workspace-heading,
  .sensitive-divider,
  .vault-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .vault-sheet,
  .vault-intro dl,
  .pin-upgrade,
  .pin-upgrade__form {
    grid-template-columns: 1fr;
  }
}
</style>
