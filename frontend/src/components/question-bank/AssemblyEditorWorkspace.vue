<script setup lang="ts">
import { computed, ref } from 'vue'

import type { AssemblyQuestion, AssemblySection } from '../../api/assembly'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'
import { useAssemblyStore } from '../../stores/assembly'
import { useJobStore } from '../../stores/jobs'
import QuestionContentRenderer from './QuestionContentRenderer.vue'

const emit = defineEmits<{
  browse: []
}>()

const assembly = useAssemblyStore()
const jobs = useJobStore()
const draggedQuestionId = ref<number | null>(null)

const currentJob = computed(() => (
  assembly.exportJobId === null ? null : jobs.jobs[assembly.exportJobId] ?? null
))
const currentJobDone = computed(() => (
  currentJob.value !== null && TERMINAL_JOB_STATUSES.has(currentJob.value.status)
))
const editableSections = computed(() => assembly.draft.sections.filter(
  (section) => section.id !== 'unassigned',
))
const assignedQuestionIds = computed(() => new Set(
  editableSections.value.flatMap((section) => section.question_ids),
))

function previewSections(): Array<{ id: string; title: string; questions: AssemblyQuestion[] }> {
  if (assembly.draft.layout_mode === 'sections' && editableSections.value.length > 0) {
    const manualSections = editableSections.value.map((section) => ({
      id: section.id,
      title: section.title,
      questions: section.question_ids
        .map((id) => assembly.questionMap.get(id))
        .filter((item): item is AssemblyQuestion => item !== undefined),
    }))
    const remaining = assembly.orderedQuestions.filter(
      (question) => !assignedQuestionIds.value.has(question.id),
    )
    return remaining.length
      ? [{ id: 'unassigned', title: '未分节', questions: remaining }, ...manualSections]
      : manualSections
  }
  if (assembly.draft.layout_mode === 'grouped_by_type') {
    const groups = new Map<string, AssemblyQuestion[]>()
    for (const question of assembly.orderedQuestions) {
      const type = question.question_type || '未分类'
      groups.set(type, [...(groups.get(type) ?? []), question])
    }
    return [...groups.entries()].map(([title, questions], index) => ({
      id: `type-${index}`,
      title,
      questions,
    }))
  }
  return [{
    id: 'all',
    title: '试题',
    questions: assembly.orderedQuestions,
  }]
}

function addSection(): void {
  const section: AssemblySection = {
    id: `section-${Date.now().toString(36)}`,
    title: `分节 ${editableSections.value.length + 1}`,
    question_ids: [],
  }
  void assembly.replaceSections([...editableSections.value, section])
}

function renameSection(sectionId: string, title: string): void {
  const cleanTitle = title.trim()
  if (!cleanTitle) return
  void assembly.replaceSections(editableSections.value.map((section) => (
    section.id === sectionId ? { ...section, title: cleanTitle } : section
  )))
}

function removeSection(sectionId: string): void {
  void assembly.replaceSections(editableSections.value.filter((section) => section.id !== sectionId))
}

function sectionForQuestion(questionId: number): string {
  return editableSections.value.find((section) => section.question_ids.includes(questionId))?.id ?? ''
}

function assignQuestion(questionId: number, sectionId: string): void {
  const next = editableSections.value.map((section) => ({
    ...section,
    question_ids: section.question_ids.filter((id) => id !== questionId),
  }))
  if (sectionId) {
    const target = next.find((section) => section.id === sectionId)
    if (target) target.question_ids.push(questionId)
  }
  void assembly.replaceSections(next)
}

function startDrag(questionId: number): void {
  draggedQuestionId.value = questionId
}

function dropBefore(questionId: number): void {
  const dragged = draggedQuestionId.value
  draggedQuestionId.value = null
  if (dragged === null) return
  void assembly.moveQuestionBefore(dragged, questionId)
}

function scoreLabel(question: AssemblyQuestion): string {
  return question.score_value === null ? '未标分' : `${question.score_value} 分`
}

async function deleteRecord(recordId: string): Promise<void> {
  const confirmed = window.confirm('确认删除这条导出记录吗？已下载到其他位置的文件不受影响。')
  if (confirmed) await assembly.deleteRecord(recordId)
}
</script>

<template>
  <div class="assembly-editor">
    <header class="assembly-editor__toolbar">
      <button type="button" class="assembly-button" @click="emit('browse')">← 返回选题</button>
      <div>
        <strong>{{ assembly.draft.title || '未命名试卷' }}</strong>
        <span>{{ assembly.selectedQuestionCount }} 道题 · {{ assembly.totalScore }} 已识别分值</span>
      </div>
      <span>{{ assembly.saveState === 'saving' ? '正在保存…' : '草稿已保存在本机' }}</span>
    </header>

    <p v-if="assembly.message" class="assembly-feedback is-error" role="status">{{ assembly.message }}</p>

    <div class="assembly-editor__workspace">
      <aside class="assembly-editor-panel assembly-editor-panel--settings" aria-labelledby="assembly-settings-title">
        <header class="assembly-panel-heading">
          <div>
            <p class="assembly-kicker">PAPER SETTINGS</p>
            <h2 id="assembly-settings-title">试卷设置</h2>
          </div>
        </header>

        <div class="assembly-settings">
          <label>
            <span>试卷标题</span>
            <input
              :value="assembly.draft.title"
              maxlength="120"
              placeholder="如：九年级函数专项练习"
              @change="assembly.updateSettings({ title: ($event.target as HTMLInputElement).value })"
            >
          </label>
          <label>
            <span>页眉说明</span>
            <input
              :value="assembly.draft.header_text"
              maxlength="200"
              placeholder="如：限时 45 分钟"
              @change="assembly.updateSettings({ header_text: ($event.target as HTMLInputElement).value })"
            >
          </label>
          <label>
            <span>预览版本</span>
            <select
              :value="assembly.draft.preview_mode"
              @change="assembly.updateSettings({
                preview_mode: ($event.target as HTMLSelectElement).value as 'student' | 'teacher',
              })"
            >
              <option value="student">学生版</option>
              <option value="teacher">教师版</option>
            </select>
          </label>
          <label>
            <span>试题排版</span>
            <select
              :value="assembly.draft.layout_mode"
              @change="assembly.updateSettings({
                layout_mode: ($event.target as HTMLSelectElement).value as 'sequential' | 'grouped_by_type' | 'sections',
              })"
            >
              <option value="sequential">按当前顺序</option>
              <option value="grouped_by_type">按题型归组</option>
              <option value="sections">按手动分节</option>
            </select>
          </label>
          <label class="assembly-check">
            <input
              type="checkbox"
              :checked="assembly.draft.include_answer"
              @change="assembly.updateSettings({
                include_answer: ($event.target as HTMLInputElement).checked,
              })"
            >
            <span>导出时包含答案</span>
          </label>
        </div>

        <section class="assembly-section-editor">
          <header>
            <div>
              <strong>手动分节</strong>
              <small>用于按自定义大题组织试卷</small>
            </div>
            <button type="button" class="assembly-link" @click="addSection">添加分节</button>
          </header>
          <div v-if="editableSections.length" class="assembly-section-list">
            <div v-for="section in editableSections" :key="section.id">
              <input
                :value="section.title"
                maxlength="80"
                aria-label="分节名称"
                @change="renameSection(section.id, ($event.target as HTMLInputElement).value)"
              >
              <span>{{ section.question_ids.length }} 题</span>
              <button type="button" aria-label="删除分节" @click="removeSection(section.id)">×</button>
            </div>
          </div>
          <p v-else>需要自定义大题时再添加；当前不会制造空分节。</p>
        </section>

        <ol v-if="assembly.orderedQuestions.length" class="assembly-order-list">
          <li
            v-for="(question, index) in assembly.orderedQuestions"
            :key="question.id"
            draggable="true"
            @dragstart="startDrag(question.id)"
            @dragover.prevent
            @drop="dropBefore(question.id)"
          >
            <span class="assembly-order-list__handle" aria-hidden="true">⋮⋮</span>
            <span class="assembly-order-list__number">{{ index + 1 }}</span>
            <span class="assembly-order-list__copy">
              <strong>{{ question.question_text }}</strong>
              <small>{{ question.question_type || '未分类' }} · {{ scoreLabel(question) }}</small>
            </span>
            <select
              :value="sectionForQuestion(question.id)"
              aria-label="选择分节"
              @change="assignQuestion(question.id, ($event.target as HTMLSelectElement).value)"
            >
              <option value="">未分节</option>
              <option v-for="section in editableSections" :key="section.id" :value="section.id">{{ section.title }}</option>
            </select>
            <span class="assembly-order-list__actions">
              <button type="button" aria-label="上移" :disabled="index === 0" @click="assembly.moveQuestion(question.id, -1)">↑</button>
              <button type="button" aria-label="下移" :disabled="index === assembly.orderedQuestions.length - 1" @click="assembly.moveQuestion(question.id, 1)">↓</button>
              <button type="button" class="is-danger" aria-label="移出试卷" @click="assembly.removeQuestion(question.id)">×</button>
            </span>
          </li>
        </ol>
        <p v-else class="assembly-editor-empty">试卷篮为空，请返回选题。</p>
      </aside>

      <main class="assembly-paper" aria-labelledby="assembly-preview-title">
        <header class="assembly-paper__heading">
          <div>
            <p class="assembly-kicker">LIVE PREVIEW</p>
            <h2 id="assembly-preview-title">试卷预览</h2>
          </div>
          <span>{{ assembly.draft.preview_mode === 'teacher' ? '教师版' : '学生版' }}</span>
        </header>

        <article class="assembly-sheet">
          <header>
            <h2>{{ assembly.draft.title || '未命名试卷' }}</h2>
            <p>{{ assembly.draft.header_text || '姓名：__________　班级：__________　日期：__________' }}</p>
          </header>
          <section v-for="section in previewSections()" :key="section.id">
            <h3>{{ section.title }}</h3>
            <ol>
              <li v-for="question in section.questions" :key="question.id">
                <div class="assembly-sheet__question">
                  <span v-if="question.score_value !== null" class="assembly-sheet__score">
                    （{{ question.score_value }} 分）
                  </span>
                  <QuestionContentRenderer
                    :blocks="question.rich_content?.question_blocks"
                    :fallback="question.question_text"
                    image-alt="试卷题目配图"
                  />
                </div>
                <div
                  v-if="assembly.draft.preview_mode === 'teacher'"
                  class="assembly-sheet__answer"
                >
                  <strong>答案与解析</strong>
                  <QuestionContentRenderer
                    :blocks="question.rich_content?.answer_blocks"
                    :fallback="question.answer_text"
                    empty-label="暂未录入答案"
                    image-alt="试卷答案配图"
                    compact
                  />
                </div>
              </li>
            </ol>
          </section>
          <p v-if="!assembly.orderedQuestions.length" class="assembly-editor-empty">返回选题，把题目加入试卷篮后会在这里生成预览。</p>
        </article>
      </main>

      <aside class="assembly-editor-panel assembly-editor-panel--export" aria-labelledby="assembly-export-title">
        <header class="assembly-panel-heading">
          <div>
            <p class="assembly-kicker">EXPORT</p>
            <h2 id="assembly-export-title">导出</h2>
          </div>
          <button type="button" class="assembly-link" @click="assembly.loadRecords">刷新</button>
        </header>
        <p>确认预览后生成文件。导出不会改写题库内容。</p>
        <div class="assembly-export-actions">
          <button
            type="button"
            class="assembly-button is-primary"
            :disabled="!assembly.canExport || assembly.submitting"
            @click="assembly.submitExport('docx')"
          >
            导出 Word
          </button>
          <button
            type="button"
            class="assembly-button"
            :disabled="!assembly.canExport || assembly.submitting"
            @click="assembly.submitExport('markdown')"
          >
            导出 Markdown
          </button>
        </div>

        <div v-if="currentJob" class="assembly-job">
          <div>
            <strong>导出任务 #{{ currentJob.id }}</strong>
            <span>{{ currentJob.status }} · {{ Math.round(currentJob.progress * 100) }}%</span>
          </div>
          <progress :value="currentJob.progress" max="1" />
          <div class="assembly-job__actions">
            <a
              v-if="currentJob.status === 'succeeded' && typeof currentJob.result.download_url === 'string'"
              class="assembly-button is-primary"
              :href="currentJob.result.download_url"
            >
              下载文件
            </a>
            <button v-if="!currentJobDone" type="button" class="assembly-button" @click="jobs.cancel(currentJob.id)">取消</button>
            <button
              v-if="currentJob.status === 'failed' || currentJob.status === 'cancelled'"
              type="button"
              class="assembly-button"
              @click="assembly.retryExport(currentJob.id)"
            >
              重试
            </button>
          </div>
        </div>

        <section class="assembly-record-section">
          <header>
            <strong>最近导出</strong>
            <span>{{ assembly.records.length }} 条</span>
          </header>
          <ul v-if="assembly.records.length" class="assembly-records">
            <li v-for="record in assembly.records" :key="record.id">
              <div>
                <strong>{{ record.title }}</strong>
                <span>{{ record.question_count }} 题 · {{ record.export_format }}</span>
                <small>{{ record.filename }}</small>
              </div>
              <div>
                <a class="assembly-link" :href="record.download_url">下载</a>
                <button type="button" class="assembly-link" @click="assembly.restoreRecord(record.id)">恢复</button>
                <button type="button" class="assembly-link is-danger" @click="deleteRecord(record.id)">删除</button>
              </div>
            </li>
          </ul>
          <p v-else>暂无导出记录。</p>
        </section>
      </aside>
    </div>
  </div>
</template>
