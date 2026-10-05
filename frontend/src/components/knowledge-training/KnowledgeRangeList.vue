<script setup lang="ts">
import { computed, ref } from 'vue'
import { knowledgeLeafLabel, type CurriculumVolume } from '../../api/question-bank'
import type { TrainingDiagnosis } from '../../api/training'
import StatePanel from '../design-system/StatePanel.vue'
type Tier = 'weak' | 'unsteady' | 'stable' | 'insufficient'
const props = defineProps<{ volume: CurriculumVolume | null; diagnosis: TrainingDiagnosis; mode: 'range' | 'select-one'; studentIds?: string[]; purpose?: 'training' | 'handout' | 'wrong_book'; progressId?: string }>()
const rangeKeys = defineModel<string[]>('rangeKeys', { default: () => [] })
const scopeMode = defineModel<'comprehensive' | 'focused'>('scopeMode', { default: 'comprehensive' })
const progress = defineModel<string>('teachingProgressChapterId', { default: '' })
const chapterKey = defineModel<string>('chapterKey', { default: '' })
const sectionKey = defineModel<string>('sectionKey', { default: '' })
const expanded = ref<string[]>([])
const labels: Record<Tier, string> = { weak: '明显薄弱', unsteady: '还不稳', stable: '较稳定', insufficient: '证据不足' }
const tiers: Tier[] = ['weak', 'unsteady', 'stable', 'insufficient']
const chapters = computed(() => [...(props.volume?.chapters ?? [])].sort((a, b) => a.order - b.order))
const selectedProgress = computed(() => chapters.value.find(chapter => chapter.id === (progress.value || props.progressId)))
const selectedStudents = computed(() => props.studentIds === undefined ? props.diagnosis.students : props.diagnosis.students.filter(student => props.studentIds?.includes(student.student_id)))
const statistics = computed(() => {
  const nodes = new Map((props.diagnosis.knowledge_catalog ?? []).map(node => [node.knowledge_key, node]))
  const sections = new Set(chapters.value.flatMap(chapter => chapter.sections.map(section => section.knowledge_id)))
  const ancestry = new Map<string, string>()
  const sectionFor = (start: string) => {
    if (ancestry.has(start)) return ancestry.get(start)!
    let key: string | null | undefined = start
    const seen = new Set<string>()
    while (key && !sections.has(key) && !seen.has(key)) { seen.add(key); key = nodes.get(key)?.parent_knowledge_key }
    const found = key && sections.has(key) ? key : ''
    ancestry.set(start, found); return found
  }
  const people = new Map<string, Map<string, Tier>>()
  const points = new Map<string, Map<string, { key: string; label: string; weak: number; unsteady: number }>>()
  for (const student of selectedStudents.value) {
    const seen = new Set<string>()
    for (const point of student.weak_points) {
      if (seen.has(point.knowledge_key)) continue
      seen.add(point.knowledge_key)
      const node = nodes.get(point.knowledge_key)
      if (!['skill', 'topic'].includes(node?.node_kind ?? '') || !(point.observation_count || point.evidence_count || point.source_question_refs?.length)) continue
      const key = sectionFor(point.knowledge_key)
      if (!key) continue
      const tier = point.tier ?? 'insufficient'
      const members = people.get(key) ?? new Map<string, Tier>()
      const previous = members.get(student.student_id)
      if (!previous || tiers.indexOf(tier) < tiers.indexOf(previous)) members.set(student.student_id, tier)
      people.set(key, members)
      const sectionPoints = points.get(key) ?? new Map()
      const counts = sectionPoints.get(point.knowledge_key) ?? { key: point.knowledge_key, label: knowledgeLeafLabel(point.knowledge_point), weak: 0, unsteady: 0 }
      if (tier === 'weak' || tier === 'unsteady') counts[tier] += 1
      sectionPoints.set(point.knowledge_key, counts); points.set(key, sectionPoints)
    }
  }
  return new Map([...sections].map(key => {
    const members = people.get(key) ?? new Map<string, Tier>()
    return [key, { total: members.size, counts: Object.fromEntries(tiers.map(tier => [tier, [...members.values()].filter(value => value === tier).length])) as Record<Tier, number>,
      points: [...(points.get(key)?.values() ?? [])] }]
  }))
})
function select(chapter: string, section = '') { chapterKey.value = chapter; sectionKey.value = section }
function toggleSection(key: string) {
  const chapter = chapters.value.find(item => item.sections.some(section => section.knowledge_id === key))
  const keys = chapter && rangeKeys.value.includes(chapter.knowledge_id)
    ? [...rangeKeys.value.filter(item => item !== chapter.knowledge_id), ...chapter.sections.map(section => section.knowledge_id)]
    : rangeKeys.value
  rangeKeys.value = keys.includes(key) ? keys.filter(item => item !== key) : [...new Set([...keys, key])]
}
function chapterChecked(chapter: CurriculumVolume['chapters'][number]) { return rangeKeys.value.includes(chapter.knowledge_id) || chapter.sections.every(section => rangeKeys.value.includes(section.knowledge_id)) }
function toggleChapter(chapter: CurriculumVolume['chapters'][number]) {
  const keys = chapter.sections.map(section => section.knowledge_id)
  const keep = rangeKeys.value.filter(key => key !== chapter.knowledge_id && !keys.includes(key))
  rangeKeys.value = chapterChecked(chapter) ? keep : [...keep, ...keys]
}
function toggleExpanded(key: string) { expanded.value = expanded.value.includes(key) ? expanded.value.filter(item => item !== key) : [...expanded.value, key] }
function unlearned(order: number) { return props.mode === 'range' && scopeMode.value === 'comprehensive' && selectedProgress.value && order > selectedProgress.value.order }
</script>
<template>
  <section class="practice-box knowledge-range-list" aria-label="章节与小节">
    <header class="practice-box-heading"><strong>{{ mode === 'range' ? '训练范围' : '章节与小节' }}</strong></header>
    <div v-if="mode === 'range'" class="range-tools">
      <div class="practice-segment" aria-label="训练范围模式"><button type="button" :aria-pressed="scopeMode === 'comprehensive'" :class="{ 'is-active': scopeMode === 'comprehensive' }" @click="scopeMode = 'comprehensive'">综合</button><button type="button" :aria-pressed="scopeMode === 'focused'" :class="{ 'is-active': scopeMode === 'focused' }" @click="scopeMode = 'focused'">专项</button></div>
      <label v-if="scopeMode === 'comprehensive'">已学到<select v-model="progress" class="app-input" aria-label="已学到的章节"><option value="">{{ selectedProgress?.label ?? '按所选学生的作答推断' }}</option><option v-for="chapter in chapters" :key="chapter.id" :value="chapter.id">{{ chapter.label }}</option></select></label>
    </div>
    <p v-if="purpose === 'wrong_book'" class="range-book-note">错题本：综合＝所选考试的全部错题；专项＝只收所勾章节的错题</p>
    <div class="range-legend"><span v-for="tier in tiers" :key="tier"><i :class="tier" />{{ labels[tier] }}</span></div>
    <p class="range-stat-basis">{{ mode === 'range' ? '按已选学生统计，每人取本节最弱档位' : '按当前范围学生统计，每人取本节最弱档位' }}</p>
    <StatePanel v-if="mode === 'range' && !selectedStudents.length" kind="empty" compact title="勾选学生后显示" />
    <div v-for="chapter in chapters" :key="chapter.id" class="range-chapter" :class="{ 'is-unlearned': unlearned(chapter.order) }">
      <header>
        <label v-if="mode === 'range' && scopeMode === 'focused'"><input type="checkbox" :aria-label="`选择${chapter.label}`" :checked="chapterChecked(chapter)" @change="toggleChapter(chapter)">{{ chapter.label }}</label>
        <button v-else-if="mode === 'select-one'" type="button" :class="{ 'is-active': chapterKey === chapter.knowledge_id && !sectionKey }" @click="select(chapter.knowledge_id)">{{ chapter.label }}</button>
        <strong v-else>{{ chapter.label }}</strong><span v-if="unlearned(chapter.order)" class="range-unlearned-label">未学</span>
      </header>
      <div v-for="section in [...chapter.sections].sort((a, b) => a.order - b.order)" :key="section.id" class="range-section">
        <div class="range-section-row">
          <label v-if="mode === 'range' && scopeMode === 'focused'"><input type="checkbox" :aria-label="`选择${section.label}`" :checked="rangeKeys.includes(section.knowledge_id) || rangeKeys.includes(chapter.knowledge_id)" @change="toggleSection(section.knowledge_id)"><span>{{ section.label }}</span></label>
          <button v-else-if="mode === 'select-one'" type="button" :class="{ 'is-active': sectionKey === section.knowledge_id }" @click="select(chapter.knowledge_id, section.knowledge_id)">{{ section.label }}</button>
          <span v-else>{{ section.label }}</span><button type="button" class="practice-link" :aria-label="`${expanded.includes(section.knowledge_id) ? '收起' : '展开'}${section.label}`" :aria-expanded="expanded.includes(section.knowledge_id)" @click="toggleExpanded(section.knowledge_id)">{{ expanded.includes(section.knowledge_id) ? '▴' : '▾' }}</button>
        </div>
        <div v-if="statistics.get(section.knowledge_id)?.total" class="range-section-stats">
          <div class="range-tier-bar" :aria-label="tiers.map(tier => `${labels[tier]} ${statistics.get(section.knowledge_id)!.counts[tier]} 人`).join('，')"><i v-for="tier in tiers" :key="tier" :class="tier" :style="{ flexGrow: statistics.get(section.knowledge_id)!.counts[tier] }" /></div>
          <small v-if="statistics.get(section.knowledge_id)!.counts.weak">明显薄弱 {{ statistics.get(section.knowledge_id)!.counts.weak }} 人</small>
        </div>
        <div v-if="expanded.includes(section.knowledge_id)" class="range-points"><div v-for="point in statistics.get(section.knowledge_id)?.points ?? []" :key="point.key"><span>{{ point.label }}</span><small>明显薄弱 {{ point.weak }} 人 · 还不稳 {{ point.unsteady }} 人</small></div><p v-if="!statistics.get(section.knowledge_id)?.points.length">暂无作答观测</p></div>
      </div>
    </div>
  </section>
</template>
<style scoped>
.range-tools{display:flex;align-items:center;gap:var(--space-3);flex-wrap:wrap;padding:var(--space-3);border-bottom:1px solid var(--color-border-subtle)}.range-tools label{display:flex;gap:var(--space-2);align-items:center;font-size:var(--font-size-caption);flex:1}.range-tools select{min-width:0;width:100%}.range-book-note,.range-stat-basis{margin:0;padding:var(--space-2) var(--space-3);font-size:var(--font-size-caption);color:var(--color-text-muted);line-height:1.6}.range-book-note{color:var(--color-accent)}
.range-legend{display:flex;gap:var(--space-2);flex-wrap:wrap;padding:var(--space-2) var(--space-3);font-size:var(--font-size-caption);color:var(--color-text-secondary)}.range-legend span{display:flex;align-items:center;gap:4px}.range-legend i{width:9px;height:9px;border-radius:2px}.weak{background:var(--color-danger)}.unsteady{background:var(--color-warning)}.stable{background:var(--color-success)}.insufficient{background:var(--color-border-default)}
.range-chapter{border-top:1px solid var(--color-border-subtle);padding:var(--space-2) 0}.range-chapter>header{display:flex;gap:var(--space-2);align-items:center;padding:var(--space-2) var(--space-3);font-size:var(--font-size-dense);font-weight:var(--font-weight-semibold)}.range-chapter label{display:flex;gap:var(--space-2);align-items:center;cursor:pointer}.range-chapter button{border:0;background:transparent;text-align:left;font:inherit;cursor:pointer;color:inherit;padding:0}.range-chapter button.is-active{color:var(--color-accent);font-weight:var(--font-weight-semibold)}.range-section{padding:var(--space-2) var(--space-3) var(--space-2) var(--space-5)}.range-section-row{display:flex;justify-content:space-between;align-items:start;gap:var(--space-2);font-size:var(--font-size-dense)}.range-section-stats{display:flex;align-items:center;gap:var(--space-2);margin-top:var(--space-2);min-width:0}.range-tier-bar{display:flex;gap:1px;height:5px;min-width:20px;flex:1;border-radius:3px;overflow:hidden}.range-tier-bar i{flex-basis:0}.range-section-stats small{font-size:var(--font-size-caption);color:var(--color-danger);white-space:nowrap;flex-shrink:0}.range-points{margin-top:var(--space-2);padding-left:var(--space-3);border-left:2px solid var(--color-border-subtle)}.range-points>div{display:flex;justify-content:space-between;flex-wrap:wrap;gap:var(--space-1);padding:var(--space-1) 0;font-size:var(--font-size-caption)}.range-points small,.range-points p{color:var(--color-text-muted);font-size:var(--font-size-caption)}.is-unlearned{opacity:.48}.range-unlearned-label{font-size:var(--font-size-caption);font-weight:var(--font-weight-regular);color:var(--color-text-muted)}input{accent-color:var(--color-accent)}
</style>
<style scoped>
.range-tools label{white-space:nowrap}.range-tools select{flex:1}
.range-section{display:grid;grid-template-columns:minmax(0,1fr) minmax(140px,.85fr);align-items:center}
.range-section-stats{padding-left:0;margin-top:0}.range-points{grid-column:1/-1}
@media(max-width:1199px){.range-section{grid-template-columns:minmax(0,1fr) minmax(160px,.65fr)}}
@media(max-width:520px){.range-section{display:block}.range-section-stats{padding-left:var(--space-4)}}
</style>
