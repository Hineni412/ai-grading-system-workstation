<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'
import FeedbackBanner from '@/components/design-system/FeedbackBanner.vue'
import StatusBadge from '@/components/design-system/StatusBadge.vue'

import { affairR1Api, type AffairDetail } from '../api/r1'
import SopAsideRail from './SopAsideRail.vue'
import SopFlowDiagram from './SopFlowDiagram.vue'
import SopStepInspector from './SopStepInspector.vue'
import SopStudentDrawer from './SopStudentDrawer.vue'

const props = defineProps<{ affairId: string }>()
const emit = defineEmits<{ back: []; discarded: [] }>()

const affair = ref<AffairDetail | null>(null)
const loading = ref(false)
const loadError = ref('')
const selectedStepId = ref<string | null>(null)
const drawerSubjectId = ref<string | null>(null)
const discardOpen = ref(false)
const discardReason = ref('')
const discardBusy = ref(false)
const closureSummary = ref('')
const reopenReason = ref('')
const syncBusy = ref(false)
const notice = ref<{ tone: 'info' | 'success' | 'warning' | 'error'; title: string; description: string } | null>(null)
let syncTimer: number | null = null
let syncGeneration = 0

const allSteps = computed(() => affair.value
  ? [...affair.value.completed_steps, ...affair.value.current_steps, ...affair.value.preview_steps]
  : [])
const selectedStep = computed(() => allSteps.value.find((step) => step.step_instance_id === selectedStepId.value) ?? null)
const pendingRevision = computed(() => {
  const list = (affair.value?.flow_revisions ?? []).filter((item) => item.state === 'pending_review')
  return list.length ? list[list.length - 1]! : null
})
const queuedSync = computed(() => {
  const list = (affair.value?.sync_requests ?? []).filter((item) => item.state === 'queued')
  return list.length ? list[list.length - 1]! : null
})
const failedSync = computed(() => {
  const list = (affair.value?.sync_requests ?? []).filter((item) => ['failed', 'invalid_result'].includes(item.state))
  return list.length ? list[list.length - 1]! : null
})
const stateBadge = computed(() => {
  if (affair.value?.state === 'discarded') return { tone: 'danger' as const, label: '已弃用' }
  if (affair.value?.state === 'closed') return { tone: 'success' as const, label: '已结案' }
  return { tone: 'info' as const, label: '进行中 · 生成即生效' }
})

async function load(): Promise<void> {
  loading.value = true; loadError.value = ''
  try {
    affair.value = await affairR1Api.read(props.affairId)
    if (queuedSync.value) {
      syncBusy.value = true
      pollSyncResult()
    }
  } catch {
    loadError.value = '事务暂时无法打开。可以返回事务列表后重试。'
  } finally { loading.value = false }
}

function stopSyncPolling(): void {
  syncGeneration += 1
  if (syncTimer !== null) window.clearTimeout(syncTimer)
  syncTimer = null
}

function pollSyncResult(): void {
  stopSyncPolling()
  const generation = syncGeneration
  const poll = async () => {
    if (generation !== syncGeneration || !affair.value) return
    try {
      affair.value = await affairR1Api.read(affair.value.affair_id)
    } catch {
      syncTimer = window.setTimeout(poll, 2500)
      return
    }
    if (generation !== syncGeneration) return
    if (pendingRevision.value) {
      syncTimer = null
      syncBusy.value = false
      notice.value = { tone: 'info', title: 'AI 已给出流程修订建议', description: '请在右侧步骤检查器中逐项核对后接受或拒绝。' }
      if (!selectedStepId.value) selectedStepId.value = affair.value.current_steps[0]?.step_instance_id ?? null
      return
    }
    if (failedSync.value && !queuedSync.value) {
      syncTimer = null
      syncBusy.value = false
      notice.value = { tone: 'warning', title: 'AI 没有形成可用的修订建议', description: '原文仍保留，可以修改后再次发送。' }
      return
    }
    syncTimer = window.setTimeout(poll, 2000)
  }
  syncTimer = window.setTimeout(poll, 1200)
}

async function sendSync(text: string): Promise<void> {
  if (!affair.value || syncBusy.value) return
  syncBusy.value = true; notice.value = null
  try {
    await affairR1Api.syncUpdate(affair.value, text)
    affair.value = await affairR1Api.read(affair.value.affair_id)
    notice.value = { tone: 'info', title: '已发送给 AI', description: '最多调用一次，失败不会自动重发。' }
    pollSyncResult()
  } catch {
    syncBusy.value = false
    notice.value = { tone: 'error', title: '同步没有提交成功', description: '事务内容没有变化。' }
  }
}

function requestAiUpdate(): void {
  if (syncBusy.value) return
  notice.value = pendingRevision.value
    ? { tone: 'info', title: '已有待核对的修订建议', description: '请在右侧步骤检查器中逐项接受或拒绝。' }
    : { tone: 'info', title: '让 AI 更新后续步骤', description: '先在左栏输入新情况并发送，AI 会基于已完成步骤更新后续安排。' }
}

async function discardAffair(): Promise<void> {
  if (!affair.value || discardBusy.value || !discardReason.value.trim()) return
  discardBusy.value = true
  try {
    await affairR1Api.discard(affair.value, discardReason.value.trim())
    emit('discarded')
  } catch {
    discardBusy.value = false
    notice.value = { tone: 'error', title: '弃用没有完成', description: '可能已有其他改动，请刷新后重试。' }
    await load()
  }
}

async function closeAffair(): Promise<void> {
  if (!affair.value || !closureSummary.value.trim()) return
  try {
    affair.value = await affairR1Api.command(affair.value, 'close', { summary: closureSummary.value })
    closureSummary.value = ''
    notice.value = { tone: 'success', title: '已结案', description: '事务已结案；如需重启可在页头重开新一轮。' }
  } catch {
    notice.value = { tone: 'error', title: '结案没有完成', description: '请刷新后重试。' }
  }
}

async function reopenAffair(): Promise<void> {
  if (!affair.value || !reopenReason.value.trim()) return
  try {
    affair.value = await affairR1Api.command(affair.value, 'reopen', { reason: reopenReason.value })
    reopenReason.value = ''
    notice.value = { tone: 'info', title: '已重开新一轮', description: '重开创建了新的轮次，不覆盖上一轮。' }
  } catch {
    notice.value = { tone: 'error', title: '重开没有完成', description: '请刷新后重试。' }
  }
}

function selectStep(stepInstanceId: string): void {
  selectedStepId.value = stepInstanceId
  drawerSubjectId.value = null
}

function openStudent(subjectId: string): void {
  drawerSubjectId.value = subjectId
  selectedStepId.value = null
}

async function reload(): Promise<void> {
  if (!affair.value) return
  affair.value = await affairR1Api.read(affair.value.affair_id)
}

watch(() => props.affairId, () => { stopSyncPolling(); selectedStepId.value = null; drawerSubjectId.value = null; void load() })
onBeforeUnmount(stopSyncPolling)
onMounted(() => { void load() })
</script>

<template>
  <section class="sop-workspace">
    <p v-if="loadError" class="sop-workspace__error" role="alert">{{ loadError }}</p>
    <p v-else-if="loading && !affair" class="sop-workspace__loading" aria-live="polite">正在打开事务…</p>

    <template v-else-if="affair">
      <header class="sop-workspace__header">
        <div class="sop-workspace__title">
          <StatusBadge :tone="stateBadge.tone" :label="stateBadge.label" />
          <h2>{{ affair.title }}</h2>
          <div class="participants">
            <span v-for="participant in affair.participants ?? []" :key="participant.participant_id" class="chip">{{ participant.reference }}</span>
          </div>
        </div>
        <div class="sop-workspace__actions">
          <AppButton variant="secondary" :loading="syncBusy" loading-label="AI 正在更新" :disabled="affair.state !== 'active'" @click="requestAiUpdate">让 AI 更新后续步骤</AppButton>
          <AppButton v-if="affair.state === 'active'" variant="danger" @click="discardOpen = !discardOpen">弃用此 SOP</AppButton>
          <AppButton variant="secondary" @click="emit('back')">返回对话</AppButton>
        </div>
      </header>

      <FeedbackBanner
        v-if="notice"
        :tone="notice.tone"
        :title="notice.title"
        :description="notice.description"
        dismissible
        @dismiss="notice = null"
      />
      <FeedbackBanner
        v-if="affair.state === 'discarded'"
        tone="error"
        title="此 SOP 已弃用"
        :description="affair.discard_reason ? `弃用原因：${affair.discard_reason}` : '弃用不可恢复；如仍需要请回到对话重新生成。'"
      />
      <FeedbackBanner
        v-else-if="affair.emergency_prompt"
        tone="warning"
        title="安全提示"
        :description="affair.emergency_prompt"
      />

      <div v-if="discardOpen" class="discard-confirm">
        <strong>弃用后不可恢复</strong>
        <p>未完成步骤会被终结，事务标记为已弃用；如仍需要只能回到对话重新生成。请填写弃用原因。</p>
        <textarea v-model="discardReason" rows="2" maxlength="4000" placeholder="弃用原因（必填）"></textarea>
        <div>
          <AppButton variant="danger" :disabled="discardBusy || !discardReason.trim()" @click="discardAffair">确认弃用</AppButton>
          <AppButton variant="secondary" :disabled="discardBusy" @click="discardOpen = false">取消</AppButton>
        </div>
      </div>

      <div class="sop-workspace__layout">
        <SopAsideRail :affair="affair" :sync-busy="syncBusy" @send-sync="sendSync" @open-student="openStudent" />
        <SopFlowDiagram :steps="allSteps" :selected-id="selectedStepId" @select="selectStep" />
        <SopStepInspector
          v-if="selectedStep"
          :key="selectedStep.step_instance_id"
          :affair="affair"
          :step="selectedStep"
          @changed="(updated) => { affair = updated }"
          @reload="reload"
          @close="selectedStepId = null"
        />
        <aside v-else class="panel-hint">
          <p>点击流程图节点查看步骤详情；点击左侧学生姓名查看档案草稿。</p>
        </aside>
      </div>

      <footer v-if="affair.state === 'active' && !affair.current_steps.length && !affair.preview_steps.length" class="closure">
        <strong>所有步骤已处理完，可以结案。</strong>
        <textarea v-model="closureSummary" rows="2" maxlength="8000" placeholder="结案摘要（必填）"></textarea>
        <AppButton variant="primary" :disabled="!closureSummary.trim()" @click="closeAffair">确认结案</AppButton>
      </footer>
      <footer v-else-if="affair.state === 'closed'" class="closure">
        <strong>事务已结案。</strong>
        <textarea v-model="reopenReason" rows="2" maxlength="4000" placeholder="重开理由（必填）；重开会创建新的轮次，不覆盖上一轮。"></textarea>
        <AppButton variant="secondary" :disabled="!reopenReason.trim()" @click="reopenAffair">填写理由并重开</AppButton>
      </footer>

      <SopStudentDrawer
        :open="Boolean(drawerSubjectId)"
        :affair="affair"
        :subject-id="drawerSubjectId"
        @close="drawerSubjectId = null"
        @reload="reload"
      />
    </template>
  </section>
</template>

<style scoped>
.sop-workspace{display:grid;gap:16px;align-content:start}
.sop-workspace__error{padding:12px 16px;border:1px solid var(--border);background:var(--color-danger-subtle);color:var(--destructive)}
.sop-workspace__loading{margin:0;padding:24px;color:var(--muted-foreground)}
.sop-workspace__header{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:20px 24px;border:1px solid var(--border);border-top:3px solid var(--color-warning);border-radius:var(--radius);background:var(--card)}
.sop-workspace__title{display:grid;gap:8px;justify-items:start}
.sop-workspace__title h2{margin:0;font-size:22px}
.participants{display:flex;gap:8px;flex-wrap:wrap}
.chip{padding:2px 12px;border:1px solid var(--border);border-radius:999px;background:var(--muted);font-size:12px}
.sop-workspace__actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.discard-confirm{display:grid;gap:8px;padding:16px;border:1px solid var(--destructive);border-radius:var(--radius);background:var(--color-danger-subtle)}
.discard-confirm p{margin:0;font-size:13px;color:var(--color-text-secondary)}
.discard-confirm textarea{padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;resize:vertical}
.discard-confirm div{display:flex;gap:8px}
.sop-workspace__layout{display:grid;grid-template-columns:250px minmax(0,1fr) 320px;gap:16px;align-items:start}
.panel-hint{padding:16px;border:1px dashed var(--border);border-radius:var(--radius);color:var(--muted-foreground);font-size:13px}
.panel-hint p{margin:0}
.closure{display:grid;gap:8px;padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.closure textarea{padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;resize:vertical}
.closure .app-button{justify-self:start}
@media(max-width:1100px){.sop-workspace__layout{grid-template-columns:220px minmax(0,1fr)}.panel-hint,.sop-workspace__layout>:last-child{grid-column:1/-1}}
</style>
