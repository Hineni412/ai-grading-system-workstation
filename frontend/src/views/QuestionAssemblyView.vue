<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import type { AssemblyQuestion, AssemblySection } from '../api/assembly'
import { TERMINAL_JOB_STATUSES } from '../api/jobs'
import { useAssemblyStore } from '../stores/assembly'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'

const assembly = useAssemblyStore()
const bank = useQuestionBankStore()
const jobs = useJobStore()
const newSectionTitle = ref('')
const draggedQuestionId = ref<number | null>(null)

const currentJob = computed(() => (
  assembly.exportJobId === null ? null : jobs.jobs[assembly.exportJobId] ?? null
))
const currentJobDone = computed(() => (
  currentJob.value !== null && TERMINAL_JOB_STATUSES.has(currentJob.value.status)
))

onMounted(() => {
  void Promise.all([
    assembly.load(),
    jobs.initialize(),
  ])
})

function questionLabel(question: AssemblyQuestion): string {
  return `第 ${question.question_number || question.id} 题`
}

function scoreLabel(question: AssemblyQuestion): string {
  return question.score_value === null ? '未标分' : `${question.score_value} 分`
}

function previewSections(): Array<{ id: string; title: string; questions: AssemblyQuestion[] }> {
  if (assembly.draft.layout_mode === 'sections' && assembly.draft.sections.length > 0) {
    return assembly.draft.sections.map((section) => ({
      id: section.id,
      title: section.title,
      questions: section.question_ids
        .map((id) => assembly.questionMap.get(id))
        .filter((item): item is AssemblyQuestion => item !== undefined),
    }))
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

function sectionHasQuestion(section: AssemblySection, questionId: number): boolean {
  return section.question_ids.includes(questionId)
}

function toggleSectionQuestion(sectionId: string, questionId: number, checked: boolean): void {
  const next = assembly.draft.sections.map((section) => {
    if (section.id !== sectionId) return section
    const current = new Set(section.question_ids)
    if (checked) current.add(questionId)
    else current.delete(questionId)
    return { ...section, question_ids: [...current] }
  })
  void assembly.replaceSections(next)
}

function addSection(): void {
  const title = newSectionTitle.value.trim()
  if (!title) return
  const next: AssemblySection = {
    id: `section-${Date.now().toString(36)}`,
    title,
    question_ids: [],
  }
  newSectionTitle.value = ''
  void assembly.replaceSections([...assembly.draft.sections, next])
}

function removeSection(sectionId: string): void {
  void assembly.replaceSections(assembly.draft.sections.filter((section) => section.id !== sectionId))
}

function startQuestionDrag(questionId: number): void {
  draggedQuestionId.value = questionId
}

function dropBefore(questionId: number): void {
  const dragged = draggedQuestionId.value
  draggedQuestionId.value = null
  if (dragged === null) return
  void assembly.moveQuestionBefore(dragged, questionId)
}

function dropIntoSection(sectionId: string): void {
  const dragged = draggedQuestionId.value
  draggedQuestionId.value = null
  if (dragged === null) return
  const next = assembly.draft.sections.map((section) => {
    const ids = section.question_ids.filter((id) => id !== dragged)
    return section.id === sectionId ? { ...section, question_ids: [...ids, dragged] } : { ...section, question_ids: ids }
  })
  void assembly.replaceSections(next)
}
</script>

<template>
  <section class="assembly">
    <header class="assembly__hero">
      <div>
        <p class="qb-eyebrow">Paper assembly</p>
        <h1 tabindex="-1">组卷工作台</h1>
        <p>把题库里已经选好的题加入篮子，调整顺序、分节和答案显示，再生成 Word 或 Markdown 试卷。</p>
      </div>
      <div class="assembly__summary" aria-label="组卷概况">
        <span><strong>{{ assembly.selectedQuestionCount }}</strong> 道题</span>
        <span><strong>{{ assembly.totalScore }}</strong> 已识别分值</span>
      </div>
    </header>

    <p v-if="assembly.message" class="qb-feedback is-error" role="status">{{ assembly.message }}</p>

    <div class="assembly__toolbar">
      <span>题库已选择 {{ bank.selectedCount }} 道</span>
      <button
        type="button"
        class="qb-button qb-button--primary"
        :disabled="assembly.loadState === 'loading' || bank.selectedCount === 0 || assembly.saveState === 'saving'"
        @click="assembly.addQuestions(bank.selectedQuestionIds)"
      >
        加入组卷篮
      </button>
      <button
        type="button"
        class="qb-button"
        :disabled="assembly.loadState === 'loading' || assembly.selectedQuestionCount === 0"
        @click="assembly.save({ ...assembly.draft, basket_ids: [], order_ids: [], sections: [] })"
      >
        清空篮子
      </button>
    </div>

    <div class="assembly__workspace">
      <aside class="assembly-panel assembly-panel--basket" aria-labelledby="assembly-basket-title">
        <header class="assembly-panel__heading">
          <h2 id="assembly-basket-title">组卷篮</h2>
          <span>{{ assembly.saveState === 'saving' ? '保存中' : '已保存草稿' }}</span>
        </header>

        <div class="assembly-settings">
          <label class="qb-field">
            <span>试卷标题</span>
            <input
              :value="assembly.draft.title"
              maxlength="120"
              placeholder="如：九年级函数专项练习"
              @change="assembly.updateSettings({ title: ($event.target as HTMLInputElement).value })"
            >
          </label>
          <label class="qb-field">
            <span>页眉说明</span>
            <input
              :value="assembly.draft.header_text"
              maxlength="200"
              placeholder="如：限时 45 分钟"
              @change="assembly.updateSettings({ header_text: ($event.target as HTMLInputElement).value })"
            >
          </label>
          <label class="qb-field">
            <span>预览版本</span>
            <select
              :value="assembly.draft.preview_mode"
              @change="assembly.updateSettings({ preview_mode: ($event.target as HTMLSelectElement).value as 'student' | 'teacher' })"
            >
              <option value="student">学生版</option>
              <option value="teacher">教师版</option>
            </select>
          </label>
          <label class="assembly-check">
            <input
              type="checkbox"
              :checked="assembly.draft.include_answer"
              @change="assembly.updateSettings({ include_answer: ($event.target as HTMLInputElement).checked })"
            >
            <span>导出时包含答案</span>
          </label>
        </div>

        <ol v-if="assembly.orderedQuestions.length" class="assembly-list">
          <li
            v-for="question in assembly.orderedQuestions"
            :key="question.id"
            draggable="true"
            @dragstart="startQuestionDrag(question.id)"
            @dragover.prevent
            @drop="dropBefore(question.id)"
          >
            <span class="assembly-list__index">{{ questionLabel(question) }}</span>
            <span class="assembly-list__text">{{ question.question_text }}</span>
            <span class="assembly-list__meta">{{ scoreLabel(question) }}</span>
            <button type="button" class="qb-link" @click="assembly.moveQuestion(question.id, -1)">上移</button>
            <button type="button" class="qb-link" @click="assembly.moveQuestion(question.id, 1)">下移</button>
            <button type="button" class="qb-link is-danger" @click="assembly.removeQuestion(question.id)">移除</button>
          </li>
        </ol>
        <p v-else class="assembly-empty">先在题库管理页勾选题目，再回到这里加入组卷篮。</p>
        <p v-if="assembly.missingQuestionIds.length" class="qb-feedback is-warning">
          有 {{ assembly.missingQuestionIds.length }} 道题已不可用，导出前需要移除。
        </p>
      </aside>

      <main class="assembly-paper" aria-labelledby="assembly-preview-title">
        <header class="assembly-paper__heading">
          <div>
            <h2 id="assembly-preview-title">{{ assembly.draft.title || '未命名试卷' }}</h2>
            <p>{{ assembly.draft.header_text || '姓名：__________ 班级：__________ 日期：__________' }}</p>
          </div>
          <select
            :value="assembly.draft.layout_mode"
            aria-label="试卷排版"
            @change="assembly.updateSettings({ layout_mode: ($event.target as HTMLSelectElement).value as 'sequential' | 'grouped_by_type' | 'sections' })"
          >
            <option value="sequential">按当前顺序</option>
            <option value="grouped_by_type">按题型归组</option>
            <option value="sections">按手动分节</option>
          </select>
        </header>

        <div v-if="assembly.draft.layout_mode === 'sections'" class="assembly-sections">
          <div class="assembly-sections__new">
            <input v-model="newSectionTitle" maxlength="80" placeholder="新增分节标题">
            <button type="button" class="qb-button" @click="addSection">新增分节</button>
          </div>
          <div
            v-for="section in assembly.draft.sections"
            :key="section.id"
            class="assembly-section-editor"
            @dragover.prevent
            @drop="dropIntoSection(section.id)"
          >
            <header>
              <strong>{{ section.title }}</strong>
              <button type="button" class="qb-link is-danger" @click="removeSection(section.id)">删除分节</button>
            </header>
            <label v-for="question in assembly.orderedQuestions" :key="question.id">
              <input
                type="checkbox"
                :checked="sectionHasQuestion(section, question.id)"
                @change="toggleSectionQuestion(section.id, question.id, ($event.target as HTMLInputElement).checked)"
              >
              <span>{{ questionLabel(question) }}</span>
            </label>
          </div>
        </div>

        <article class="assembly-sheet" :class="{ 'is-teacher': assembly.draft.preview_mode === 'teacher' }">
          <section v-for="section in previewSections()" :key="section.id">
            <h3>{{ section.title }}</h3>
            <ol>
              <li v-for="question in section.questions" :key="question.id">
                <p class="assembly-question-text">
                  <span v-if="question.score_value !== null">（{{ question.score_value }}分）</span>{{ question.question_text }}
                </p>
                <p v-if="assembly.draft.preview_mode === 'teacher' && question.answer_text" class="assembly-answer">
                  答案：{{ question.answer_text }}
                </p>
              </li>
            </ol>
          </section>
        </article>
      </main>

      <aside class="assembly-panel assembly-panel--exports" aria-labelledby="assembly-export-title">
        <header class="assembly-panel__heading">
          <h2 id="assembly-export-title">导出</h2>
          <button type="button" class="qb-link" @click="assembly.loadRecords">刷新记录</button>
        </header>
        <div class="assembly-export-actions">
          <button
            type="button"
            class="qb-button qb-button--primary"
            :disabled="assembly.loadState === 'loading' || !assembly.canExport || assembly.submitting"
            @click="assembly.submitExport('docx')"
          >
            导出 Word
          </button>
          <button
            type="button"
            class="qb-button"
            :disabled="assembly.loadState === 'loading' || !assembly.canExport || assembly.submitting"
            @click="assembly.submitExport('markdown')"
          >
            导出 Markdown
          </button>
        </div>

        <div v-if="currentJob" class="assembly-job">
          <strong>任务 #{{ currentJob.id }}</strong>
          <span>{{ currentJob.status }} · {{ Math.round(currentJob.progress * 100) }}%</span>
          <progress :value="currentJob.progress" max="1" />
          <div class="assembly-job__actions">
            <a
              v-if="currentJob.status === 'succeeded' && typeof currentJob.result.download_url === 'string'"
              class="qb-button"
              :href="currentJob.result.download_url"
            >下载</a>
            <button
              v-if="!currentJobDone"
              type="button"
              class="qb-button"
              @click="jobs.cancel(currentJob.id)"
            >
              取消
            </button>
            <button
              v-if="currentJob.status === 'failed' || currentJob.status === 'cancelled'"
              type="button"
              class="qb-button"
              @click="assembly.retryExport(currentJob.id)"
            >
              重试
            </button>
          </div>
        </div>

        <ul v-if="assembly.records.length" class="assembly-records">
          <li v-for="record in assembly.records" :key="record.id">
            <div>
              <strong>{{ record.title }}</strong>
              <span>{{ record.question_count }} 题 · {{ record.export_format }}</span>
              <small>{{ record.filename }}</small>
            </div>
            <a class="qb-link" :href="record.download_url">下载</a>
            <button type="button" class="qb-link" @click="assembly.restoreRecord(record.id)">恢复</button>
            <button type="button" class="qb-link is-danger" @click="assembly.deleteRecord(record.id)">删除记录</button>
          </li>
        </ul>
        <p v-else class="assembly-empty">暂无导出记录。</p>
      </aside>
    </div>
  </section>
</template>
