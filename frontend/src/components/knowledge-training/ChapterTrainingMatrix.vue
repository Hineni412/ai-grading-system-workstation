<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { TrainingDiagnosis, TrainingWeakPoint } from '../../api/training'

const props = defineProps<{
  diagnosis: TrainingDiagnosis
  modelValue: string[]
  groupScopeLabel: string
}>()
const emit = defineEmits<{
  'update:modelValue': [keys: string[]]
  'adjust-scope': []
}>()

const viewMode = ref<'student' | 'group'>('student')
const activeChapterKey = ref('')
const activeSectionKey = ref('')
const selectedCell = ref<{ studentId: string; knowledgeKey: string } | null>(null)
const selectedGroupKey = ref('')

const catalog = computed(() => props.diagnosis.knowledge_catalog ?? [])
const chapters = computed(() => catalog.value.filter((item) => !item.parent_knowledge_key))
const sections = computed(() => catalog.value.filter((item) => item.parent_knowledge_key === activeChapterKey.value))
const points = computed(() => {
  const direct = catalog.value.filter((item) => item.parent_knowledge_key === activeSectionKey.value)
  if (direct.length) return direct
  return activeSectionKey.value
    ? catalog.value.filter((item) => item.knowledge_key === activeSectionKey.value)
    : []
})
const groupMap = computed(() => new Map((props.diagnosis.group_weak_points ?? []).map((item) => [
  item.knowledge_key,
  item,
])))

watch(chapters, (items) => {
  if (!items.some((item) => item.knowledge_key === activeChapterKey.value)) {
    activeChapterKey.value = items[0]?.knowledge_key ?? ''
  }
}, { immediate: true })
watch(sections, (items) => {
  if (!items.some((item) => item.knowledge_key === activeSectionKey.value)) {
    activeSectionKey.value = items[0]?.knowledge_key ?? ''
  }
}, { immediate: true })
watch(points, (items) => {
  if (!items.some((item) => item.knowledge_key === selectedGroupKey.value)) {
    selectedGroupKey.value = ''
  }
})

function weakFor(studentId: string, knowledgeKey: string): TrainingWeakPoint | null {
  return props.diagnosis.students.find((student) => student.student_id === studentId)
    ?.weak_points.find((point) => point.knowledge_key === knowledgeKey) ?? null
}

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`
}

function heatClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'is-empty'
  if (value < .6) return 'is-low'
  if (value < .75) return 'is-mid'
  return 'is-good'
}

function toggleTarget(key: string): void {
  emit('update:modelValue', props.modelValue.includes(key)
    ? props.modelValue.filter((item) => item !== key)
    : [...props.modelValue, key])
}

const selectedWeak = computed(() => selectedCell.value
  ? weakFor(selectedCell.value.studentId, selectedCell.value.knowledgeKey)
  : null)
const selectedStudent = computed(() => props.diagnosis.students.find(
  (student) => student.student_id === selectedCell.value?.studentId,
) ?? null)
const selectedGroup = computed(() => selectedGroupKey.value
  ? groupMap.value.get(selectedGroupKey.value) ?? null
  : null)
const groupRows = computed(() => points.value.map((point) => {
  const aggregate = groupMap.value.get(point.knowledge_key) ?? null
  const contributorCount = props.diagnosis.students.filter((student) => (
    (weakFor(student.student_id, point.knowledge_key)?.evidence_count ?? 0) > 0
  )).length
  return {
    key: point.knowledge_key,
    label: point.knowledge_point,
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
        <span>同一小节可查看个人明细，也可查看当前范围的群体加权结果</span>
      </div>
      <div class="chapter-training__modes" aria-label="章节训练展示方式">
        <button type="button" :class="{ 'is-active': viewMode === 'student' }" @click="viewMode = 'student'">学生明细</button>
        <button type="button" :class="{ 'is-active': viewMode === 'group' }" @click="viewMode = 'group'">群体加权</button>
      </div>
    </header>

    <div class="chapter-training__body">
      <aside class="chapter-training__scope">
        <header><strong>章节与小节</strong><span>一次只看 6 个知识点</span></header>
        <button
          v-for="chapter in chapters"
          :key="chapter.knowledge_key"
          type="button"
          :class="{ 'is-active': chapter.knowledge_key === activeChapterKey }"
          @click="activeChapterKey = chapter.knowledge_key"
        >
          <span>{{ chapter.knowledge_point }}</span>
          <b>{{ percent(groupMap.get(chapter.knowledge_key)?.mastery) }}</b>
        </button>
        <div class="chapter-training__sections">
          <button
            v-for="section in sections"
            :key="section.knowledge_key"
            type="button"
            :class="{ 'is-active': section.knowledge_key === activeSectionKey }"
            @click="activeSectionKey = section.knowledge_key"
          >
            {{ section.knowledge_point }}
          </button>
        </div>
      </aside>

      <main class="chapter-training__main">
        <header>
          <div>
            <span>{{ sections.find((item) => item.knowledge_key === activeSectionKey)?.knowledge_point }}</span>
            <h2>{{ viewMode === 'student' ? '学生 × 知识点' : '群体加权 × 知识点' }}</h2>
          </div>
          <strong v-if="viewMode === 'student'">{{ diagnosis.students.length }} 名学生 · {{ points.length }} 个知识点</strong>
          <div v-else class="chapter-training__group-scope">
            <span>统计群体</span>
            <b>{{ groupScopeLabel }}</b>
            <button type="button" @click="emit('adjust-scope')">调整</button>
          </div>
        </header>

        <template v-if="viewMode === 'student'">
          <div class="chapter-training__scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">学生</th>
                  <th v-for="point in points" :key="point.knowledge_key" scope="col">
                    <label>
                      <input type="checkbox" :checked="modelValue.includes(point.knowledge_key)" @change="toggleTarget(point.knowledge_key)">
                      <span>{{ point.knowledge_point }}</span>
                      <b>{{ percent(groupMap.get(point.knowledge_key)?.mastery) }}</b>
                    </label>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="student in diagnosis.students" :key="student.student_id">
                  <th scope="row"><strong>{{ student.student_name }}</strong><span>{{ student.student_code }}</span></th>
                  <td v-for="point in points" :key="point.knowledge_key">
                    <button
                      type="button"
                      :class="[heatClass(weakFor(student.student_id, point.knowledge_key)?.mastery), { 'is-selected': selectedCell?.studentId === student.student_id && selectedCell?.knowledgeKey === point.knowledge_key }]"
                      @click="selectedCell = { studentId: student.student_id, knowledgeKey: point.knowledge_key }"
                    >
                      {{ percent(weakFor(student.student_id, point.knowledge_key)?.mastery) }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <footer><span>待补强 &lt;60%</span><span>需巩固 60–74%</span><span>较稳定 ≥75%</span><span>斜纹为无证据</span></footer>
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
            <button type="button" @click="selectedGroupKey = row.key">
              <strong>{{ row.label }}</strong>
              <span class="chapter-training__bar"><i :class="heatClass(row.aggregate?.mastery)" :style="{ width: `${(row.aggregate?.mastery ?? 0) * 100}%` }"></i></span>
              <b>{{ percent(row.aggregate?.mastery) }}</b>
              <small>{{ row.contributorCount }} / {{ diagnosis.students.length }} 人有效</small>
              <small>{{ row.aggregate?.evidence_count ?? 0 }} 条证据</small>
            </button>
          </div>
          <p class="chapter-training__group-note">群体掌握度直接使用后端证据加权结果；无证据学生不进入分母，也不按 0 分计算。</p>
        </div>
      </main>

      <aside class="chapter-training__detail">
        <template v-if="viewMode === 'student' && selectedWeak && selectedStudent">
          <span>当前学生</span>
          <h2>{{ selectedStudent.student_name }}</h2>
          <strong>{{ selectedWeak.knowledge_point }}</strong>
          <div :class="['chapter-training__score', heatClass(selectedWeak.mastery)]">{{ percent(selectedWeak.mastery) }}</div>
          <dl><div><dt>证据</dt><dd>{{ selectedWeak.evidence_count }} 条</dd></div><div><dt>考试</dt><dd>{{ selectedWeak.exam_count }} 场</dd></div></dl>
          <button type="button" @click="toggleTarget(selectedWeak.knowledge_key)">{{ modelValue.includes(selectedWeak.knowledge_key) ? '移出训练目标' : '加入训练目标' }}</button>
        </template>
        <template v-else-if="viewMode === 'group' && selectedGroup">
          <span>当前群体</span>
          <h2>{{ selectedGroup.knowledge_point }}</h2>
          <div :class="['chapter-training__score', heatClass(selectedGroup.mastery)]">{{ percent(selectedGroup.mastery) }}</div>
          <dl><div><dt>有效证据</dt><dd>{{ selectedGroup.evidence_count }} 条</dd></div><div><dt>有效权重</dt><dd>{{ (selectedGroup.effective_weight ?? 0).toFixed(1) }}</dd></div></dl>
          <button type="button" @click="toggleTarget(selectedGroup.knowledge_key)">{{ modelValue.includes(selectedGroup.knowledge_key) ? '移出训练目标' : '加入训练目标' }}</button>
        </template>
        <template v-else>
          <span>当前小节</span>
          <h2>{{ viewMode === 'student' ? '选择一个掌握度格子' : '选择一行群体结果' }}</h2>
          <p>{{ viewMode === 'student' ? '查看某名学生在具体知识点上的掌握情况。' : '查看当前筛选、某个班级或全部班级的证据加权结果。' }}</p>
        </template>
        <section><strong>本次已选 {{ modelValue.length }} 项</strong><p v-if="!modelValue.length">尚未选择训练知识点。</p><ul v-else><li v-for="key in modelValue" :key="key">{{ catalog.find((item) => item.knowledge_key === key)?.knowledge_point ?? key }}</li></ul></section>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.chapter-training{overflow:hidden;border:1px solid var(--color-border-default);border-radius:14px;background:white}.chapter-training__toolbar{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:.75rem 1rem;border-bottom:1px solid var(--color-border-default)}.chapter-training__toolbar>div:first-child{display:flex;gap:.8rem;align-items:baseline}.chapter-training__toolbar span{color:var(--color-text-secondary);font-size:.78rem}.chapter-training__modes{display:flex;padding:.2rem;border-radius:9px;background:#eef2f2}.chapter-training__modes button{padding:.45rem .75rem;border:0;border-radius:7px;background:transparent;color:var(--color-text-secondary);cursor:pointer}.chapter-training__modes button.is-active{background:white;color:var(--color-accent-active);font-weight:700;box-shadow:0 2px 7px rgb(24 53 61 / 10%)}
.chapter-training__body{display:grid;grid-template-columns:220px minmax(560px,1fr) 230px;min-height:560px}.chapter-training__scope{padding:.8rem;border-right:1px solid var(--color-border-default);background:#f5f7f7}.chapter-training__scope header{display:grid;gap:.15rem;padding:.35rem .4rem .75rem}.chapter-training__scope header span{color:var(--color-text-secondary);font-size:.76rem}.chapter-training__scope>button{display:flex;justify-content:space-between;gap:.5rem;width:100%;margin-bottom:.3rem;padding:.6rem;border:1px solid transparent;border-radius:8px;background:transparent;text-align:left;cursor:pointer}.chapter-training__scope>button.is-active{border-color:var(--color-accent);background:white;box-shadow:inset 3px 0 var(--color-accent)}.chapter-training__sections{display:grid;gap:.25rem;margin:.65rem 0 0 .65rem;padding-left:.65rem;border-left:2px solid var(--color-border-default)}.chapter-training__sections button{padding:.45rem .55rem;border:0;border-radius:7px;background:transparent;color:var(--color-text-secondary);text-align:left;cursor:pointer}.chapter-training__sections button.is-active{background:var(--color-accent-subtle);color:var(--color-accent-active);font-weight:700}
.chapter-training__main{min-width:0;padding:1rem}.chapter-training__main>header{display:flex;justify-content:space-between;align-items:end;gap:1rem;margin-bottom:.8rem}.chapter-training__main h2{margin:.15rem 0 0;font-size:1.2rem}.chapter-training__main header span{color:var(--color-text-secondary);font-size:.76rem}.chapter-training__group-scope{display:flex;align-items:center;gap:.5rem}.chapter-training__group-scope button{padding:.35rem .55rem;border:1px solid var(--color-border-default);border-radius:7px;background:white;color:var(--color-accent-active);cursor:pointer}.chapter-training__scroll{overflow:auto;border:1px solid var(--color-border-default);border-radius:10px}.chapter-training table{width:100%;min-width:740px;border-collapse:collapse;table-layout:fixed}.chapter-training th,.chapter-training td{padding:.38rem;border-right:1px solid var(--color-border-default);border-bottom:1px solid var(--color-border-default)}.chapter-training thead th:first-child,.chapter-training tbody th{width:105px}.chapter-training thead th{height:105px;vertical-align:bottom;background:#f7f9f9}.chapter-training thead label{display:grid;gap:.3rem;justify-items:start}.chapter-training thead label span{font-size:.76rem;line-height:1.25}.chapter-training tbody th{text-align:left}.chapter-training tbody th strong,.chapter-training tbody th span{display:block}.chapter-training tbody th span{color:var(--color-text-secondary);font-size:.72rem}.chapter-training td button{width:100%;min-height:2.1rem;border:1px solid transparent;border-radius:6px;font-weight:750;cursor:pointer}.is-low{background:#f3c7c4}.is-mid{background:#f5dda7}.is-good{background:#bfe2dc}.is-empty{background:repeating-linear-gradient(135deg,#e9eded 0 5px,#f9fafa 5px 10px)}.chapter-training td button.is-selected{border-color:var(--color-accent);box-shadow:0 0 0 2px var(--color-accent-subtle)}.chapter-training__main footer{display:flex;flex-wrap:wrap;gap:.45rem;padding-top:.7rem}.chapter-training__main footer span{padding:.2rem .45rem;border-radius:999px;background:#eef1f2;font-size:.72rem}
.chapter-training__groups{border:1px solid var(--color-border-default);border-radius:10px}.chapter-training__groups>div{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.6rem;align-items:center;padding:0 .8rem;border-bottom:1px solid var(--color-border-default);background:white}.chapter-training__groups>div.is-selected{background:var(--color-accent-subtle);box-shadow:inset 3px 0 var(--color-accent)}.chapter-training__groups>div>button{display:grid;grid-template-columns:minmax(110px,160px) minmax(150px,1fr) 48px 82px 78px;gap:.6rem;align-items:center;width:100%;padding:.85rem 0;border:0;background:transparent;text-align:left;cursor:pointer}.chapter-training__groups small{color:var(--color-text-secondary);text-align:right}.chapter-training__bar{height:.7rem;overflow:hidden;border-radius:999px;background:#e7ecec}.chapter-training__bar i{display:block;height:100%}.chapter-training__group-note{margin:0;padding:.75rem;color:var(--color-text-secondary);font-size:.78rem}
.chapter-training__detail{padding:1rem;border-left:1px solid var(--color-border-default);background:#fbfcfc}.chapter-training__detail>span{color:var(--color-text-secondary);font-size:.75rem}.chapter-training__detail h2{margin:.2rem 0 .8rem;font-size:1.2rem}.chapter-training__detail p{color:var(--color-text-secondary)}.chapter-training__score{margin:1rem 0;padding:.8rem;border-radius:9px;font-size:1.8rem;font-weight:800}.chapter-training__detail dl{margin:0}.chapter-training__detail dl div{display:flex;justify-content:space-between;padding:.45rem 0;border-bottom:1px solid var(--color-border-default)}.chapter-training__detail>button{width:100%;margin:.8rem 0;padding:.6rem;border:0;border-radius:8px;background:var(--color-accent);color:white;cursor:pointer}.chapter-training__detail section{margin-top:1rem;padding-top:1rem;border-top:1px solid var(--color-border-default)}.chapter-training__detail ul{max-height:180px;overflow:auto;padding-left:1.1rem}
@media(max-width:1180px){.chapter-training__body{grid-template-columns:195px minmax(0,1fr)}.chapter-training__detail{grid-column:1/-1;border-top:1px solid var(--color-border-default);border-left:0}.chapter-training__groups>div>button{grid-template-columns:minmax(110px,150px) minmax(140px,1fr) 48px 76px 72px}}
</style>
