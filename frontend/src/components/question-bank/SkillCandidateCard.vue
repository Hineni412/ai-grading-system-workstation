<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import AppButton from '../design-system/AppButton.vue'
import type {
  QuestionSkillIndex,
  SkillCandidateApprovedSkill,
  SkillCandidateEditKind,
  SkillCandidateReviewEdits,
  SkillCandidateSuggestion,
} from '../../api/question-bank'

type Chapter = QuestionSkillIndex['chapters'][number]
type Choice = SkillCandidateEditKind

const props = defineProps<{
  suggestion: SkillCandidateSuggestion
  chapter: Chapter | undefined
  approved: SkillCandidateApprovedSkill[]
  busy: boolean
  notice: string
  hasPending: boolean
}>()
const emit = defineEmits<{
  accept: [payload: { edits: SkillCandidateReviewEdits | null; gapKeys: string[] | null }]
  reject: []
  resubmit: []
  openQuestion: [ref: { questionId: number; paperId: number | null }]
}>()

const draft = reactive({
  choice: 'link_existing' as Choice,
  skillKey: '',
  approvedSkillId: '',
  name: '',
  sectionKey: '',
  include: '',
  exclude: '',
  examplesText: '',
  checked: {} as Record<string, boolean>,
})

watch(() => props.suggestion.suggestion_id, () => init(), { immediate: true })
function init() {
  const suggestion = props.suggestion
  draft.choice = suggestion.decision
  draft.skillKey = suggestion.skill_key
  draft.approvedSkillId = props.approved[0]?.skill_id ?? ''
  draft.name = suggestion.new_skill.name
  draft.sectionKey = suggestion.new_skill.section_key || props.chapter?.sections[0]?.id || ''
  draft.include = suggestion.new_skill.include
  draft.exclude = suggestion.new_skill.exclude
  draft.examplesText = suggestion.new_skill.examples.slice(0, 3).join('\n')
  draft.checked = Object.fromEntries(suggestion.gap_refs.map((ref) => [ref.gap_key, true]))
}

const decisionLabels: Record<string, string> = {
  link_existing: '归入已有技能',
  new_skill: '新技能',
  keep_section: '保持只归小节',
}
const skills = computed(() => {
  const chapter = props.chapter
  if (!chapter) return []
  const entries = [...chapter.sections.flatMap((section) => section.skills), ...chapter.cross_section_skills]
  return [...new Map(entries.map((skill) => [skill.stable_key, skill])).values()]
})
const checkedKeys = computed(() => props.suggestion.gap_refs.filter((ref) => draft.checked[ref.gap_key]).map((ref) => ref.gap_key))
const partialSelection = computed(() => checkedKeys.value.length < props.suggestion.gap_refs.length)
const examples = computed(() => draft.examplesText.split('\n').map((line) => line.trim()).filter(Boolean).slice(0, 3))
const acceptBlocked = computed(() => {
  if (props.suggestion.stale && draft.choice !== 'keep_section') return true
  if (!checkedKeys.value.length) return true
  if (draft.choice === 'link_existing' && !draft.skillKey) return true
  if (draft.choice === 'new_skill' && (!draft.name.trim() || !draft.sectionKey)) return true
  if (draft.choice === 'merge_into_approved' && !draft.approvedSkillId) return true
  return false
})
const newSkillEdited = computed(() => (
  draft.name !== props.suggestion.new_skill.name
  || draft.sectionKey !== props.suggestion.new_skill.section_key
  || draft.include !== props.suggestion.new_skill.include
  || draft.exclude !== props.suggestion.new_skill.exclude
  || examples.value.join('\n') !== props.suggestion.new_skill.examples.join('\n')
))
const explanation = computed(() => draft.choice === 'link_existing'
  ? '采纳后立即为这些判定点挂上该技能，会影响掌握度和推荐。'
  : draft.choice === 'new_skill' || draft.choice === 'merge_into_approved'
    ? '采纳后进入“已批准待发布”，暂不影响掌握度和推荐。'
    : '')

function submit() {
  let edits: SkillCandidateReviewEdits | null = null
  if (draft.choice !== props.suggestion.decision) {
    edits = { kind: draft.choice }
  } else if (draft.choice === 'new_skill' && newSkillEdited.value) {
    edits = { kind: 'new_skill' }
  } else if (draft.choice === 'link_existing' && draft.skillKey !== props.suggestion.skill_key) {
    edits = { kind: 'link_existing' }
  }
  if (edits) {
    if (edits.kind === 'link_existing') edits.skill_key = draft.skillKey
    else if (edits.kind === 'new_skill') Object.assign(edits, { name: draft.name.trim(), section_key: draft.sectionKey, include: draft.include, exclude: draft.exclude, examples: examples.value })
    else if (edits.kind === 'merge_into_approved') edits.approved_skill_id = draft.approvedSkillId
  }
  emit('accept', { edits, gapKeys: partialSelection.value ? checkedKeys.value : null })
}
</script>

<template>
  <article class="sc-card" :class="{ 'is-stale': suggestion.stale }">
    <header class="sc-card__head">
      <strong>{{ chapter?.label || suggestion.chapter_key }}</strong>
      <span class="sc-badge">{{ decisionLabels[suggestion.decision] }}</span>
      <span v-if="suggestion.stale" class="sc-badge is-stale">标准已更新</span>
    </header>
    <p class="sc-card__reason">{{ suggestion.reason }}</p>
    <ul class="sc-card__gaps">
      <li v-for="ref in suggestion.gap_refs" :key="ref.gap_key">
        <label><input v-model="draft.checked[ref.gap_key]" type="checkbox" :disabled="busy">
          {{ ref.paper_title || '题目' }} 第{{ ref.question_number || '—' }}题 · 判定点：{{ ref.target }}</label>
        <button class="qb-link" type="button" @click="emit('openQuestion', { questionId: ref.question_id, paperId: ref.paper_id ?? null })">看题</button>
      </li>
    </ul>
    <fieldset class="sc-card__choices" :disabled="busy">
      <label><input v-model="draft.choice" type="radio" value="link_existing" :disabled="suggestion.stale"> 归入已有技能</label>
      <select v-if="draft.choice === 'link_existing'" v-model="draft.skillKey" class="app-input">
        <option v-for="skill in skills" :key="skill.stable_key" :value="skill.stable_key">{{ skill.display_name }}</option>
      </select>
      <label><input v-model="draft.choice" type="radio" value="new_skill" :disabled="suggestion.stale"> 作为新技能</label>
      <div v-if="draft.choice === 'new_skill'" class="sc-card__fields">
        <label>名称 <input v-model="draft.name" class="app-input" maxlength="120"></label>
        <label>所属小节 <select v-model="draft.sectionKey" class="app-input">
          <option v-for="section in chapter?.sections ?? []" :key="section.id" :value="section.id">{{ section.label }}</option>
        </select></label>
        <label>判定时看什么（纳入）<textarea v-model="draft.include" class="app-input" rows="2" /></label>
        <label>不包括（排除）<textarea v-model="draft.exclude" class="app-input" rows="2" /></label>
        <label>典型表现（每行一条，最多 3 条）<textarea v-model="draft.examplesText" class="app-input" rows="3" /></label>
      </div>
      <label><input v-model="draft.choice" type="radio" value="merge_into_approved" :disabled="suggestion.stale || !approved.length"> 并入已批准的新技能</label>
      <select v-if="draft.choice === 'merge_into_approved'" v-model="draft.approvedSkillId" class="app-input">
        <option v-for="skill in approved" :key="skill.skill_id" :value="skill.skill_id">{{ skill.name }}</option>
      </select>
      <label><input v-model="draft.choice" type="radio" value="keep_section"> 保持只归小节</label>
    </fieldset>
    <p v-if="explanation" class="sc-card__explain">{{ explanation }}</p>
    <p v-if="notice" role="alert" class="qb-feedback is-error">{{ notice }}</p>
    <footer class="sc-card__actions">
      <AppButton v-if="hasPending" variant="primary" :disabled="busy" @click="emit('resubmit')">重新提交</AppButton>
      <template v-else>
        <AppButton variant="primary" :disabled="busy || acceptBlocked" @click="submit">采纳</AppButton>
        <AppButton variant="secondary" :disabled="busy" @click="emit('reject')">驳回</AppButton>
      </template>
    </footer>
  </article>
</template>
