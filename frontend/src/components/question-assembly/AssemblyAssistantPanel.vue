<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { fetchStudents } from '../../api/students'
import { knowledgeLeafLabel } from '../../api/question-bank'
import type { AssemblyQuestion } from '../../api/assembly'
import { useAssemblyStore } from '../../stores/assembly'
import { useAssemblyAssistantStore } from '../../stores/assembly-assistant'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import DifficultyRangeFilter from '../question-bank/DifficultyRangeFilter.vue'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'

const emit = defineEmits<{ edit: [] }>()
const assistant = useAssemblyAssistantStore()
const assembly = useAssemblyStore()
const curriculum = useCurriculumScopeStore()
const classes = ref<string[]>([])
const rosterState = ref<'loading' | 'ready' | 'error'>('loading')
const expanded = ref(new Set<number>())
const similarOpen = ref(new Set<number>())
const similarItems = ref(new Map<number, AssemblyQuestion[]>())
const actionMessage = ref('')
const weaknessList = ref<HTMLElement | null>(null)
const selectedKey = computed(() => assistant.selectedKey)
const weaknesses = computed(() => new Map(assistant.result?.weaknesses.map(item => [item.knowledge_key, item]) ?? []))
const selectedWeakness = computed(() => weaknesses.value.get(selectedKey.value))
const questionMap = computed(() => new Map(assistant.questions.map(item => [item.id, item])))
const candidates = computed(() => (assistant.result?.candidates ?? []).slice(0, assistant.visibleCount).flatMap(item => {
  const question = questionMap.value.get(item.question_id)
  return question ? [{ question, fitCount: item.suitable_student_count, remediationCount: item.remediation_student_count, consolidationCount: item.consolidation_student_count, newCount: item.new_practice_student_count, uncertainCount: item.uncertain_student_count, difficultyBasis: item.difficulty_basis, matchLabel: item.selection_kind === 'task_matched' ? item.match_label : item.match_level ? `${item.match_level}级 · ${item.match_label}` : '', practiceKind: item.practice_kind ?? 'focus', band: item.difficulty_band ?? 'unknown', similarIds: item.similar_question_ids ?? [], targets: item.target_keys.flatMap(key => weaknesses.value.get(key) ?? []) }] : []
}))
const busy = computed(() => assistant.state === 'loading' || assistant.waiting)
const rangeChoice = computed(() => assistant.filters.chapter_id ? `focused:${assistant.filters.chapter_id}`
  : assistant.filters.teaching_progress_chapter_id ? `through:${assistant.filters.teaching_progress_chapter_id}` : '')
function changeRange(value: string): void {
  const [mode, id = ''] = value.split(':')
  assistant.changeScope({ chapter_id: mode === 'focused' ? id : '', teaching_progress_chapter_id: mode === 'through' ? id : '' })
}
const BAND_LABELS: Record<string, string> = { suitable: '难度合适', lower: '难度较低', higher: '难度较高' }

async function moveSelection(delta: number): Promise<void> {
  assistant.moveSelection(delta)
  await nextTick()
  weaknessList.value?.querySelector('.is-selected')?.scrollIntoView?.({ block: 'nearest' })
}

function onKeydown(event: KeyboardEvent): void {
  if (event.ctrlKey || event.metaKey || event.altKey) return
  const target = event.target
  if (target instanceof Element && target.closest('input, select, textarea, [contenteditable="true"]')) return
  const key = event.key.toLowerCase()
  if (key === 'w') { event.preventDefault(); void moveSelection(-1) }
  else if (key === 's') { event.preventDefault(); void moveSelection(1) }
}
watch(() => [assistant.filters.question_type, assistant.filters.exclude_exam_originals, assistant.filters.exclude_recent], () => assistant.scheduleSearch())
const canAdd = computed(() => !busy.value && !assistant.isStale && assistant.state === 'ready'
  && assembly.loadState !== 'loading' && assembly.loadState !== 'error' && assembly.saveState !== 'saving')
function percent(value: number | null): string { return value === null ? '暂无数据' : `${Math.round(value * 100)}%` }
function inBasket(id: number): boolean { return assembly.draft.basket_ids.includes(id) }
async function toggleBasket(id: number): Promise<void> {
  const removing = inBasket(id)
  const ok = removing ? await assembly.removeQuestion(id) : await assembly.addQuestions([id], true)
  actionMessage.value = ok ? (removing ? '已移出试卷篮' : '已加入试卷篮，可继续选题') : assembly.message
}
async function toggleSimilar(id: number, ids: number[]): Promise<void> {
  if (similarOpen.value.delete(id)) return
  similarOpen.value.add(id)
  if (!similarItems.value.has(id)) similarItems.value.set(id, await assistant.previewsFor(ids))
}
async function loadClasses(): Promise<void> {
  rosterState.value = 'loading'
  try {
    classes.value = [...new Set((await fetchStudents()).flatMap(item => item.class_name ? [item.class_name] : []))].sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true }))
    if (!classes.value.includes(assistant.filters.class_id)) assistant.changeScope({ class_id: classes.value.length === 1 ? classes.value[0]! : '' })
    rosterState.value = 'ready'
  } catch { rosterState.value = 'error' }
}
watch(() => curriculum.selectedVolumeId, volume => {
  if (assistant.filters.curriculum_volume_id !== (volume ?? '')) assistant.changeScope({ curriculum_volume_id: volume ?? '', chapter_id: '', teaching_progress_chapter_id: '' })
}, { immediate: true })
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  void loadClasses()
  void curriculum.initialize()
  if (assembly.loadState === 'idle' || assembly.loadState === 'error') void assembly.load()
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <section class="assembly-assistant" aria-labelledby="assistant-title">
    <header class="assistant-heading">
      <div><h2 id="assistant-title">从班级薄弱处，找到值得练的题</h2><p>依据本学期考试与最新训练掌握情况，先缩小候选范围，再由你选题成卷。</p></div>
      <span class="assistant-local">本地筛选 · 无模型费用</span>
    </header>

    <form class="assistant-filters" @submit.prevent="assistant.search()">
      <label>练习班级<select :value="assistant.filters.class_id" :disabled="rosterState !== 'ready'" @change="assistant.changeScope({ class_id: ($event.target as HTMLSelectElement).value })"><option value="">请选择班级</option><option v-for="name in classes" :key="name" :value="name">{{ name }}</option></select></label>
      <label class="assistant-chapter">训练范围<select :value="rangeChoice" :disabled="!curriculum.selectedVolume" @change="changeRange(($event.target as HTMLSelectElement).value)"><option value="">综合训练 · 按已有作答涉及的最晚章节</option><optgroup label="综合训练 · 已学到"><option v-for="chapter in curriculum.selectedVolume?.chapters ?? []" :key="chapter.id" :value="`through:${chapter.id}`">本册开头至{{ chapter.label }}</option></optgroup><optgroup label="专项训练"><option v-for="chapter in curriculum.selectedVolume?.chapters ?? []" :key="chapter.id" :value="`focused:${chapter.id}`">仅{{ chapter.label }}</option></optgroup></select></label>
      <label>题型<select v-model="assistant.filters.question_type"><option value="">全部题型</option><option>选择题</option><option>多选题</option><option>填空题</option><option>解答题</option></select></label>
      <DifficultyRangeFilter v-model:min="assistant.filters.difficulty_min" v-model:max="assistant.filters.difficulty_max" class="assistant-difficulty" :ceiling="8" compact @change="assistant.scheduleSearch()" />
      <button class="assembly-button is-primary" type="submit" :disabled="!assistant.canSearch || rosterState !== 'ready'">{{ busy ? '正在筛选…' : assistant.result ? '刷新学情与题库' : '查看班级知识与技能' }}</button>
      <div class="assistant-exclusions"><span>训练与考试合并，排除每名学生最近3次已有批改结果的原题；同技能最多1道、解答题最多2道，相似题受限。</span></div>
    </form>
    <p v-if="rosterState === 'error'" class="assistant-notice" role="alert">班级列表暂时无法读取。<button type="button" class="assembly-link" @click="loadClasses">重新读取</button></p>
    <p v-if="assembly.loadState === 'error'" class="assistant-notice" role="alert">{{ assembly.message }} <button type="button" class="assembly-link" @click="assembly.load()">重新读取试卷篮</button></p>
    <p v-if="assistant.message" class="assistant-notice" role="alert">{{ assistant.message }} <button v-if="assistant.state === 'error'" type="button" class="assembly-link" @click="assistant.resetTargets(); assistant.search()">重新查看班级薄弱点</button></p>
    <p v-if="assistant.isStale" class="assistant-notice" role="status">{{ busy ? '正在按新的选择更新候选题…' : '筛选条件已调整，更新后即可选题。' }}</p>

    <div v-if="assistant.result" class="assistant-evidence-summary">
      <strong>{{ assistant.filters.class_id }} · {{ assistant.result.student_count }} 人</strong>
      <span>本学期 {{ assistant.result.exam_count }} 场考试</span>
      <span>考试平均得分率 <b>{{ percent(assistant.result.exam_score_rate) }}</b>（{{ assistant.result.exam_student_count }} 人有成绩）</span>
      <span v-if="selectedWeakness">当前重点：<b>{{ knowledgeLeafLabel(selectedWeakness.knowledge_point) }}</b></span>
    </div>

    <div v-if="assistant.result" class="assistant-workspace" :aria-busy="busy">
      <aside ref="weaknessList" class="assistant-weaknesses">
        <div class="assistant-section-title"><h3>班级知识与技能</h3><small>W / S 键切换</small></div>
        <p class="assistant-help">点击卡片查看该点候选题；薄弱点在前，掌握较好的知识点在后；可搭配基础巩固题。</p>
        <div v-if="!assistant.result.weaknesses.length" class="assistant-empty">{{ !assistant.result.student_count ? '这个班级暂无可用学生，请检查班级名单。' : !assistant.result.evidence_student_count ? '本学期尚无可用掌握证据，暂不能判断班级薄弱点。可先完成考试批改，或前往题库选题。' : '当前范围没有可用知识点证据，可调整章节或前往题库选题。' }}</div>
        <button v-for="point in assistant.result.weaknesses" :key="point.knowledge_key" type="button" class="assistant-weakness" :class="{ 'is-selected': selectedKey === point.knowledge_key, 'is-mastered': point.weak_student_count === 0 }" :aria-pressed="selectedKey === point.knowledge_key" @click="assistant.selectTarget(point.knowledge_key)">
          <span><small v-if="point.weak_student_count === 0" class="assistant-foundation-label">可搭配巩固</small><strong :title="point.knowledge_point">{{ knowledgeLeafLabel(point.knowledge_point) }}</strong><span class="assistant-weakness-count"><b>{{ point.weak_student_count }}</b> 人需关注 <small>/ {{ point.evidence_student_count }} 人有证据</small></span><span class="assistant-weakness-detail">掌握度 {{ percent(point.mastery) }} · 考试得分率 {{ percent(point.exam_score_rate) }}<template v-if="point.target_difficulty != null"> · 适配约 {{ point.target_difficulty }} 级</template></span><small v-if="assistant.candidateCountFor(point.knowledge_key) !== null">当前条件下 {{ assistant.candidateCountFor(point.knowledge_key) }} 道关联题</small></span>
        </button>
      </aside>

      <section class="assistant-candidates" aria-label="候选题">
        <div class="assistant-section-title"><div><h3>{{ assistant.result.candidate_total }} 道候选题 · 已显示 {{ candidates.length }} 道</h3><p>与个人、小组共用匹配规则，显示适合人数和用途；无直接证据的合适题可作新练习，由你选择成卷。</p></div><button type="button" class="assembly-button is-secondary" @click="emit('edit')">试卷篮 · {{ assembly.selectedQuestionCount }} 题 →</button></div>
        <p v-if="actionMessage" class="assistant-action" role="status">{{ actionMessage }}</p>
        <div v-if="!candidates.length" class="assistant-empty"><strong>{{ selectedKey ? '当前条件下没有合适的候选题' : '先选择本次要练习的重点' }}</strong><p>{{ selectedKey ? '可调整题型、难度或所选目标，候选题会自动更新。' : '点击左侧薄弱点卡片，候选题会自动显示。' }}</p></div>
        <article v-for="(item, index) in candidates" :key="item.question.id" class="assistant-question" :class="{ 'is-in-basket': inBasket(item.question.id) }">
          <header><span class="assistant-question-number">{{ String(index + 1).padStart(2, '0') }}</span><span>{{ item.question.question_type || '未分类' }} · 难度 {{ item.question.difficulty ?? '待定' }}</span><span v-if="BAND_LABELS[item.band]" class="assistant-band" :class="`is-${item.band}`">{{ BAND_LABELS[item.band] }}</span><span class="assistant-purpose" :class="{ 'is-foundation': item.practiceKind === 'foundation' }">{{ item.practiceKind === 'foundation' ? '基础与巩固' : '补弱练习' }}</span><button v-if="item.similarIds.length" type="button" class="assistant-similar-toggle" :aria-expanded="similarOpen.has(item.question.id)" @click="toggleSimilar(item.question.id, item.similarIds)">相似 ×{{ item.similarIds.length }}</button><span class="assistant-question-source">{{ item.question.paper_title || '题库题目' }}</span><strong v-if="inBasket(item.question.id)" class="assistant-added">已在试卷篮</strong></header>
          <div class="assistant-reason"><span>练习依据</span><strong v-if="item.matchLabel">{{ item.matchLabel }}</strong><p v-for="point in item.targets" :key="point.knowledge_key"><b>{{ knowledgeLeafLabel(point.knowledge_point) }}</b> · {{ point.weak_student_count }} 人需关注 / {{ point.evidence_student_count }} 人有证据</p></div>
          <p v-if="item.fitCount !== undefined" class="assistant-help">适合 {{ item.fitCount }} 人 · 针对练习 {{ item.remediationCount ?? 0 }} 人 · 巩固 {{ item.consolidationCount ?? 0 }} 人 · 新练习 {{ item.newCount ?? 0 }} 人<span v-if="item.uncertainCount"> · {{ item.uncertainCount }} 人缺少同技能多次依据</span>。{{ item.difficultyBasis }}</p><QuestionContentRenderer :blocks="item.question.rich_content?.question_blocks" :fallback="item.question.question_text" media-mode="list" paper-media-flow dense typeset-text />
          <div v-if="expanded.has(item.question.id)" class="assistant-answer"><strong>答案与解析</strong><QuestionContentRenderer :blocks="item.question.rich_content?.answer_blocks" :fallback="item.question.answer_text" empty-label="暂未录入答案或解析" compact typeset-text /></div>
          <footer><button type="button" class="assembly-link" :aria-expanded="expanded.has(item.question.id)" @click="expanded.has(item.question.id) ? expanded.delete(item.question.id) : expanded.add(item.question.id)">{{ expanded.has(item.question.id) ? '收起解析' : '查看解析' }}</button><button type="button" class="assembly-button" :class="{ 'is-primary': !inBasket(item.question.id) }" :disabled="!canAdd" @click="toggleBasket(item.question.id)">{{ inBasket(item.question.id) ? '移出试卷篮' : '加入试卷篮' }}</button></footer>
          <div v-if="similarOpen.has(item.question.id)" class="assistant-similar-list">
            <article v-for="member in similarItems.get(item.question.id) ?? []" :key="member.id" class="assistant-similar-item" :class="{ 'is-in-basket': inBasket(member.id) }">
              <header><span>{{ member.question_type || '未分类' }} · 难度 {{ member.difficulty ?? '待定' }}</span><span class="assistant-question-source">{{ member.paper_title || '题库题目' }}</span><strong v-if="inBasket(member.id)" class="assistant-added">已在试卷篮</strong></header>
              <QuestionContentRenderer :blocks="member.rich_content?.question_blocks" :fallback="member.question_text" media-mode="list" paper-media-flow dense typeset-text />
              <footer><button type="button" class="assembly-button" :class="{ 'is-primary': !inBasket(member.id) }" :disabled="!canAdd" @click="toggleBasket(member.id)">{{ inBasket(member.id) ? '移出试卷篮' : '加入试卷篮' }}</button></footer>
            </article>
          </div>
        </article>
        <button v-if="assistant.hasMore" type="button" class="assembly-button assistant-load-more" :disabled="busy || assistant.isStale || assistant.loadingMore" @click="assistant.loadMore()">{{ assistant.loadingMore ? '正在读取题目…' : '继续查看候选题' }}</button>
      </section>
    </div>
    <div v-else class="assistant-intro"><span>01 选班级与范围</span><i>→</i><span>02 确定薄弱点</span><i>→</i><span>03 挑题加入试卷篮</span><p>候选题依据已有学情和题库标签筛选。选题、调整顺序与导出由你完成。</p></div>
  </section>
</template>

<style scoped>
.assembly-assistant { display: grid; gap: var(--space-5); }
.assistant-heading, .assistant-section-title { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); }
.assistant-heading h2 { margin: 0; font-size: 22px; }
.assistant-heading p, .assistant-section-title p { margin: 8px 0 0; color: var(--color-text-secondary); }
.assistant-local { color: var(--color-accent); background: var(--color-accent-subtle); padding: 7px 12px; border-radius: var(--radius-control); white-space: nowrap; font-size: 13px; }
.assistant-filters { display: flex; flex-wrap: wrap; align-items: end; gap: var(--space-4); padding: var(--space-5); border: 1px solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-surface); }
.assistant-filters > label { display: grid; gap: 7px; flex: 1; min-width: 110px; font-size: 13px; font-weight: 600; color: var(--color-text-secondary); }
.assistant-filters > .assistant-chapter { flex: 2; min-width: 240px; }
.assistant-difficulty { flex: 2; min-width: 270px; }
.assistant-filters select { width: 100%; min-height: 40px; border: 1px solid var(--color-border-strong); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-primary); padding: 8px; font: inherit; }
.assistant-exclusions { display: flex; flex-wrap: wrap; gap: 16px; flex-basis: 100%; align-items: center; color: var(--color-text-secondary); font-size: 13px; }
.assistant-exclusions label { display: flex; gap: 6px; align-items: center; }
.assistant-exclusions > span { margin-left: auto; }
.assistant-notice { padding: 12px 16px; background: var(--color-warning-subtle); color: var(--color-warning); border-radius: var(--radius-control); margin: 0; }
.assistant-evidence-summary { display: flex; flex-wrap: wrap; gap: 12px 24px; padding: 0 4px; color: var(--color-text-secondary); font-size: 14px; }
.assistant-evidence-summary strong, .assistant-evidence-summary b { color: var(--color-text-primary); }
.assistant-workspace { display: grid; grid-template-columns: minmax(260px, 320px) minmax(0, 1fr); gap: var(--space-5); align-items: start; }
.assistant-weaknesses { padding: var(--space-4); border: 1px solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-surface); }
.assistant-section-title h3 { margin: 0; font-size: 17px; }
.assistant-section-title small, .assistant-help { color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; }
.assistant-help { margin: 12px 0 16px; }
.assistant-weakness { display: flex; gap: 10px; align-items: start; width: 100%; padding: 14px 10px; border: 0; border-top: 1px solid var(--color-border-subtle); border-radius: var(--radius-control); cursor: pointer; background: none; font: inherit; color: inherit; text-align: left; }
.assistant-weakness:hover { background: var(--color-bg-subtle); }
.assistant-weakness.is-selected { background: var(--color-accent-subtle); }
.assistant-weakness:focus-visible { outline: 2px solid var(--color-accent); outline-offset: -2px; }
.assistant-weakness > span { display: grid; gap: 7px; min-width: 0; }
.assistant-weakness strong { font-size: 14px; line-height: 1.5; overflow-wrap: anywhere; }
.assistant-weakness-count { font-size: 14px; }
.assistant-weakness-count b { font-size: 21px; color: var(--color-accent); }
.assistant-weakness small, .assistant-weakness-detail { font-size: 12px; color: var(--color-text-secondary); line-height: 1.5; }
.assistant-candidates { display: grid; gap: var(--space-4); min-width: 0; }
.assistant-candidates > .assistant-section-title { padding: 4px 0 8px; }
.assistant-section-title p { font-size: 13px; line-height: 1.6; }
.assistant-section-title button { flex-shrink: 0; }
.assistant-question { padding: var(--space-5); border: 1px solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-surface); overflow: hidden; }
.assistant-question.is-in-basket { border-color: var(--color-accent); }
.assistant-question > header { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; color: var(--color-text-secondary); font-size: 13px; }
.assistant-question-number { color: var(--color-accent); font-weight: 700; font-size: 18px; }
.assistant-question-source { flex: 1; }
.assistant-added, .assistant-action { color: var(--color-accent); font-size: 13px; }
.assistant-action { margin: 0; }
.assistant-reason { background: var(--color-bg-subtle); padding: 10px 14px; margin: 14px 0 20px; border-left: 3px solid var(--color-accent); color: var(--color-text-secondary); font-size: 13px; line-height: 1.6; }
.assistant-reason > span { color: var(--color-accent); font-size: 12px; }
.assistant-reason p { margin: 3px 0 0; }
.assistant-reason b { color: var(--color-text-primary); font-weight: 500; }
.assistant-purpose { background: var(--color-accent-subtle); color: var(--color-accent); padding: 4px 8px; border-radius: var(--radius-control); }
.assistant-band { padding: 4px 8px; border-radius: var(--radius-control); background: var(--color-bg-subtle); color: var(--color-text-secondary); }
.assistant-band.is-suitable { background: var(--color-accent-subtle); color: var(--color-accent); }
.assistant-foundation-label, .assistant-purpose.is-foundation { color: #34724f; }
.assistant-load-more { justify-self: center; }
.assistant-question :deep(.question-html img), .assistant-question :deep(.question-content__media img) { max-width: min(100%, 240px); max-height: 150px; width: auto; height: auto; }
.assistant-answer { margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--color-border-default); }
.assistant-answer > strong { display: block; font-size: 13px; margin-bottom: 12px; color: var(--color-text-secondary); }
.assistant-question footer { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-top: 22px; }
.assistant-similar-toggle { border: 1px solid var(--color-border-strong); background: var(--color-bg-subtle); color: var(--color-text-secondary); border-radius: var(--radius-control); padding: 4px 8px; font: inherit; font-size: 12px; cursor: pointer; }
.assistant-similar-toggle:hover { color: var(--color-accent); border-color: var(--color-accent); }
.assistant-similar-list { display: grid; gap: 10px; margin-top: 16px; padding-top: 14px; border-top: 1px dashed var(--color-border-default); }
.assistant-similar-item { padding: 12px 14px; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-subtle); }
.assistant-similar-item.is-in-basket { border-color: var(--color-accent); }
.assistant-similar-item > header { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: var(--color-text-secondary); font-size: 12px; margin-bottom: 8px; }
.assistant-similar-item footer { display: flex; justify-content: flex-end; margin-top: 10px; }
.assistant-empty, .assistant-intro { padding: 32px; text-align: center; color: var(--color-text-secondary); line-height: 1.8; border-radius: var(--radius-panel); background: var(--color-bg-surface); }
.assistant-weaknesses .assistant-empty { padding: 20px 4px; font-size: 13px; }
.assistant-intro { padding: 64px 24px; border: 1px solid var(--color-border-default); }
.assistant-intro span { font-weight: 600; color: var(--color-text-primary); }
.assistant-intro i { padding: 0 24px; font-style: normal; }
.assistant-intro p { margin: 24px 0 0; font-size: 14px; }
input[type=checkbox] { accent-color: var(--color-accent); }
@media (max-width: 950px) { .assistant-workspace { grid-template-columns: minmax(230px, 270px) minmax(0, 1fr); } .assistant-candidates > .assistant-section-title { align-items: start; flex-direction: column; } }
@media (max-width: 700px) { .assistant-heading { align-items: start; flex-direction: column; } .assistant-workspace { grid-template-columns: minmax(0, 1fr); } .assistant-exclusions > span { margin-left: 0; } .assistant-intro { display: grid; gap: 12px; padding: 28px; } .assistant-intro i { display: none; } }
</style>
