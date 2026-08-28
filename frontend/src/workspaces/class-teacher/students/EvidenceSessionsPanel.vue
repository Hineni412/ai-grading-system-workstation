<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'
import { ApiError } from '@/api/errors'
import { studentR1Api, type AcademicSessionSummary } from '../api/r1'
import { supportApi, type EvidenceSessionDeletePreview } from '../api/support'
import { TERM_OPTIONS, academicYearOf, type TermValue } from './EvidenceUploadPanel.vue'

const EXAM_TYPE_OPTIONS = ['期末考试', '期中考试', '月考', '模拟考试', '常规检测', '常规考试'] as const

const props = withDefaults(defineProps<{ open?: boolean }>(), { open: false })
const emit = defineEmits<{ changed: []; close: [] }>()

const sessions = ref<AcademicSessionSummary[]>([])
const loaded = ref(false)
const failed = ref(false)
const busy = ref(false)
const message = ref('')

const editingId = ref<string | null>(null)
const editTitle = ref('')
const editTermLabel = ref<TermValue | ''>('')
const editOccurredOn = ref('')
const editExamType = ref('')
const editError = ref('')

const deletingId = ref<string | null>(null)
const deletePreview = ref<EvidenceSessionDeletePreview | null>(null)
const deletePhrase = ref('')
const deleteError = ref('')

const maxScoreId = ref<string | null>(null)
const maxScoreSubjects = ref<Array<{ subject_name: string; max_score: number | null }>>([])
const maxScoreInputs = ref<Record<string, string | number>>({})
const maxScoreParticipantCount = ref<number | null>(null)
const participantCountInput = ref<string | number>('')
const maxScoreError = ref('')

const globalOpen = ref(false)
const globalSubjects = ref<string[]>([])
const globalInputs = ref<Record<string, string | number>>({})
const globalParticipantInput = ref<string | number>('')
const globalError = ref('')

const dialog = ref<HTMLElement | null>(null)
let previouslyFocused: HTMLElement | null = null
let previousBodyOverflow = ''

async function load(): Promise<void> {
  failed.value = false
  try {
    const overview = await studentR1Api.academicOverview()
    sessions.value = overview.sessions
    loaded.value = true
  } catch {
    failed.value = true
    return
  }
  // 全局设置的科目表取各场次 canonical 科目并集；走势读取失败不阻塞场次管理。
  // 「总分」不出现在输入列表：总分满分按各科满分之和自动计算。
  try {
    const trend = await studentR1Api.classTrend()
    const names = new Set<string>()
    for (const session of trend.sessions) for (const subject of session.subjects) if (subject.subject_name !== '总分') names.add(subject.subject_name)
    globalSubjects.value = [...names]
  } catch {
    globalSubjects.value = []
  }
}

function termLabelOf(session: AcademicSessionSummary): string {
  const option = TERM_OPTIONS.find((item) => item.grade === session.grade && item.term === session.term)
  return option ? option.value : '未指定'
}

function resetEditors(): void {
  editingId.value = null
  deletingId.value = null
  deletePreview.value = null
  maxScoreId.value = null
  maxScoreSubjects.value = []
  maxScoreInputs.value = {}
  maxScoreParticipantCount.value = null
  participantCountInput.value = ''
  message.value = ''
}

function startEdit(session: AcademicSessionSummary): void {
  resetEditors()
  editingId.value = session.session_id
  editTitle.value = session.title
  editTermLabel.value = TERM_OPTIONS.find((item) => item.grade === session.grade && item.term === session.term)?.value ?? ''
  editOccurredOn.value = session.occurred_on
  editExamType.value = session.exam_type ?? ''
  editError.value = ''
}

async function submitEdit(): Promise<void> {
  if (!editingId.value || busy.value) return
  if (!editTitle.value.trim()) {
    editError.value = '考试名称不能为空。'
    return
  }
  if (!editOccurredOn.value) {
    editError.value = '请选择考试日期。'
    return
  }
  const option = TERM_OPTIONS.find((item) => item.value === editTermLabel.value)
  busy.value = true
  editError.value = ''
  try {
    // 后端忽略缺省字段：未选择的项不发送即为保持不变；学年随考试日期按 8 月分界重算
    await supportApi.updateEvidenceSession(editingId.value, {
      title: editTitle.value.trim(),
      ...(option ? { grade: option.grade, term: option.term } : {}),
      ...(editExamType.value ? { exam_type: editExamType.value } : {}),
      occurred_on: editOccurredOn.value,
      academic_year: academicYearOf(editOccurredOn.value),
    })
    editingId.value = null
    message.value = '场次信息已更正。'
    emit('changed')
    await load()
  } catch (error) {
    editError.value = error instanceof ApiError && error.status === 409
      ? '已存在相同的场次，可考虑删除本场次后重新上传。'
      : error instanceof ApiError ? error.message : '更正没有保存成功，请稍后重试。'
  } finally {
    busy.value = false
  }
}

async function startDelete(session: AcademicSessionSummary): Promise<void> {
  if (busy.value) return
  busy.value = true
  deleteError.value = ''
  resetEditors()
  try {
    deletePreview.value = await supportApi.previewDeleteEvidenceSession(session.session_id)
    deletingId.value = session.session_id
    deletePhrase.value = ''
  } catch (error) {
    deleteError.value = error instanceof ApiError ? error.message : '删除预览暂时无法读取，请稍后重试。'
    deletingId.value = session.session_id
    deletePreview.value = null
  } finally {
    busy.value = false
  }
}

async function confirmDelete(): Promise<void> {
  const sessionId = deletingId.value
  const previewSnapshot = deletePreview.value
  if (!sessionId || !previewSnapshot || busy.value) return
  busy.value = true
  deleteError.value = ''
  try {
    const result = await supportApi.deleteEvidenceSession(
      sessionId,
      previewSnapshot.preview_version,
      deletePhrase.value.trim(),
    )
    const counts = (result as { counts?: { results?: number } }).counts
    message.value = `已删除场次「${previewSnapshot.title}」${typeof counts?.results === 'number' ? `及其 ${counts.results} 条成绩` : ''}。`
    deletingId.value = null
    deletePreview.value = null
    emit('changed')
    await load()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      deleteError.value = '删除预览已过期，已为你重新读取影响范围，请再次确认。'
      try {
        deletePreview.value = await supportApi.previewDeleteEvidenceSession(sessionId)
      } catch {
        deletingId.value = null
        deletePreview.value = null
      }
    } else if (error instanceof ApiError && error.status === 422) {
      deleteError.value = '确认短语不符，请按上方提示原样输入后再确认。'
    } else {
      deleteError.value = error instanceof ApiError ? error.message : '删除没有成功，现有成绩未改变。'
    }
  } finally {
    busy.value = false
  }
}

async function startMaxScores(session: AcademicSessionSummary): Promise<void> {
  if (busy.value) return
  busy.value = true
  maxScoreError.value = ''
  resetEditors()
  try {
    const results = await studentR1Api.sessionClassResults(session.session_id)
    // 总分满分由后端按各科满分之和自动派生，不提供手填。
    maxScoreSubjects.value = results.subjects
      .filter((subject) => subject.subject_name !== '总分')
      .map((subject) => ({ subject_name: subject.subject_name, max_score: subject.max_score }))
    maxScoreInputs.value = {}
    maxScoreParticipantCount.value = results.participant_count
    participantCountInput.value = ''
    maxScoreId.value = session.session_id
  } catch (error) {
    maxScoreError.value = error instanceof ApiError ? error.message : '这场次各科满分暂时无法读取，请稍后重试。'
    maxScoreId.value = session.session_id
    maxScoreSubjects.value = []
  } finally {
    busy.value = false
  }
}

function maxScoreDraft(): Record<string, number> {
  const draft: Record<string, number> = {}
  for (const subject of maxScoreSubjects.value) {
    const entry = maxScoreInputs.value[subject.subject_name]
    const raw = entry == null ? '' : String(entry).trim()
    if (!raw) continue
    const value = Number(raw)
    if (Number.isFinite(value)) draft[subject.subject_name] = value
  }
  return draft
}

function participantCountDraft(): number | null {
  // 年级人数留空即不改；只接受有限数字。
  const raw = String(participantCountInput.value ?? '').trim()
  if (!raw) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

function globalDraft(): Record<string, number> {
  const draft: Record<string, number> = {}
  for (const name of globalSubjects.value) {
    const entry = globalInputs.value[name]
    const raw = entry == null ? '' : String(entry).trim()
    if (!raw) continue
    const value = Number(raw)
    if (Number.isFinite(value)) draft[name] = value
  }
  return draft
}

function globalParticipantDraft(): number | null {
  const raw = String(globalParticipantInput.value ?? '').trim()
  if (!raw) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

async function submitGlobal(): Promise<void> {
  const draft = globalDraft()
  const participantCount = globalParticipantDraft()
  if ((!Object.keys(draft).length && participantCount == null) || busy.value) return
  busy.value = true
  globalError.value = ''
  try {
    const result = participantCount == null
      ? await supportApi.updateGlobalEvidenceSettings(draft)
      : await supportApi.updateGlobalEvidenceSettings(draft, participantCount)
    globalInputs.value = {}
    globalParticipantInput.value = ''
    message.value = `全局设置已保存，已覆盖 ${result.sessions_updated} 场考试。`
    emit('changed')
    await load()
  } catch (error) {
    globalError.value = error instanceof ApiError && error.status === 422
      ? '满分与年级人数需要是正数，请核对后再保存。'
      : error instanceof ApiError ? error.message : '全局设置没有保存成功，请稍后重试。'
  } finally {
    busy.value = false
  }
}

async function submitMaxScores(): Promise<void> {
  const sessionId = maxScoreId.value
  const draft = maxScoreDraft()
  const participantCount = participantCountDraft()
  if (!sessionId || (!Object.keys(draft).length && participantCount == null) || busy.value) return
  busy.value = true
  maxScoreError.value = ''
  try {
    if (participantCount == null) {
      await supportApi.updateEvidenceSessionMaxScores(sessionId, draft)
    } else {
      await supportApi.updateEvidenceSessionMaxScores(sessionId, draft, participantCount)
    }
    maxScoreId.value = null
    maxScoreSubjects.value = []
    maxScoreInputs.value = {}
    maxScoreParticipantCount.value = null
    participantCountInput.value = ''
    message.value = '满分已保存，分数段与得分率统计即刻生效。'
    emit('changed')
    await load()
  } catch (error) {
    maxScoreError.value = error instanceof ApiError && error.status === 422
      ? '满分与年级人数需要是正数，且科目必须属于这场次；请核对后再保存。'
      : error instanceof ApiError ? error.message : '满分没有保存成功，请稍后重试。'
  } finally {
    busy.value = false
  }
}

function closeDrawer(): void {
  emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeDrawer()
}

watch(() => props.open, async (open) => {
  if (open) {
    previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeydown)
    await load()
    await nextTick()
    dialog.value?.focus()
  } else {
    window.removeEventListener('keydown', onKeydown)
    document.body.style.overflow = previousBodyOverflow
    resetEditors()
    previouslyFocused?.focus()
  }
}, { immediate: true })

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  if (props.open) {
    document.body.style.overflow = previousBodyOverflow
    previouslyFocused?.focus()
  }
})
</script>

<template>
  <div v-if="open" class="sessions-layer">
    <button class="sessions-backdrop" type="button" aria-label="关闭成绩管理" @click="closeDrawer" />
    <aside
      ref="dialog"
      class="sessions-drawer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="evidence-sessions-title"
      tabindex="-1"
    >
      <section class="evidence-sessions">
        <header>
          <div><p>成绩管理</p><h2 id="evidence-sessions-title">已登记成绩管理</h2></div>
          <span class="actions">
            <AppButton variant="secondary" @click="load">刷新列表</AppButton>
            <AppButton variant="ghost" @click="closeDrawer">关闭</AppButton>
          </span>
        </header>
        <p class="intro">这里列出全班已登记的大考场次；名称、学期、日期或考试性质登记错了可以更正，满分没填可以补填，整场传错了可以删除。</p>
        <p v-if="message" class="message" role="status">{{ message }}</p>
        <div v-if="failed" class="empty" role="alert">
          <p>已登记场次暂时无法读取，成绩数据未受影响。</p>
          <AppButton variant="secondary" @click="load">重新读取</AppButton>
        </div>
        <div v-else-if="loaded && !sessions.length" class="empty">
          <h3>还没有已登记的成绩场次</h3>
          <p>先在上方"大考成绩表"上传成绩；登记后可以在这里更正场次信息或删除整场成绩。</p>
        </div>
        <template v-else>
          <section v-if="loaded && sessions.length" class="global-settings" aria-label="全局设置">
            <button type="button" class="global-settings__toggle" :aria-expanded="globalOpen" @click="globalOpen = !globalOpen">
              <strong>全局设置</strong>
              <span>{{ globalOpen ? '收起' : '展开' }}：统一设置各科满分与年级人数</span>
            </button>
            <div v-if="globalOpen" class="session-form">
              <label><span>年级人数（留空不改）</span><input v-model="globalParticipantInput" type="number" min="1" step="1" placeholder="如：320"></label>
              <label v-for="name in globalSubjects" :key="name"><span>{{ name }}满分（留空不改）</span><input v-model="globalInputs[name]" type="number" min="0" step="any" placeholder="未定标"></label>
              <p class="hint global-settings__note">保存后覆盖全部已登记场次的对应设置；只提交填写了的项，留空的科目保持不变。总分满分按各科满分之和自动计算，无需填写。</p>
              <p v-if="globalError" class="error" role="alert">{{ globalError }}</p>
              <footer><AppButton variant="primary" :disabled="!Object.keys(globalDraft()).length && globalParticipantDraft() == null" :loading="busy" loading-label="正在保存" @click="submitGlobal">保存全局设置</AppButton></footer>
            </div>
          </section>
          <div v-for="session in sessions" :key="session.session_id" class="session">
            <div class="session__row">
              <strong>{{ session.title }}</strong>
              <span>{{ session.short_label || termLabelOf(session) }} · {{ session.occurred_on }} · {{ session.exam_type || '性质未标注' }}</span>
              <span>{{ session.subject_names.length ? session.subject_names.join('、') : '未标注学科' }} · {{ session.member_count }} 条成绩</span>
              <span class="actions">
                <AppButton variant="ghost" @click="startMaxScores(session)">满分</AppButton>
                <AppButton variant="ghost" @click="startEdit(session)">更正</AppButton>
                <AppButton variant="ghost" @click="startDelete(session)">删除</AppButton>
              </span>
            </div>
            <div v-if="maxScoreId === session.session_id" class="session-form">
              <label>
                <span>年级人数（当前 {{ maxScoreParticipantCount ?? '未填' }}，留空不改）</span>
                <input v-model="participantCountInput" type="number" min="1" step="1" :placeholder="maxScoreParticipantCount != null ? String(maxScoreParticipantCount) : '未填'">
              </label>
              <template v-if="maxScoreSubjects.length">
                <label v-for="subject in maxScoreSubjects" :key="subject.subject_name">
                  <span>{{ subject.subject_name }}满分（当前 {{ subject.max_score ?? '未定标' }}，留空不改）</span>
                  <input v-model="maxScoreInputs[subject.subject_name]" type="number" min="0" step="any" :placeholder="subject.max_score != null ? String(subject.max_score) : '未定标'">
                </label>
                <p class="hint">总分满分按各科满分之和自动计算，无需填写。</p>
              </template>
              <p v-else-if="!maxScoreError" class="error">这场次还没有可补填满分的科目。</p>
              <p v-if="maxScoreError" class="error" role="alert">{{ maxScoreError }}</p>
              <footer><AppButton variant="primary" :disabled="!Object.keys(maxScoreDraft()).length && participantCountDraft() == null" :loading="busy" loading-label="正在保存" @click="submitMaxScores">保存满分</AppButton><AppButton variant="ghost" @click="maxScoreId = null">取消</AppButton></footer>
            </div>
            <div v-if="editingId === session.session_id" class="session-form">
              <label><span>考试名称</span><input v-model="editTitle" maxlength="500"></label>
              <label><span>学期类别</span><select v-model="editTermLabel"><option value="">保持不变</option><option v-for="option in TERM_OPTIONS" :key="option.value" :value="option.value">{{ option.value }}</option></select></label>
              <label><span>考试日期</span><input v-model="editOccurredOn" type="date"></label>
              <label><span>考试性质</span><select v-model="editExamType"><option value="">保持不变</option><option v-for="kind in EXAM_TYPE_OPTIONS" :key="kind" :value="kind">{{ kind }}</option></select></label>
              <p v-if="editError" class="error" role="alert">{{ editError }}</p>
              <footer><AppButton variant="primary" :loading="busy" loading-label="正在保存" @click="submitEdit">保存更正</AppButton><AppButton variant="ghost" @click="editingId = null">取消</AppButton></footer>
            </div>
            <div v-if="deletingId === session.session_id" class="delete-confirm">
              <template v-if="deletePreview">
                <p class="error">删除「{{ deletePreview.title }}」将一并删除 {{ deletePreview.counts.results }} 条成绩、{{ deletePreview.counts.assessments }} 个科目、{{ deletePreview.counts.imports }} 个批次、{{ deletePreview.counts.attention_cards }} 张关注卡，且不可恢复。</p>
                <label><span>输入确认短语：{{ deletePreview.confirmation_phrase }}</span><input v-model="deletePhrase"></label>
              </template>
              <p v-if="deleteError" class="error" role="alert">{{ deleteError }}</p>
              <footer><AppButton variant="primary" :disabled="!deletePreview" :loading="busy" loading-label="正在删除" @click="confirmDelete">确认删除</AppButton><AppButton variant="ghost" @click="deletingId = null; deletePreview = null">取消删除</AppButton></footer>
            </div>
          </div>
        </template>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.sessions-layer{position:fixed;z-index:65;inset:var(--shell-topbar-height,64px) 0 0;color:var(--foreground)}
.sessions-backdrop{position:absolute;inset:0;border:0;background:var(--color-overlay-mask);cursor:default}
.sessions-drawer{position:absolute;inset-block:0;right:0;display:block;width:min(680px,94vw);overflow:auto;border:0;border-left:1px solid var(--border);outline:0;background:var(--card);box-shadow:var(--shadow-overlay);animation:drawer-in .22s ease-out}
.sessions-drawer:focus-visible{outline:2px solid var(--ring);outline-offset:-2px}
@keyframes drawer-in{from{transform:translateX(24px)}to{transform:none}}
@media(prefers-reduced-motion:reduce){.sessions-drawer{animation:none}}
@media(max-width:700px){.sessions-drawer{width:100%;border-left:0}}
.evidence-sessions{overflow:hidden;background:var(--card)}
header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--border)}
header p{margin:0 0 2px;color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.07em}
h2{margin:0;font-size:var(--font-size-h2)}
.intro{margin:0;padding:var(--space-3) var(--space-5);color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.message{margin:0;padding:0 var(--space-5);color:var(--primary);font-size:var(--font-size-dense)}
.error{margin:0;color:var(--destructive,#b03a2e);font-size:var(--font-size-dense)}
.session{padding:var(--space-2) var(--space-5);border-bottom:1px solid var(--color-border-subtle)}
.global-settings{padding:var(--space-2) var(--space-5);border-bottom:1px solid var(--color-border-subtle)}
.global-settings__toggle{display:flex;align-items:baseline;gap:var(--space-3);width:100%;padding:var(--space-1) 0;border:0;background:transparent;text-align:left;font:inherit;font-size:var(--font-size-dense);cursor:pointer}
.global-settings__toggle span{color:var(--color-text-secondary)}
.global-settings__note{grid-column:1/-1;margin:0;color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.session__row{display:flex;align-items:baseline;gap:var(--space-3);padding:var(--space-1) 0;font-size:var(--font-size-dense)}
.session__row>span{color:var(--color-text-secondary)}
.actions{margin-left:auto;display:flex;gap:var(--space-1)}
.session-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:var(--space-3);margin:var(--space-2) 0;padding:var(--space-3);border:1px solid var(--color-border-subtle);border-radius:var(--radius);background:var(--muted)}
.session-form label{display:grid;gap:var(--space-1);margin:0;font-size:var(--font-size-dense);font-weight:650}
.session-form input,.session-form select{min-height:38px;padding:0 var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit}
.session-form footer{grid-column:1/-1;display:flex;gap:var(--space-2)}
.session-form .hint{grid-column:1/-1;margin:0;color:var(--color-text-secondary);font-size:var(--font-size-dense);font-weight:400}
.delete-confirm{display:grid;gap:var(--space-2);margin:var(--space-2) 0;padding:var(--space-3);border:1px solid var(--color-border-subtle);border-radius:var(--radius);background:var(--muted)}
.delete-confirm label{display:grid;gap:var(--space-1);margin:0;font-size:var(--font-size-dense);font-weight:650}
.delete-confirm input{min-height:38px;padding:0 var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit}
.delete-confirm footer{display:flex;gap:var(--space-2)}
.empty{display:grid;place-items:center;align-content:center;gap:var(--space-2);min-height:160px;padding:var(--space-6);text-align:center}
.empty h3{margin-bottom:0}
.empty p{max-width:460px;color:var(--color-text-secondary)}
</style>
