<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { intakeApi, type SpeechCapabilities } from '../api/intake'
import { browserVoiceRecordingSupported, createVoiceRecorder, type VoiceRecorder } from './browserVoiceRecorder'

type VoiceMode = 'local' | 'cloud'

const props = defineProps<{
  disabled?: boolean
  contextKey?: string | null
  submitCloudAudio?: (wav: Blob, operationId: string, fingerprint: string, signal: AbortSignal) => Promise<boolean>
}>()
const emit = defineEmits<{
  transcript: [text: string]
  info: [message: string]
  error: [message: string]
  busyChanged: [busy: boolean]
}>()

const capabilities = ref<SpeechCapabilities | null>(null)
const mode = ref<VoiceMode>('local')
const starting = ref(false)
const recording = ref(false)
const transcribing = ref(false)
const cloudSending = ref(false)
const pendingAudio = ref<{ wav: Blob; seconds: number; operationId: string; attempted: boolean } | null>(null)
const elapsedSeconds = ref(0)
let recorder: VoiceRecorder | null = null
let ticker: number | null = null
let limitTimer: number | null = null
let activeController: AbortController | null = null
let operationVersion = 0

const label = computed(() => {
  if (cloudSending.value) return '正在发送录音'
  if (transcribing.value) return '正在转写'
  if (starting.value) return '等待麦克风'
  if (recording.value) return '停止录音'
  return '语音输入'
})
const timeLabel = computed(() => `00:${String(elapsedSeconds.value % 60).padStart(2, '0')} / 01:00`)
const statusLabel = computed(() => {
  if (starting.value) return '等待麦克风权限…'
  if (recording.value) return `正在录音 ${timeLabel.value}`
  if (transcribing.value) return '正在本机转写…'
  if (cloudSending.value) return '正在发送给模型…'
  if (pendingAudio.value) return `录音 ${pendingAudio.value.seconds} 秒，尚未发送`
  return ''
})
const busy = computed(() => starting.value || recording.value || transcribing.value || cloudSending.value || Boolean(pendingAudio.value))

function setBusy(): void {
  emit('busyChanged', busy.value)
}

function clearTimers(): void {
  if (ticker !== null) window.clearInterval(ticker)
  if (limitTimer !== null) window.clearTimeout(limitTimer)
  ticker = null
  limitTimer = null
}

async function loadCapabilities(): Promise<void> {
  try { capabilities.value = await intakeApi.speechCapabilities() }
  catch { capabilities.value = null }
}

function localUnavailableMessage(value: SpeechCapabilities | null): string {
  if (!browserVoiceRecordingSupported()) return '当前浏览器不能安全录制本地语音，请继续使用文字输入。'
  if (value?.status === 'dependency_missing') return '本地语音组件尚未安装，文字输入仍可正常使用。'
  if (value?.status === 'model_missing') return '本地语音模型尚未安装，文字输入仍可正常使用。'
  return '本地语音输入暂时不可用，文字输入仍可正常使用。'
}

function cloudUnavailableMessage(): string {
  return '请先在模型配置中设置班主任工作台模型；该模型需要支持语音输入。'
}

function transcriptionError(value: unknown): string {
  if (value instanceof DOMException && value.name === 'NotAllowedError') return '没有获得麦克风权限；你可以在浏览器地址栏重新允许。'
  if (value instanceof DOMException && value.name === 'NotFoundError') return '没有找到可用麦克风，请检查设备连接。'
  if (value && typeof value === 'object' && 'code' in value) {
    const code = String((value as { code: unknown }).code)
    if (code === 'speech_no_text') return '没有识别到清晰语音，请靠近麦克风后重试。'
    if (code === 'speech_engine_unavailable' || code === 'speech_engine_load_failed') return '本地语音组件没有成功启动，已有文字不会丢失。'
    if (code === 'speech_audio_too_long') return '单次录音不能超过 60 秒，请分段录入。'
  }
  return '本地语音转写没有完成，已有文字不会丢失。'
}

function chooseMode(next: VoiceMode): void {
  if (busy.value || props.disabled || next === mode.value) return
  mode.value = next
}

async function startRecording(): Promise<void> {
  if (props.disabled || busy.value) return
  if (!browserVoiceRecordingSupported()) {
    emit('error', localUnavailableMessage(capabilities.value))
    return
  }
  if (mode.value === 'local' && !capabilities.value?.available) {
    await loadCapabilities()
    if (!capabilities.value?.available) {
      emit('error', localUnavailableMessage(capabilities.value))
      return
    }
  }
  if (props.disabled) return
  const version = operationVersion
  starting.value = true
  setBusy()
  try {
    const activeRecorder = createVoiceRecorder()
    recorder = activeRecorder
    await activeRecorder.start()
    if (version !== operationVersion || recorder !== activeRecorder) {
      activeRecorder.cancel()
      return
    }
    starting.value = false
    elapsedSeconds.value = 0
    recording.value = true
    setBusy()
    emit('info', mode.value === 'cloud'
      ? '正在录音；停止后还需点击“发送录音”，现在不会调用模型。'
      : '正在本机录音；停止后只会回填文字，不会自动发送。')
    ticker = window.setInterval(() => { elapsedSeconds.value += 1 }, 1000)
    limitTimer = window.setTimeout(() => { void finishRecording() }, 60_000)
  } catch (error) {
    recorder?.cancel()
    recorder = null
    if (version === operationVersion) emit('error', transcriptionError(error))
  } finally {
    if (version === operationVersion) {
      starting.value = false
      setBusy()
    }
  }
}

async function transcribe(wav: Blob): Promise<boolean> {
  transcribing.value = true
  setBusy()
  emit('info', '正在本机转写；结果返回后仍需你检查。')
  const controller = new AbortController()
  activeController = controller
  try {
    const result = await intakeApi.transcribeSpeech(wav, controller.signal)
    if (controller.signal.aborted) return false
    emit('transcript', result.text)
    emit('info', '语音已转成文字并回填，请检查姓名、日期、数字和否定词。')
    return true
  } catch (error) {
    if (!controller.signal.aborted) emit('error', transcriptionError(error))
    return false
  } finally {
    if (activeController === controller) activeController = null
    transcribing.value = false
    setBusy()
  }
}

async function finishRecording(): Promise<void> {
  if (!recording.value || !recorder) return
  clearTimers()
  const seconds = Math.max(1, Math.min(60, elapsedSeconds.value))
  recording.value = false
  const activeRecorder = recorder
  recorder = null
  try {
    const wav = await activeRecorder.stop()
    if (mode.value === 'cloud') {
      pendingAudio.value = { wav, seconds, operationId: crypto.randomUUID(), attempted: false }
      emit('info', '录音已停下但尚未发送；你可以发送给模型，或改为本机转文字。')
      setBusy()
      return
    }
    await transcribe(wav)
  } catch (error) {
    emit('error', transcriptionError(error))
    setBusy()
  }
}

async function sendPendingAudio(): Promise<void> {
  const pending = pendingAudio.value
  if (!pending || pending.attempted || !props.submitCloudAudio) return
  if (!capabilities.value?.cloud_audio) await loadCapabilities()
  const cloud = capabilities.value?.cloud_audio
  if (!cloud) {
    emit('error', cloudUnavailableMessage())
    return
  }
  pending.attempted = true
  cloudSending.value = true
  setBusy()
  emit('info', '正在把这段录音发送给模型；系统不会自动重试。')
  const controller = new AbortController()
  activeController = controller
  try {
    const completed = await props.submitCloudAudio(
      pending.wav,
      pending.operationId,
      cloud.destination_fingerprint,
      controller.signal,
    )
    if (completed) pendingAudio.value = null
  } finally {
    if (activeController === controller) activeController = null
    cloudSending.value = false
    setBusy()
  }
}

async function convertPendingToText(): Promise<void> {
  const pending = pendingAudio.value
  if (!pending || transcribing.value || cloudSending.value) return
  const completed = await transcribe(pending.wav)
  if (completed) pendingAudio.value = null
  setBusy()
}

function cancelRecording(): void {
  operationVersion += 1
  clearTimers()
  activeController?.abort()
  activeController = null
  recorder?.cancel()
  recorder = null
  pendingAudio.value = null
  starting.value = false
  recording.value = false
  transcribing.value = false
  cloudSending.value = false
  setBusy()
}

async function toggle(): Promise<void> {
  if (recording.value) await finishRecording()
  else await startRecording()
}

watch(() => props.disabled, (disabled) => {
  if (disabled && (starting.value || recording.value || transcribing.value)) cancelRecording()
})
watch(() => props.contextKey, (next, previous) => {
  if (previous && next !== previous && busy.value) {
    cancelRecording()
    emit('info', '会话已经切换，未发送的录音已从当前页面清除。')
  }
})
onBeforeUnmount(cancelRecording)
onMounted(() => { void loadCapabilities() })
</script>

<template>
  <div class="voice-input">
    <div class="voice-mode-wrap">
      <div class="voice-mode" :data-mode="mode" role="group" aria-label="语音输入方式">
        <span class="voice-mode__slider" aria-hidden="true" />
        <button type="button" :class="{ 'is-active': mode === 'local' }" :aria-pressed="mode === 'local'" :disabled="busy || disabled" @click="chooseMode('local')">本机转文字</button>
        <button type="button" :class="{ 'is-active': mode === 'cloud' }" :aria-pressed="mode === 'cloud'" :disabled="busy || disabled" @click="chooseMode('cloud')">语音给模型</button>
      </div>
      <small class="voice-mode__hint">语音给模型要求当前模型支持语音输入</small>
    </div>
    <span class="voice-input__status" role="status">{{ statusLabel }}</span>
    <button type="button" class="voice-input__button" :class="{ 'voice-input__button--recording': recording }" :disabled="disabled || starting || transcribing || cloudSending || Boolean(pendingAudio)" :aria-pressed="recording" :aria-label="label" @click="toggle">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 0 0 3.5-3.5V6a3.5 3.5 0 1 0-7 0v6a3.5 3.5 0 0 0 3.5 3.5Zm-6-4a1 1 0 0 1 2 0 4 4 0 0 0 8 0 1 1 0 1 1 2 0 6 6 0 0 1-5 5.92V20h2.5a1 1 0 1 1 0 2h-7a1 1 0 1 1 0-2H11v-2.58A6 6 0 0 1 6 11.5Z" /></svg>
      <span>{{ label }}</span>
    </button>
    <div v-if="pendingAudio" class="voice-input__pending" aria-label="尚未发送的录音">
      <button type="button" class="voice-input__send" :disabled="cloudSending || pendingAudio.attempted" @click="sendPendingAudio">{{ pendingAudio.attempted ? '不再重复发送' : '发送录音' }}</button>
      <button type="button" class="voice-input__secondary" :disabled="cloudSending || transcribing" @click="convertPendingToText">转为本机文字</button>
      <button type="button" class="voice-input__quiet" :disabled="cloudSending || transcribing" @click="cancelRecording">取消</button>
    </div>
  </div>
</template>

<style scoped>
.voice-input{display:grid;grid-template-columns:218px 140px;grid-template-areas:"mode microphone" "hint status" "pending pending";justify-content:end;align-items:start;column-gap:12px;row-gap:3px;width:370px;max-width:100%;min-width:0}.voice-mode-wrap{display:contents}.voice-mode{position:relative;display:grid;grid-area:mode;grid-template-columns:1fr 1fr;width:218px;padding:3px;border:1px solid var(--border);border-radius:var(--radius);background:var(--muted);isolation:isolate}.voice-mode__slider{position:absolute;z-index:-1;inset:3px auto 3px 3px;width:calc(50% - 3px);border-radius:6px;background:var(--card);box-shadow:0 1px 2px color-mix(in srgb,var(--foreground) 10%,transparent);transition:transform .18s ease}.voice-mode[data-mode="cloud"] .voice-mode__slider{transform:translateX(100%)}.voice-mode button{min-height:32px;padding:0 9px;border:0;border-radius:6px;background:transparent;color:var(--muted-foreground);font:inherit;font-size:12px;font-weight:600;white-space:nowrap;cursor:pointer}.voice-mode button.is-active{color:var(--foreground)}.voice-mode__hint{grid-area:hint;color:var(--muted-foreground);font-size:11px;line-height:1.3;text-align:center}.voice-input__status{grid-area:status;min-height:15px;color:var(--muted-foreground);font-size:11px;font-variant-numeric:tabular-nums;font-weight:600;line-height:1.3;text-align:center;white-space:nowrap}.voice-input__button,.voice-input__send,.voice-input__secondary,.voice-input__quiet{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:40px;padding:0 13px;border-radius:var(--radius);font:inherit;font-weight:600;white-space:nowrap;cursor:pointer}.voice-input__button{grid-area:microphone;width:140px;border:1px solid var(--border);background:var(--card);color:var(--foreground)}.voice-input__button:hover{background:var(--accent)}.voice-input__button svg{width:18px;height:18px;fill:currentColor}.voice-input__button--recording{border-color:var(--destructive);background:var(--color-danger-subtle);color:var(--destructive)}.voice-input__pending{display:flex;grid-area:pending;justify-content:flex-end;gap:8px;padding-top:5px}.voice-input__send{border:1px solid var(--primary);background:var(--primary);color:var(--primary-foreground)}.voice-input__secondary{border:1px solid var(--border);background:var(--card);color:var(--foreground)}.voice-input__quiet{border:0;background:transparent;color:var(--muted-foreground)}button:focus-visible{outline:2px solid var(--ring);outline-offset:2px}button:disabled{cursor:not-allowed;opacity:.62}@media(max-width:800px){.voice-input{grid-template-columns:minmax(0,1fr) minmax(140px,1fr);width:100%}.voice-mode{width:100%}.voice-input__button{width:100%}.voice-input__pending{flex-wrap:wrap}.voice-input__send,.voice-input__secondary{flex:1}}
</style>
