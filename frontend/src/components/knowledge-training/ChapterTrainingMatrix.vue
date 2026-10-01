<script setup lang="ts">
import { computed, onMounted, ref, useSlots, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { tierClass, masteryDetail, parentMasteryDetails, tierOf } from '../knowledge-overview/model'

import { knowledgeLeafLabel } from '../../api/question-bank'
import type { TrainingDiagnosis, TrainingWeakPoint } from '../../api/training'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'

const props = defineProps<{
  diagnosis: TrainingDiagnosis
  modelValue: string[]
  groupScopeLabel: string
  initialChapterKey?: string
  initialSectionKey?: string
}>()
const emit = defineEmits<{
  'update:modelValue': [keys: string[]]
  'adjust-scope': []
  'scope-change': [scope: { chapterKey: string; sectionKey: string }]
}>()

const slots = useSlots()
const viewMode = ref<'student' | 'group' | 'recommendations'>(slots.recommendations ? 'recommendations' : 'student')
const activeChapterKey = ref(props.initialChapterKey ?? '')
const activeSectionKey = ref(props.initialSectionKey ?? '')
const selectedCell = ref<{ studentId: string; knowledgeKey: string } | null>(null)
const selectedGroupKey = ref('')

const curriculumScope = useCurriculumScopeStore()
onMounted(() => { void curriculumScope.initialize() })

// 目录标签是"册｜章｜小节｜细分点"全路径；章名取第二段，其余层级只显示末段。
function pathParts(value: string): string[] {
  return value.split(/[|｜]/).map((part) => part.trim()).filter(Boolean)
}

function chapterLabel(value: string): string {
  const parts = pathParts(value)
  return parts.length >= 2 ? (parts[1] ?? value) : value
}

const catalog = computed(() => props.diagnosis.knowledge_catalog ?? [])
const chapters = computed(() => {
  const all = catalog.value.filter((item) => !item.parent_knowledge_key)
  const volume = curriculumScope.selectedVolume?.label
  if (!volume) return all
  return all.filter((item) => pathParts(item.knowledge_point)[0] === volume)
})
const sections = computed(() => catalog.value.filter((item) => item.parent_knowledge_key === activeChapterKey.value))
const activeChapter = computed(() => chapters.value.find(
  (item) => item.knowledge_key === activeChapterKey.value,
) ?? null)
// 小节为空表示整章视图：汇总每个小节的直接子点（无子点的小节取小节自身）。
const points = computed(() => {
  if (activeSectionKey.value) {
    const direct = catalog.value.filter((item) => item.parent_knowledge_key === activeSectionKey.value)
    if (direct.length) return direct
    return catalog.value.filter((item) => item.knowledge_key === activeSectionKey.value)
  }
  if (!sections.value.length) return activeChapter.value ? [activeChapter.value] : []
  return sections.value.flatMap((section) => {
    const direct = catalog.value.filter((item) => item.parent_knowledge_key === section.knowledge_key)
    return direct.length ? direct : [section]
  })
})
const groupMap = computed(() => new Map((props.diagnosis.group_weak_points ?? []).map((item) => [
  item.knowledge_key,
  item,
])))

// 默认只展示有证据的知识点，减少拥挤；可用开关查看完整小节内容。
const showEmptyPoints = ref(false)
const visiblePoints = computed(() => showEmptyPoints.value
  ? points.value
  : points.value.filter((point) => (groupMap.value.get(point.knowledge_key)?.evidence_count ?? 0) > 0))
const hiddenPointCount = computed(() => points.value.length - visiblePoints.value.length)

function weakFor(studentId: string, knowledgeKey: string): TrainingWeakPoint | null {
  return props.diagnosis.students.find((student) => student.student_id === studentId)
    ?.weak_points.find((point) => point.knowledge_key === knowledgeKey) ?? null
}

// 默认只展示当前章/节内有证据的学生，减少拥挤；可用开关查看完整名单。
const showEmptyStudents = ref(false)
function studentHasEvidence(studentId: string): boolean {
  return points.value.some((point) => (
    (weakFor(studentId, point.knowledge_key)?.evidence_count ?? 0) > 0
  ))
}
const visibleStudents = computed(() => showEmptyStudents.value
  ? props.diagnosis.students
  : props.diagnosis.students.filter((student) => studentHasEvidence(student.student_id)))
const hiddenStudentCount = computed(() => props.diagnosis.students.length - visibleStudents.value.length)

watch(chapters, (items) => {
  if (!items.some((item) => item.knowledge_key === activeChapterKey.value)) {
    activeChapterKey.value = items[0]?.knowledge_key ?? ''
  }
}, { immediate: true })
// 整章视图（空小节）始终合法；小节 key 失效时回到整章视图。
watch(sections, (items) => {
  if (activeSectionKey.value && !items.some((item) => item.knowledge_key === activeSectionKey.value)) {
    activeSectionKey.value = ''
  }
}, { immediate: true })
watch(points, (items) => {
  if (!items.some((item) => item.knowledge_key === selectedGroupKey.value)) {
    selectedGroupKey.value = ''
  }
})
watch([activeChapterKey, activeSectionKey], () => {
  selectedCell.value = null
  selectedGroupKey.value = ''
  emit('scope-change', { chapterKey: activeChapterKey.value, sectionKey: activeSectionKey.value })
}, { immediate: true })

function selectChapter(key: string): void {
  activeChapterKey.value = key
  activeSectionKey.value = ''
}

function selectSection(key: string): void {
  activeSectionKey.value = key
}

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`
}

function heatClass(tier: string | null | undefined): string { return tierClass(tier) }

function toggleTarget(key: string): void {
  emit('update:modelValue', props.modelValue.includes(key)
    ? props.modelValue.filter((item) => item !== key)
    : [...props.modelValue, key])
}

const selectedWeak = computed(() => selectedCell.value
  ? weakFor(selectedCell.value.studentId, selectedCell.value.knowledgeKey)
  : null)

const selectedEvidenceSummary = computed(() => {
  const refs = selectedWeak.value?.source_question_refs ?? []
  const fine = refs.filter(ref => ['part', 'step'].includes(String(ref.assessment?.granularity)))
  const missing = fine.filter(ref => ref.assessment?.eligible === false).length
  const coarse = refs.length - fine.length
  const difficulties = [...new Set(fine.map(ref => ref.assessment?.part_difficulty)
    .filter((value): value is number => typeof value === 'number'))]
  return [fine.length ? `${fine.length} 条小问或步骤来源` : '',
    coarse ? `${coarse} 条整题来源，细分依据尚未补齐` : '',
    missing ? `${missing} 条空白或待复核记录未计入掌握度` : '',
    difficulties.length ? `来源小问预估难度 ${difficulties.join('、')}（1–10），有效记录按此难度计算` : '',
    fine.some(ref => ref.assessment?.reason === 'part_composite_attribution_limited') ? '小问综合表现，内部归因有限' : '',
  ].filter(Boolean).join('；')
})
const selectedStudent = computed(() => props.diagnosis.students.find(
  (student) => student.student_id === selectedCell.value?.studentId,
) ?? null)
const selectedGroup = computed(() => selectedGroupKey.value
  ? groupMap.value.get(selectedGroupKey.value) ?? null
  : null)
const parentReferences = computed(() => {
  const selected = viewMode.value === 'student' ? selectedWeak.value : selectedGroup.value
  if (!selected || tierOf(selected.tier) !== 'insufficient') return []
  return parentMasteryDetails(selected.knowledge_key,
    viewMode.value === 'student' ? selectedStudent.value?.weak_points ?? [] : props.diagnosis.group_weak_points ?? [],
    catalog.value)
})
const groupRows = computed(() => visiblePoints.value.map((point) => {
  const aggregate = groupMap.value.get(point.knowledge_key) ?? null
  const contributorCount = props.diagnosis.students.filter((student) => (
    (weakFor(student.student_id, point.knowledge_key)?.evidence_count ?? 0) > 0
  )).length
  return {
    key: point.knowledge_key,
    label: knowledgeLeafLabel(point.knowledge_point),
    fullLabel: point.knowledge_point,
    aggregate,
    contributorCount,
    missingCount: Math.max(0, props.diagnosis.students.length - contributorCount),
  }
}))
</script>

<template>
  <section class="chapter-training" aria-label="按章节训练">
    <header class="chapter-training__toolbar">
      <div>
        <strong>章节训练视图</strong>
        <span>{{ slots.recommendations ? '先核对共同训练小组，掌握度明细按需查看' : '同一章或小节可查看个人明细，也可查看当前范围的群体加权结果' }}</span>
      </div>
      <div class="chapter-training__modes" aria-label="章节训练展示方式">
        <button v-if="slots.recommendations" type="button" :class="{ 'is-active': viewMode === 'recommendations' }" @click="viewMode = 'recommendations'">推荐小组</button>
        <button type="button" :class="{ 'is-active': viewMode === 'student' }" @click="viewMode = 'student'">学生明细</button>
        <button type="button" :class="{ 'is-active': viewMode === 'group' }" @click="viewMode = 'group'">群体平均</button>
      </div>
    </header>

    <div class="chapter-training__body" :class="{ 'is-recommending': viewMode === 'recommendations' }">
      <aside class="chapter-training__scope">
        <header><strong>章节与小节</strong><span>点章名看整章汇总，点小节只看单节</span></header>
        <p v-if="!chapters.length" class="chapter-training__empty">当前学期在此范围内没有章节。</p>
        <template v-for="chapter in chapters" :key="chapter.knowledge_key">
          <button
            type="button"
            :class="{ 'is-active': chapter.knowledge_key === activeChapterKey }"
            :title="chapter.knowledge_point"
            @click="selectChapter(chapter.knowledge_key)"
          >
            <span>{{ chapterLabel(chapter.knowledge_point) }}</span>
            <b>{{ percent(groupMap.get(chapter.knowledge_key)?.mastery) }}</b>
          </button>
          <div v-if="chapter.knowledge_key === activeChapterKey" class="chapter-training__sections">
            <button
              type="button"
              :class="{ 'is-active': activeSectionKey === '' }"
              @click="selectSection('')"
            >
              整章汇总
            </button>
            <button
              v-for="section in sections"
              :key="section.knowledge_key"
              type="button"
              :class="{ 'is-active': section.knowledge_key === activeSectionKey }"
              :title="section.knowledge_point"
              @click="selectSection(section.knowledge_key)"
            >
              {{ knowledgeLeafLabel(section.knowledge_point) }}
            </button>
          </div>
        </template>
      </aside>

      <main class="chapter-training__main">
        <slot v-if="viewMode === 'recommendations'" name="recommendations" :scope-key="activeSectionKey || activeChapterKey" />
        <template v-else>
        <header>
          <div>
            <span>{{ activeSectionKey ? sections.find((item) => item.knowledge_key === activeSectionKey)?.knowledge_point : activeChapter?.knowledge_point }}</span>
            <h2>{{ viewMode === 'student' ? '学生 × 知识点' : '群体平均 × 知识点' }}</h2>
          </div>
          <div class="chapter-training__head-tools">
            <label class="chapter-training__toggle">
              <input v-model="showEmptyPoints" type="checkbox">
              显示无证据知识点<template v-if="hiddenPointCount">（{{ hiddenPointCount }}）</template>
            </label>
            <label class="chapter-training__toggle">
              <input v-model="showEmptyStudents" type="checkbox">
              显示无证据学生<template v-if="hiddenStudentCount">（{{ hiddenStudentCount }}）</template>
            </label>
            <strong v-if="viewMode === 'student'">{{ visibleStudents.length }} 名学生 · {{ visiblePoints.length }} 个知识点</strong>
            <div v-else class="chapter-training__group-scope">
              <span>统计群体</span>
              <b>{{ groupScopeLabel }}</b>
              <button type="button" @click="emit('adjust-scope')">调整</button>
            </div>
          </div>
        </header>

        <p v-if="points.length && !visiblePoints.length" class="chapter-training__empty-hint">
          当前范围的知识点都没有证据；勾选右上角“显示无证据知识点”可查看完整内容。
        </p>
        <p v-else-if="viewMode === 'student' && diagnosis.students.length && !visibleStudents.length" class="chapter-training__empty-hint">
          当前范围所有学生都没有证据；勾选右上角“显示无证据学生”可查看完整名单。
        </p>

        <template v-if="viewMode === 'student'">
          <div class="chapter-training__scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">学生</th>
                  <th v-for="point in visiblePoints" :key="point.knowledge_key" scope="col">
                    <label :title="point.knowledge_point">
                      <input type="checkbox" :checked="modelValue.includes(point.knowledge_key)" @change="toggleTarget(point.knowledge_key)">
                      <span>{{ knowledgeLeafLabel(point.knowledge_point) }}</span>
                      <b>{{ percent(groupMap.get(point.knowledge_key)?.mastery) }}</b>
                    </label>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="student in visibleStudents" :key="student.student_id">
                  <th scope="row"><strong>{{ student.student_name }}</strong><span>{{ student.class_id }} · {{ student.student_code }}</span></th>
                  <td v-for="point in visiblePoints" :key="point.knowledge_key">
                    <button
                      type="button"
                      :class="[heatClass(weakFor(student.student_id, point.knowledge_key)?.tier), { 'is-selected': selectedCell?.studentId === student.student_id && selectedCell?.knowledgeKey === point.knowledge_key }]"
                      @click="selectedCell = { studentId: student.student_id, knowledgeKey: point.knowledge_key }"
                    >
                      {{ percent(weakFor(student.student_id, point.knowledge_key)?.mastery) }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <footer><span>明显薄弱</span><span>还不稳</span><span>较稳定</span><span>灰色为证据不足</span></footer>
        </template>

        <div v-else class="chapter-training__groups">
          <div
            v-for="row in groupRows"
            :key="row.key"
            :class="{ 'is-selected': selectedGroupKey === row.key }"
          >
            <input
              type="checkbox"
              :checked="modelValue.includes(row.key)"
              :aria-label="`选择${row.label}作为训练知识点`"
              @click.stop
              @change="toggleTarget(row.key)"
            >
            <button type="button" :title="row.fullLabel" @click="selectedGroupKey = row.key">
              <strong>{{ row.label }}</strong>
              <span class="chapter-training__bar"><i :class="heatClass(row.aggregate?.tier)" :style="{ width: `${(row.aggregate?.mastery ?? 0) * 100}%` }"></i></span>
              <b>{{ percent(row.aggregate?.mastery) }}</b>
              <small>{{ row.contributorCount }} / {{ diagnosis.students.length }} 人有效</small>
              <small>{{ row.aggregate?.evidence_count ?? 0 }} 条证据</small>
            </button>
          </div>
          <p class="chapter-training__group-note">群体掌握度是有证据学生的平均值；无证据学生不进入分母。群体颜色表示人数最多的档位。</p>
        </div>
        </template>
      </main>

      <aside v-if="viewMode !== 'recommendations'" class="chapter-training__detail">
        <template v-if="viewMode === 'student' && selectedWeak && selectedStudent">
          <span>当前学生</span>
          <h2>{{ selectedStudent.student_name }}</h2>
          <strong :title="selectedWeak.knowledge_point">{{ knowledgeLeafLabel(selectedWeak.knowledge_point) }}</strong>
          <div :class="['chapter-training__score', heatClass(selectedWeak.tier)]">{{ percent(selectedWeak.mastery) }}</div><p>{{ masteryDetail(selectedWeak) }}</p>
          <p v-for="reference in parentReferences" :key="reference">上级参考 · {{ reference }}</p>
          <dl><div><dt>证据</dt><dd>{{ selectedWeak.evidence_count }} 条</dd></div><div><dt>考试</dt><dd>{{ selectedWeak.exam_count }} 场</dd></div></dl>
          <p v-if="selectedEvidenceSummary">{{ selectedEvidenceSummary }}</p>
          <button type="button" @click="toggleTarget(selectedWeak.knowledge_key)">{{ modelValue.includes(selectedWeak.knowledge_key) ? '移出训练目标' : '加入训练目标' }}</button>
          <RouterLink
            class="chapter-training__evidence-link"
            :to="{ name: 'student-evidence', params: { studentId: selectedStudent.student_id }, query: { from: 'chapter', knowledge: selectedWeak.knowledge_key, klabel: knowledgeLeafLabel(selectedWeak.knowledge_point) } }"
          >查看证据详情</RouterLink>
        </template>
        <template v-else-if="viewMode === 'group' && selectedGroup">
          <span>当前群体</span>
          <h2 :title="selectedGroup.knowledge_point">{{ knowledgeLeafLabel(selectedGroup.knowledge_point) }}</h2>
          <div :class="['chapter-training__score', heatClass(selectedGroup.tier)]">{{ percent(selectedGroup.mastery) }}</div><p>{{ masteryDetail(selectedGroup) }}</p>
          <p v-for="reference in parentReferences" :key="reference">上级参考 · {{ reference }}</p>
          <dl><div><dt>有效证据</dt><dd>{{ selectedGroup.evidence_count }} 条</dd></div><div><dt>有效权重</dt><dd>{{ (selectedGroup.effective_weight ?? 0).toFixed(1) }}</dd></div></dl>
          <button type="button" @click="toggleTarget(selectedGroup.knowledge_key)">{{ modelValue.includes(selectedGroup.knowledge_key) ? '移出训练目标' : '加入训练目标' }}</button>
        </template>
        <template v-else>
          <span>当前范围</span>
          <h2>{{ viewMode === 'student' ? '选择一个掌握度格子' : '选择一行群体结果' }}</h2>
          <p>{{ viewMode === 'student' ? '查看某名学生在具体知识点上的掌握情况。' : '查看当前筛选、某个班级或全部班级的掌握平均值。' }}</p>
        </template>
        <section><strong>本次已选 {{ modelValue.length }} 项</strong><p v-if="!modelValue.length">尚未选择训练知识点。</p><ul v-else><li v-for="key in modelValue" :key="key">{{ knowledgeLeafLabel(catalog.find((item) => item.knowledge_key === key)?.knowledge_point ?? key) }}</li></ul></section>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.chapter-training{overflow:hidden;border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface)}
.chapter-training__toolbar{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:.75rem 1rem;border-bottom:1px solid var(--color-border-default)}
.chapter-training__toolbar>div:first-child{display:flex;gap:.8rem;align-items:baseline}
.chapter-training__toolbar span{color:var(--color-text-secondary);font-size:.78rem}
.chapter-training__modes{display:flex;padding:var(--space-1);border-radius:var(--radius-control);background:var(--color-bg-subtle)}
.chapter-training__modes button{padding:.45rem .75rem;border:0;border-radius:calc(var(--radius) - 2px);background:transparent;color:var(--color-text-secondary);cursor:pointer}
.chapter-training__modes button.is-active{background:var(--color-bg-surface);color:var(--color-accent-active);font-weight:700}
.chapter-training__body{display:grid;grid-template-columns:220px minmax(560px,1fr) 230px;min-height:560px}
.chapter-training__body.is-recommending{grid-template-columns:220px minmax(0,1fr)}
.chapter-training__scope{padding:.8rem;border-right:1px solid var(--color-border-default);background:var(--color-bg-subtle)}
.chapter-training__scope header{display:grid;gap:.15rem;padding:.35rem .4rem .75rem}
.chapter-training__scope header span{color:var(--color-text-secondary);font-size:.76rem}
.chapter-training__empty{padding:.5rem .4rem;color:var(--color-text-secondary);font-size:.82rem}
.chapter-training__head-tools{display:flex;align-items:center;gap:.9rem}
.chapter-training__toggle{display:inline-flex;align-items:center;gap:.3rem;color:var(--color-text-secondary);font-size:.78rem;white-space:nowrap;cursor:pointer}
.chapter-training__empty-hint{margin:0 0 .8rem;padding:.6rem .8rem;border:1px dashed var(--color-border-default);border-radius:var(--radius-control);color:var(--color-text-secondary);font-size:.84rem}
.chapter-training__scope>button{display:flex;justify-content:space-between;gap:.5rem;width:100%;margin-bottom:.3rem;padding:.6rem;border:1px solid transparent;border-radius:var(--radius-control);background:transparent;text-align:left;cursor:pointer}
.chapter-training__scope>button.is-active{border-color:var(--color-accent);background:var(--color-bg-surface);box-shadow:inset 3px 0 var(--color-accent)}
.chapter-training__sections{display:grid;gap:.25rem;margin:.65rem 0 0 .65rem;padding-left:.65rem;border-left:2px solid var(--color-border-default)}
.chapter-training__sections button{padding:.45rem .55rem;border:0;border-radius:calc(var(--radius) - 2px);background:transparent;color:var(--color-text-secondary);text-align:left;cursor:pointer}
.chapter-training__sections button.is-active{background:var(--color-accent-subtle);color:var(--color-accent-active);font-weight:700}
.chapter-training__main{min-width:0;padding:1rem}
.chapter-training__main>header{display:flex;justify-content:space-between;align-items:end;gap:1rem;margin-bottom:.8rem}
.chapter-training__main h2{margin:.15rem 0 0;font-size:1.2rem}
.chapter-training__main header span{color:var(--color-text-secondary);font-size:.76rem}
.chapter-training__group-scope{display:flex;align-items:center;gap:.5rem}
.chapter-training__group-scope button{padding:.35rem .55rem;border:1px solid var(--color-border-default);border-radius:calc(var(--radius) - 2px);background:var(--color-bg-surface);color:var(--color-accent-active);cursor:pointer}
.chapter-training__scroll{overflow:auto;border:1px solid var(--color-border-default);border-radius:var(--radius-control)}
.chapter-training table{width:100%;min-width:740px;border-collapse:collapse;table-layout:fixed}
.chapter-training th,.chapter-training td{padding:.38rem;border-right:1px solid var(--color-border-default);border-bottom:1px solid var(--color-border-default)}
.chapter-training thead th:first-child,.chapter-training tbody th{width:105px}
.chapter-training thead th{height:105px;vertical-align:bottom;background:var(--color-bg-subtle)}
.chapter-training thead label{display:grid;gap:.3rem;justify-items:start}
.chapter-training thead label span{font-size:.76rem;line-height:1.25}
.chapter-training tbody th{text-align:left}
.chapter-training tbody th strong,.chapter-training tbody th span{display:block}
.chapter-training tbody th span{color:var(--color-text-secondary);font-size:.72rem}
.chapter-training td button{width:100%;min-height:2.1rem;border:1px solid transparent;border-radius:calc(var(--radius) - 2px);font-weight:750;cursor:pointer}
.is-low{background:var(--color-danger-subtle);color:var(--color-danger)}
.is-mid{background:var(--color-warning-subtle);color:var(--color-warning)}
.is-good{background:var(--color-success-subtle);color:var(--color-success)}
.is-empty{background:repeating-linear-gradient(135deg,var(--color-border-subtle) 0 5px,var(--color-bg-subtle) 5px 10px);color:var(--color-text-muted)}
.chapter-training td button.is-selected{border-color:var(--color-accent);box-shadow:0 0 0 2px var(--color-accent-subtle)}
.chapter-training__main footer{display:flex;flex-wrap:wrap;gap:.45rem;padding-top:.7rem}
.chapter-training__main footer span{padding:.2rem .45rem;border-radius:999px;background:var(--color-bg-subtle);font-size:.72rem}
.chapter-training__groups{border:1px solid var(--color-border-default);border-radius:var(--radius-control)}
.chapter-training__groups>div{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.6rem;align-items:center;padding:0 .8rem;border-bottom:1px solid var(--color-border-default);background:var(--color-bg-surface)}
.chapter-training__groups>div.is-selected{background:var(--color-accent-subtle);box-shadow:inset 3px 0 var(--color-accent)}
.chapter-training__groups>div>button{display:grid;grid-template-columns:minmax(110px,160px) minmax(150px,1fr) 48px 82px 78px;gap:.6rem;align-items:center;width:100%;padding:.85rem 0;border:0;background:transparent;text-align:left;cursor:pointer}
.chapter-training__groups small{color:var(--color-text-secondary);text-align:right}
.chapter-training__bar{height:.7rem;overflow:hidden;border-radius:999px;background:var(--color-bg-selected)}
.chapter-training__bar i{display:block;height:100%}
.chapter-training__group-note{margin:0;padding:.75rem;color:var(--color-text-secondary);font-size:.78rem}
.chapter-training__detail{padding:1rem;border-left:1px solid var(--color-border-default);background:var(--color-bg-subtle)}
.chapter-training__detail>span{color:var(--color-text-secondary);font-size:.75rem}
.chapter-training__detail h2{margin:.2rem 0 .8rem;font-size:1.2rem}
.chapter-training__detail p{color:var(--color-text-secondary)}
.chapter-training__score{margin:1rem 0;padding:.8rem;border-radius:var(--radius-control);font-size:1.8rem;font-weight:800}
.chapter-training__detail dl{margin:0}
.chapter-training__detail dl div{display:flex;justify-content:space-between;padding:.45rem 0;border-bottom:1px solid var(--color-border-default)}
.chapter-training__detail>button{width:100%;margin:.8rem 0;padding:.6rem;border:0;border-radius:var(--radius-control);background:var(--color-accent);color:var(--primary-foreground);cursor:pointer}
.chapter-training__detail>button:hover{background:var(--color-accent-hover)}
.chapter-training__evidence-link{display:block;box-sizing:border-box;width:100%;margin:.8rem 0;padding:.6rem;border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);color:var(--color-accent-active);text-align:center;text-decoration:none}
.chapter-training__detail section{margin-top:1rem;padding-top:1rem;border-top:1px solid var(--color-border-default)}
.chapter-training__detail ul{max-height:180px;overflow:auto;padding-left:1.1rem}
@media(max-width:1180px){.chapter-training__body{grid-template-columns:195px minmax(0,1fr)}.chapter-training__detail{grid-column:1/-1;border-top:1px solid var(--color-border-default);border-left:0}.chapter-training__groups>div>button{grid-template-columns:minmax(110px,150px) minmax(140px,1fr) 48px 76px 72px}}
@media(max-width:760px){.chapter-training__body,.chapter-training__body.is-recommending{grid-template-columns:minmax(0,1fr)}.chapter-training__toolbar,.chapter-training__toolbar>div:first-child{align-items:flex-start;flex-direction:column}.chapter-training__scope{border-right:0;border-bottom:1px solid var(--color-border-default)}.chapter-training__modes{flex-wrap:wrap}.chapter-training__modes button{white-space:nowrap}}
</style>
