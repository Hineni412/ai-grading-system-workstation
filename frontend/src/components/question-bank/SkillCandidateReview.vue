<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { DialogRoot, DialogPortal, DialogContent, DialogTitle } from 'reka-ui'
import AppButton from '../design-system/AppButton.vue'
import SkillCandidateCard from './SkillCandidateCard.vue'
import {
  questionBankApi,
  type QuestionSkillIndex,
  type SkillCandidateApprovedSkill,
  type SkillCandidateList,
  type SkillCandidateReviewEdits,
  type SkillCandidateSuggestion,
} from '../../api/question-bank'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'

const props = defineProps<{ open: boolean; volumeId: string; index: QuestionSkillIndex | null }>()
const emit = defineEmits<{ close: []; changed: []; openQuestion: [ref: { questionId: number; paperId: number | null }] }>()

interface ReviewBody {
  decision: 'accept' | 'reject' | 'reopen'
  expected_revision: number
  request_token: string
  edits?: SkillCandidateReviewEdits
  gap_keys?: string[]
}
interface ApprovedDraft { editing: boolean; name: string; include: string; exclude: string; examplesText: string; notice: string; token: string }

const data = ref<SkillCandidateList | null>(null)
const loading = ref(false)
const error = ref('')
const globalNotice = ref('')
const tab = ref<'pending' | 'approved' | 'handled'>('pending')
const notices = reactive<Record<string, string>>({})
const pendingBodies = reactive<Record<string, ReviewBody>>({})
const busyIds = reactive(new Set<string>())
const approvedDrafts = reactive<Record<string, ApprovedDraft>>({})
let controller: AbortController | null = null
let returnTarget: HTMLElement | null = null

const pending = computed(() => data.value?.suggestions.filter((item) => item.status === 'pending') ?? [])
const approved = computed(() => data.value?.approved_skills ?? [])
const unpublished = computed(() => approved.value.filter((skill) => !skill.published))
const handled = computed(() => (data.value?.suggestions.filter((item) => item.status !== 'pending') ?? [])
  .slice().sort((left, right) => String(right.reviewed_at ?? '').localeCompare(String(left.reviewed_at ?? ''))))

function chapterOf(suggestion: SkillCandidateSuggestion) {
  return props.index?.chapters.find((chapter) => chapter.id === suggestion.chapter_key)
}
function sectionLabel(sectionKey: string) {
  for (const chapter of props.index?.chapters ?? []) {
    const found = chapter.sections.find((section) => section.id === sectionKey)
    if (found) return found.label
  }
  return sectionKey
}
function outcomeOf(suggestion: SkillCandidateSuggestion): string {
  if (suggestion.status === 'rejected') return '已驳回'
  const result = suggestion.result ?? {}
  if (Array.isArray(result.linked)) return `已采纳 · 归入已有技能（${result.linked.length} 个判定点）`
  if (typeof result.approved_skill_id === 'string') {
    const name = approved.value.find((skill) => skill.skill_id === result.approved_skill_id)?.name
    return `已采纳 · 已批准待发布${name ? `「${name}」` : ''}`
  }
  if (Array.isArray(result.dismissed)) return '已采纳 · 保持只归小节'
  return '已采纳'
}
function canRetract(suggestion: SkillCandidateSuggestion): boolean {
  if (suggestion.status !== 'accepted' || !suggestion.result) return false
  if (Array.isArray(suggestion.result.linked)) return false
  const skillId = typeof suggestion.result.approved_skill_id === 'string' ? suggestion.result.approved_skill_id : ''
  if (skillId && approved.value.find((skill) => skill.skill_id === skillId)?.published) return false
  return true
}

async function load() {
  controller?.abort()
  if (!props.volumeId) { data.value = null; return }
  const request = new AbortController()
  controller = request
  loading.value = true; error.value = ''
  try {
    const value = await questionBankApi.skillCandidates(props.volumeId, request.signal)
    if (!request.signal.aborted) data.value = value
  } catch { if (!request.signal.aborted) error.value = '技能候选暂时无法读取，请重试。' }
  finally { if (!request.signal.aborted) loading.value = false }
}

async function send(suggestionId: string, body: ReviewBody) {
  pendingBodies[suggestionId] = body
  busyIds.add(suggestionId)
  delete notices[suggestionId]
  try {
    const result = await questionBankApi.reviewSkillCandidate(suggestionId, body)
    delete pendingBodies[suggestionId]
    const linked = result.result.linked
    if (Array.isArray(linked)) {
      const skipped = Array.isArray(result.result.skipped) ? result.result.skipped as { gap_key?: string; reason?: string }[] : []
      globalNotice.value = `已挂上 ${linked.length} 个判定点` + (skipped.length ? `；未挂 ${skipped.length} 个：${skipped.map((item) => item.reason || '').filter(Boolean).join('；')}` : '')
    } else {
      globalNotice.value = ''
    }
    emit('changed')
    await load()
  } catch (failure) {
    if (isAmbiguousWriteError(failure)) {
      notices[suggestionId] = '提交结果尚未确认。重新提交会沿用原请求编号。'
    } else {
      delete pendingBodies[suggestionId]
      if (failure instanceof ApiError && failure.status === 409) {
        notices[suggestionId] = '内容已更新，请重新查看后再操作。'
        await load()
      } else {
        notices[suggestionId] = '操作未能提交，请重试。'
      }
    }
  } finally { busyIds.delete(suggestionId) }
}

function buildBody(suggestion: SkillCandidateSuggestion, decision: ReviewBody['decision'], extra: Partial<ReviewBody> = {}): ReviewBody {
  return { decision, expected_revision: data.value?.revision ?? 0, request_token: crypto.randomUUID().replace(/-/g, ''), ...extra }
}
function accept(suggestion: SkillCandidateSuggestion, payload: { edits: SkillCandidateReviewEdits | null; gapKeys: string[] | null }) {
  const extra: Partial<ReviewBody> = {}
  if (payload.edits) extra.edits = payload.edits
  if (payload.gapKeys) extra.gap_keys = payload.gapKeys
  void send(suggestion.suggestion_id, buildBody(suggestion, 'accept', extra))
}
const reject = (suggestion: SkillCandidateSuggestion) => void send(suggestion.suggestion_id, buildBody(suggestion, 'reject'))
const reopen = (suggestion: SkillCandidateSuggestion) => void send(suggestion.suggestion_id, buildBody(suggestion, 'reopen'))
const resubmit = (suggestion: SkillCandidateSuggestion) => {
  const body = pendingBodies[suggestion.suggestion_id]
  if (body) void send(suggestion.suggestion_id, body)
}

function approvedDraft(skill: SkillCandidateApprovedSkill): ApprovedDraft {
  return approvedDrafts[skill.skill_id] ??= { editing: false, name: skill.name, include: skill.include, exclude: skill.exclude, examplesText: skill.examples.join('\n'), notice: '', token: '' }
}
async function saveApproved(skill: SkillCandidateApprovedSkill) {
  const draft = approvedDraft(skill)
  if (!draft.token) draft.token = crypto.randomUUID().replace(/-/g, '')
  draft.notice = ''
  try {
    await questionBankApi.updateApprovedSkill(skill.skill_id, {
      name: draft.name.trim(), include: draft.include, exclude: draft.exclude,
      examples: draft.examplesText.split('\n').map((line) => line.trim()).filter(Boolean).slice(0, 3),
      expected_revision: data.value?.revision ?? 0, request_token: draft.token,
    })
    draft.editing = false; draft.token = ''
    emit('changed')
    await load()
  } catch (failure) {
    if (isAmbiguousWriteError(failure)) draft.notice = '提交结果尚未确认。再次保存会沿用原请求编号。'
    else if (failure instanceof ApiError && failure.status === 409) { draft.notice = '内容已更新，请重新查看后再操作。'; await load() }
    else { draft.notice = '保存失败，请重试。'; draft.token = '' }
  }
}

watch(() => props.open, (open) => {
  if (!open) { controller?.abort(); return }
  returnTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
  tab.value = 'pending'
  globalNotice.value = ''
  void load()
}, { immediate: true })
function restoreFocus(event: Event) { event.preventDefault(); returnTarget?.focus({ preventScroll: true }) }
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <DialogRoot :open="open" @update:open="!$event && emit('close')"><DialogPortal>
    <div v-if="open" class="qb-modal-layer" @click.self="emit('close')">
      <DialogContent class="sc-review qb-import-dialog" :aria-describedby="undefined" @close-auto-focus="restoreFocus">
        <header class="sc-review__header">
          <DialogTitle as="h2">审核技能候选</DialogTitle>
          <button class="qb-link" aria-label="关闭审核面板" @click="emit('close')">关闭</button>
        </header>
        <nav class="sc-review__tabs" aria-label="候选分组">
          <button type="button" :class="{ 'is-active': tab === 'pending' }" @click="tab = 'pending'">待审核 ({{ pending.length }})</button>
          <button type="button" :class="{ 'is-active': tab === 'approved' }" @click="tab = 'approved'">已批准待发布 ({{ approved.length }})</button>
          <button type="button" :class="{ 'is-active': tab === 'handled' }" @click="tab = 'handled'">已处理</button>
        </nav>
        <p v-if="loading" role="status">正在读取技能候选…</p>
        <p v-if="globalNotice" role="status" class="sc-review__note">{{ globalNotice }}</p>
        <p v-if="error" role="alert" class="qb-feedback is-error">{{ error }} <button class="qb-link" @click="load">重试</button></p>
        <div v-if="data" class="sc-review__body">
          <template v-if="tab === 'pending'">
            <p v-if="!pending.length" class="sc-review__empty">当前没有待审核的技能候选。</p>
            <SkillCandidateCard v-for="suggestion in pending" :key="suggestion.suggestion_id"
              :suggestion="suggestion" :chapter="chapterOf(suggestion)" :approved="unpublished"
              :busy="busyIds.has(suggestion.suggestion_id)" :notice="notices[suggestion.suggestion_id] || ''"
              :has-pending="!!pendingBodies[suggestion.suggestion_id]"
              @accept="accept(suggestion, $event)" @reject="reject(suggestion)" @resubmit="resubmit(suggestion)"
              @open-question="emit('openQuestion', $event)" />
          </template>
          <template v-else-if="tab === 'approved'">
            <p class="sc-review__note">这些新技能会在学期标准修订时统一发布。</p>
            <p v-if="!approved.length" class="sc-review__empty">还没有已批准的新技能。</p>
            <article v-for="skill in approved" :key="skill.skill_id" class="sc-card">
              <header class="sc-card__head"><strong>{{ skill.name }}</strong>
                <span class="sc-badge">{{ sectionLabel(skill.section_key) }}</span>
                <span v-if="skill.published" class="sc-badge is-done">已发布</span>
              </header>
              <p class="sc-card__reason">{{ skill.gap_refs.length }} 个判定点将归入此技能。</p>
              <template v-if="approvedDraft(skill).editing">
                <div class="sc-card__fields">
                  <label>名称 <input v-model="approvedDraft(skill).name" class="app-input" maxlength="120"></label>
                  <label>判定时看什么（纳入）<textarea v-model="approvedDraft(skill).include" class="app-input" rows="2" /></label>
                  <label>不包括（排除）<textarea v-model="approvedDraft(skill).exclude" class="app-input" rows="2" /></label>
                  <label>典型表现（每行一条，最多 3 条）<textarea v-model="approvedDraft(skill).examplesText" class="app-input" rows="3" /></label>
                </div>
                <p v-if="approvedDraft(skill).notice" role="alert" class="qb-feedback is-error">{{ approvedDraft(skill).notice }}</p>
                <footer class="sc-card__actions">
                  <AppButton variant="secondary" @click="approvedDraft(skill).editing = false">取消</AppButton>
                  <AppButton variant="primary" :disabled="skill.published" @click="saveApproved(skill)">保存</AppButton>
                </footer>
              </template>
              <template v-else>
                <dl class="sc-card__meta">
                  <div><dt>纳入</dt><dd>{{ skill.include || '—' }}</dd></div>
                  <div><dt>排除</dt><dd>{{ skill.exclude || '—' }}</dd></div>
                  <div><dt>典型表现</dt><dd>{{ skill.examples.join('；') || '—' }}</dd></div>
                </dl>
                <footer v-if="!skill.published" class="sc-card__actions">
                  <AppButton variant="secondary" @click="approvedDraft(skill).editing = true">修改文字</AppButton>
                </footer>
              </template>
            </article>
          </template>
          <template v-else>
            <p v-if="!handled.length" class="sc-review__empty">还没有处理过的候选。</p>
            <article v-for="suggestion in handled" :key="suggestion.suggestion_id" class="sc-card">
              <header class="sc-card__head"><strong>{{ chapterOf(suggestion)?.label || suggestion.chapter_key }}</strong>
                <span class="sc-badge is-done">{{ outcomeOf(suggestion) }}</span>
              </header>
              <p class="sc-card__reason">{{ suggestion.reason }}</p>
              <p v-if="notices[suggestion.suggestion_id]" role="alert" class="qb-feedback is-error">{{ notices[suggestion.suggestion_id] }}</p>
              <footer v-if="canRetract(suggestion)" class="sc-card__actions">
                <AppButton v-if="pendingBodies[suggestion.suggestion_id]" variant="secondary" @click="resubmit(suggestion)">重新提交</AppButton>
                <AppButton v-else variant="secondary" :disabled="busyIds.has(suggestion.suggestion_id)" @click="reopen(suggestion)">撤回</AppButton>
              </footer>
            </article>
          </template>
        </div>
      </DialogContent>
    </div>
  </DialogPortal></DialogRoot>
</template>
