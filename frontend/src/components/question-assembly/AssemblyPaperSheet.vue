<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import type { AssemblyQuestion } from '../../api/assembly'
import { useAssemblyStore } from '../../stores/assembly'
import StatePanel from '../design-system/StatePanel.vue'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import {
  SHEET_CHUNK_SIZE,
  analysisBlocks,
  analysisFallback,
  answerSpaceLines,
  canonicalTypeGroup,
  extractPrintedAnswer,
  stripLeadingPrint,
  stripStemBlocks,
  useSheetSections,
  type SheetSection,
} from './assembly-sheet'

const props = defineProps<{ variant: 'page' | 'drawer'; swap?: boolean; showMeta?: boolean }>()
const emit = defineEmits<{ replace: [id: number] }>()

const assembly = useAssemblyStore()
const { sheetSections } = useSheetSections()
const wrapRef = ref<HTMLElement | null>(null)

// 显示题目信息：抽屉默认开，整页默认关；由局部勾选切换。
const metaOn = ref(props.showMeta ?? props.variant === 'drawer')

// 教师版答案：默认收起为两行，可逐题展开或用分段器全部展开。
const answerMode = ref<'collapsed' | 'expanded'>('collapsed')
const answerOverrides = ref(new Map<number, boolean>())
function answerOpen(questionId: number): boolean {
  return answerOverrides.value.get(questionId) ?? answerMode.value === 'expanded'
}
function toggleAnswer(questionId: number): void {
  const next = new Map(answerOverrides.value)
  next.set(questionId, !answerOpen(questionId))
  answerOverrides.value = next
}
function setAnswerMode(mode: 'collapsed' | 'expanded'): void {
  answerMode.value = mode
  answerOverrides.value = new Map()
}
function setPreviewMode(mode: 'student' | 'teacher'): void {
  void assembly.updateSettings({ preview_mode: mode })
}

// 题号与轨道按卷面显示顺序编号（与导出的卷面阅读顺序一致）。
const displayQuestions = computed(() => sheetSections.value.flatMap((section) => section.questions))
const displayIndexById = computed(() => {
  const map = new Map<number, number>()
  displayQuestions.value.forEach((question, index) => map.set(question.id, index + 1))
  return map
})

const sources = computed(() => assembly.draft.assembly_context?.sources ?? {})
const violations = computed(() => assembly.draft.rule_violations ?? [])
function violationsFor(questionId: number) {
  return violations.value.filter((violation) => violation.question_id === questionId)
}
function metaLine(question: AssemblyQuestion): string {
  const source = sources.value[question.id]
  const origin = source ? (source.original ? '原题 ' : '补 ') + source.label : '手动选题'
  return `${question.question_type || '未分类'} · 难度 ${question.difficulty ?? '待定'} · ${origin}`
}
function printedAnswer(question: AssemblyQuestion): string | null {
  return extractPrintedAnswer(question.answer_text ?? '')
}
function answerBlocksFor(question: AssemblyQuestion) {
  return analysisBlocks(question.rich_content?.answer_blocks)
}
function answerFallbackFor(question: AssemblyQuestion): string {
  return analysisFallback(question.answer_text)
}

// ↑/↓：按显示顺序移动；题型归组时只在同组相邻题之间移动。
function canMoveDisplay(question: AssemblyQuestion, direction: -1 | 1): boolean {
  if (assembly.draft.layout_mode === 'grouped_by_type') {
    const group = sheetSections.value.find(
      (section) => section.questions.some((item) => item.id === question.id),
    )
    const index = group?.questions.findIndex((item) => item.id === question.id) ?? -1
    return index >= 0 && index + direction >= 0 && index + direction < (group?.questions.length ?? 0)
  }
  const index = displayIndexById.value.get(question.id) ?? 0
  return direction === -1 ? index > 1 : index < displayIndexById.value.size
}
function moveDisplay(question: AssemblyQuestion, direction: -1 | 1): void {
  if (assembly.draft.layout_mode === 'grouped_by_type') {
    const group = sheetSections.value.find(
      (section) => section.questions.some((item) => item.id === question.id),
    )
    const index = group?.questions.findIndex((item) => item.id === question.id) ?? -1
    const neighbor = index >= 0 ? group?.questions[index + direction] : undefined
    if (!neighbor) return
    void assembly.moveQuestionToSlot(
      question.id,
      currentSectionId(question.id),
      neighbor.id,
      direction === -1 ? 'before' : 'after',
    )
    return
  }
  void assembly.moveQuestion(question.id, direction)
}

const visibleCounts = reactive(new Map<string, number>())
function visibleCount(sectionId: string): number {
  return visibleCounts.get(sectionId) ?? SHEET_CHUNK_SIZE
}
function expandSection(sectionId: string): void {
  visibleCounts.set(sectionId, visibleCount(sectionId) + SHEET_CHUNK_SIZE)
}

function chipClass(question: AssemblyQuestion): string {
  const group = canonicalTypeGroup(question.question_type)
  return group === '选择题' ? 'aps-chip--c' : group === '填空题' ? 'aps-chip--f' : 'aps-chip--s'
}

// ---- 拖拽排序：卷面按 clientY 中点给前/后指示，题号轨道用同一套规则 ----
const draggedId = ref<number | null>(null)
const dropTarget = ref<{ sectionId: string; anchorId: number | null; position: 'before' | 'after' } | null>(null)
const railDropTarget = ref<{ anchorId: number; position: 'before' | 'after' } | null>(null)

function currentSectionId(questionId: number): string {
  const section = assembly.draft.sections.find(
    (item) => item.id !== 'unassigned' && item.question_ids.includes(questionId),
  )
  return section?.id ?? ''
}

function sectionIdForSheetDrop(group: SheetSection, dragged: number): string {
  // 手动分节：落到哪个节就属于哪个节；其他排版保留题目当前分节，不悄悄摘掉。
  if (assembly.draft.layout_mode === 'sections') return group.id === 'unassigned' ? '' : group.id
  return currentSectionId(dragged)
}

function sameTypeGroup(anchorId: number): boolean {
  if (draggedId.value === null) return false
  const dragged = assembly.questionMap.get(draggedId.value)
  const anchor = assembly.questionMap.get(anchorId)
  return canonicalTypeGroup(dragged?.question_type) === canonicalTypeGroup(anchor?.question_type)
}

function startDrag(questionId: number): void {
  draggedId.value = questionId
}
function endDrag(): void {
  draggedId.value = null
  dropTarget.value = null
  railDropTarget.value = null
}

function dragOverQuestion(event: DragEvent, group: SheetSection, anchorId: number): void {
  if (draggedId.value === null || draggedId.value === anchorId
    || (assembly.draft.layout_mode === 'grouped_by_type' && !sameTypeGroup(anchorId))) {
    dropTarget.value = null
    return
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  dropTarget.value = {
    sectionId: sectionIdForSheetDrop(group, draggedId.value),
    anchorId,
    position: event.clientY < rect.top + rect.height / 2 ? 'before' : 'after',
  }
}

function dragOverSectionEnd(group: SheetSection): void {
  if (draggedId.value === null || assembly.draft.layout_mode === 'grouped_by_type') {
    dropTarget.value = null
    return
  }
  const last = group.questions[group.questions.length - 1]
  dropTarget.value = {
    sectionId: sectionIdForSheetDrop(group, draggedId.value),
    anchorId: last && last.id !== draggedId.value ? last.id : null,
    position: 'after',
  }
}

function dropOnSheet(): void {
  const dragged = draggedId.value
  const target = dropTarget.value
  endDrag()
  if (dragged === null || target === null) return
  void assembly.moveQuestionToSlot(dragged, target.sectionId, target.anchorId, target.position)
}

function dragOverRail(event: DragEvent, anchorId: number): void {
  if (draggedId.value === null || draggedId.value === anchorId
    || (assembly.draft.layout_mode === 'grouped_by_type' && !sameTypeGroup(anchorId))) {
    railDropTarget.value = null
    return
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  railDropTarget.value = {
    anchorId,
    position: event.clientY < rect.top + rect.height / 2 ? 'before' : 'after',
  }
}

function dropOnRail(): void {
  const dragged = draggedId.value
  const target = railDropTarget.value
  endDrag()
  if (dragged === null || target === null) return
  const sectionId = assembly.draft.layout_mode === 'sections'
    ? currentSectionId(target.anchorId)
    : currentSectionId(dragged)
  void assembly.moveQuestionToSlot(dragged, sectionId, target.anchorId, target.position)
}

// ---- 题号轨道：点击定位、视口内题目高亮 ----
const inViewId = ref<number | null>(null)
let observer: IntersectionObserver | null = null

function scrollParent(el: HTMLElement): Element | null {
  for (let parent = el.parentElement; parent; parent = parent.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(parent).overflowY)) return parent
  }
  return null
}

function scrollToQuestion(questionId: number): void {
  wrapRef.value
    ?.querySelector(`.aps-q[data-qid="${questionId}"]`)
    ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

async function observeQuestions(): Promise<void> {
  observer?.disconnect()
  observer = null
  await nextTick()
  const wrap = wrapRef.value
  if (!wrap || typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) inViewId.value = Number((entry.target as HTMLElement).dataset.qid)
    }
  }, { root: scrollParent(wrap), threshold: 0.4 })
  wrap.querySelectorAll('.aps-q').forEach((el) => observer!.observe(el))
}

onMounted(observeQuestions)
watch(
  () => [assembly.draft.order_ids, assembly.draft.layout_mode, assembly.draft.sections],
  observeQuestions,
  { deep: true },
)
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div ref="wrapRef" class="aps" :class="`aps--${variant}`">
    <div class="aps-modes">
      <div class="app-segmented" role="group" aria-label="预览版本">
        <button type="button" :aria-pressed="assembly.draft.preview_mode !== 'teacher'" @click="setPreviewMode('student')">学生版</button>
        <button type="button" :aria-pressed="assembly.draft.preview_mode === 'teacher'" @click="setPreviewMode('teacher')">教师版</button>
      </div>
      <div v-if="assembly.draft.preview_mode === 'teacher'" class="app-segmented" role="group" aria-label="答案展开方式">
        <button type="button" :aria-pressed="answerMode === 'collapsed'" @click="setAnswerMode('collapsed')">答案收起</button>
        <button type="button" :aria-pressed="answerMode === 'expanded'" @click="setAnswerMode('expanded')">全部展开</button>
      </div>
      <label class="aps-meta-toggle"><input v-model="metaOn" type="checkbox">显示题目信息</label>
    </div>
    <div class="aps-wrap">
      <nav class="aps-rail" aria-label="题号导航">
        <button
          v-for="question in displayQuestions"
          :key="question.id"
          type="button"
          class="aps-chip"
          :class="[chipClass(question), {
            'is-current': inViewId === question.id,
            'is-drop-before': railDropTarget?.anchorId === question.id && railDropTarget.position === 'before',
            'is-drop-after': railDropTarget?.anchorId === question.id && railDropTarget.position === 'after',
          }]"
          draggable="true"
          :title="`第 ${displayIndexById.get(question.id)} 题 · ${question.question_type || '未分类'}`"
          @click="scrollToQuestion(question.id)"
          @dragstart="startDrag(question.id)"
          @dragend="endDrag"
          @dragover.prevent.stop="dragOverRail($event, question.id)"
          @drop.prevent.stop="dropOnRail()"
        >{{ displayIndexById.get(question.id) }}</button>
      </nav>
      <article class="assembly-sheet" :class="`assembly-sheet--${variant}`">
        <header v-if="variant === 'page'">
          <h2>{{ assembly.draft.title || '未命名试卷' }}</h2>
          <p>{{ assembly.draft.header_text || '姓名：__________　班级：__________　日期：__________' }}</p>
        </header>
        <section
          v-for="section in sheetSections"
          :key="section.id"
          class="aps-section"
          :class="{ 'is-drop-end': dropTarget?.sectionId === section.id && dropTarget.anchorId === null }"
          @dragover.prevent="dragOverSectionEnd(section)"
          @drop.prevent="dropOnSheet()"
        >
          <h3 v-if="section.title && section.id !== 'all'">{{ section.title }}</h3>
          <div
            v-for="question in section.questions.slice(0, visibleCount(section.id))"
            :key="question.id"
            class="aps-q"
            :data-qid="question.id"
            :class="{
              'is-drag-source': draggedId === question.id,
              'is-drop-before': dropTarget?.anchorId === question.id && dropTarget.position === 'before',
              'is-drop-after': dropTarget?.anchorId === question.id && dropTarget.position === 'after',
            }"
            draggable="true"
            @dragstart="startDrag(question.id)"
            @dragend="endDrag"
            @dragover.prevent.stop="dragOverQuestion($event, section, question.id)"
            @drop.prevent.stop="dropOnSheet()"
          >
            <span class="aps-q__num">{{ displayIndexById.get(question.id) }}.</span>
            <span class="aps-q__handle" aria-hidden="true">⋮⋮</span>
            <div class="aps-tools">
              <button
                type="button"
                :aria-label="`上移第 ${displayIndexById.get(question.id)} 题`"
                :disabled="!canMoveDisplay(question, -1)"
                @click="moveDisplay(question, -1)"
              >↑</button>
              <button
                type="button"
                :aria-label="`下移第 ${displayIndexById.get(question.id)} 题`"
                :disabled="!canMoveDisplay(question, 1)"
                @click="moveDisplay(question, 1)"
              >↓</button>
              <button v-if="swap" type="button" @click="emit('replace', question.id)">换一题</button>
              <button type="button" @click="assembly.removeQuestion(question.id)">移出</button>
            </div>
            <div class="aps-q__stem">
              <QuestionContentRenderer
                :blocks="stripStemBlocks(question.rich_content?.question_blocks)"
                :fallback="stripLeadingPrint(question.question_text ?? '')"
                image-alt="试卷题目配图"
                media-mode="paper"
                paper-media-flow
                :compact-media-with-text="answerSpaceLines(question.question_type) === 0"
                dense
              />
            </div>
            <p v-if="metaOn" class="aps-meta">{{ metaLine(question) }}</p>
            <p
              v-for="violation in violationsFor(question.id)"
              :key="violation.code"
              class="aps-violation"
            >超出：{{ violation.message }}</p>
            <div
              v-if="variant === 'page' && answerSpaceLines(question.question_type) > 0"
              class="assembly-sheet__answer-space"
              aria-hidden="true"
            ></div>
            <div v-if="assembly.draft.preview_mode === 'teacher'" class="assembly-sheet__answer aps-answer">
              <template v-if="!answerOpen(question.id) && printedAnswer(question)">
                <div class="aps-answer__short">
                  <strong>答案：</strong>
                  <QuestionContentRenderer :fallback="printedAnswer(question)!" typeset-text dense />
                </div>
                <button type="button" class="aps-answer__toggle" @click="toggleAnswer(question.id)">看解析</button>
              </template>
              <template v-else>
                <strong>答案与解析</strong>
                <div class="aps-answer__body" :class="{ 'is-clamped': !answerOpen(question.id) }">
                  <QuestionContentRenderer
                    :blocks="answerBlocksFor(question)"
                    :fallback="answerFallbackFor(question)"
                    empty-label="暂未录入答案"
                    image-alt="试卷答案配图"
                    media-mode="paper"
                    dense
                  />
                </div>
                <button type="button" class="aps-answer__toggle" @click="toggleAnswer(question.id)">{{ answerOpen(question.id) ? '收起答案' : '展开' }}</button>
              </template>
            </div>
          </div>
          <p v-if="!section.questions.length" class="aps-empty">拖到这里</p>
          <button
            v-if="section.questions.length > visibleCount(section.id)"
            type="button"
            class="assembly-sheet__more"
            @click="expandSection(section.id)"
          >加载更多题目（还有 {{ section.questions.length - visibleCount(section.id) }} 题）</button>
        </section>
        <StatePanel v-if="!assembly.orderedQuestions.length" kind="empty" title="试卷篮为空" description="去题库选题。" />
      </article>
    </div>
  </div>
</template>
