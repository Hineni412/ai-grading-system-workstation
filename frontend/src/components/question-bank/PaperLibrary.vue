<script setup lang="ts">
import { computed, ref } from 'vue'

import type { QuestionBankPaper } from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'

const props = withDefaults(defineProps<{
  pendingTaxonomyCount?: number
}>(), {
  pendingTaxonomyCount: 0,
})

const emit = defineEmits<{
  open: [paper: QuestionBankPaper]
  import: []
  reviewTaxonomy: []
}>()

const store = useQuestionBankStore()
const keyword = ref('')
const year = ref('')
const examType = ref('')
const sourceType = ref('')
const progressStatus = ref('')

const years = computed(() => uniqueValues(store.papers.map((paper) => paper.year)))
const examTypes = computed(() => uniqueValues(store.papers.map((paper) => paper.exam_type)))

const filteredPapers = computed(() => {
  const search = keyword.value.trim().toLocaleLowerCase()
  return [...store.papers]
    .filter((paper) => {
      if (year.value && paper.year !== year.value) return false
      if (examType.value && paper.exam_type !== examType.value) return false
      if (sourceType.value && paper.source_type !== sourceType.value) return false
      if (
        progressStatus.value === 'complete' &&
        paper.tagged_question_count < paper.question_count
      ) return false
      if (
        progressStatus.value === 'pending' &&
        paper.tagged_question_count >= paper.question_count
      ) return false
      if (!search) return true
      return [
        paper.title,
        paper.year,
        paper.exam_type,
        paper.grade,
        paper.semester,
        paper.textbook_version,
        paper.province,
        paper.city,
        paper.district,
      ].some((value) => value?.toLocaleLowerCase().includes(search))
    })
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
})

const totalQuestions = computed(() => store.papers.reduce(
  (total, paper) => total + paper.question_count,
  0,
))
const completeQuestions = computed(() => store.papers.reduce(
  (total, paper) => total + paper.tagged_question_count,
  0,
))

function uniqueValues(values: Array<string | null>): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value?.trim())))]
    .sort((left, right) => right.localeCompare(left, 'zh-CN'))
}

function progressFor(paper: QuestionBankPaper): number {
  if (paper.question_count === 0) return 0
  return Math.round((paper.tagged_question_count / paper.question_count) * 100)
}

function sourceLabel(source: QuestionBankPaper['source_type']): string {
  if (source === 'docx') return 'Word'
  if (source === 'pdf') return 'PDF'
  return '文件'
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '日期未知'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date)
}

function resetFilters(): void {
  keyword.value = ''
  year.value = ''
  examType.value = ''
  sourceType.value = ''
  progressStatus.value = ''
}
</script>

<template>
  <section class="paper-library" aria-labelledby="paper-library-title">
    <header class="paper-library__header">
      <div>
        <p class="paper-library__eyebrow">PAPER LIBRARY</p>
        <h1 id="paper-library-title">试卷库</h1>
        <p>以老师上传的 Word 或 PDF 试卷为入口，再进入试题核对与标注。</p>
      </div>
      <div class="paper-library__stats" aria-label="试卷库概况">
        <span><strong>{{ store.papers.length }}</strong> 份试卷</span>
        <span><strong>{{ totalQuestions }}</strong> 道题</span>
        <span><strong>{{ completeQuestions }}</strong> 道标签完整</span>
        <button
          type="button"
          class="paper-button is-review"
          @click="emit('reviewTaxonomy')"
        >
          待审核新词
          <strong>{{ props.pendingTaxonomyCount }}</strong>
        </button>
        <button type="button" class="paper-button is-primary" @click="emit('import')">
          上传试卷
        </button>
      </div>
    </header>

    <div class="paper-library__filters">
      <label class="paper-search">
        <span class="sr-only">搜索试卷</span>
        <input v-model="keyword" type="search" placeholder="搜索试卷名称、地区或教材">
      </label>
      <label>
        <span>年份</span>
        <select v-model="year">
          <option value="">全部年份</option>
          <option v-for="item in years" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label>
        <span>试卷类型</span>
        <select v-model="examType">
          <option value="">全部类型</option>
          <option v-for="item in examTypes" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label>
        <span>文件</span>
        <select v-model="sourceType">
          <option value="">全部文件</option>
          <option value="docx">Word</option>
          <option value="pdf">PDF</option>
          <option value="other">其他</option>
        </select>
      </label>
      <label>
        <span>标注</span>
        <select v-model="progressStatus">
          <option value="">全部进度</option>
          <option value="complete">已完成</option>
          <option value="pending">待完善</option>
        </select>
      </label>
      <button type="button" class="paper-button is-quiet" @click="resetFilters">清除</button>
    </div>

    <p v-if="store.papersState === 'loading'" class="paper-library__state" role="status">
      正在读取试卷库…
    </p>
    <div v-else-if="store.papersState === 'error'" class="paper-library__state is-error" role="alert">
      <span>试卷库暂时无法读取。</span>
      <button type="button" class="paper-button is-quiet" @click="store.loadPapers()">重新读取</button>
    </div>
    <div v-else-if="filteredPapers.length === 0" class="paper-library__state">
      <strong>{{ store.papers.length ? '当前筛选下没有试卷' : '还没有导入试卷' }}</strong>
      <p>{{ store.papers.length ? '可以清除筛选后再查看。' : '上传 Word 或 PDF 后，会在这里生成一张试卷卡片。' }}</p>
    </div>

    <div v-else class="paper-library__grid">
      <article v-for="paper in filteredPapers" :key="paper.id" class="paper-card">
        <div class="paper-card__cover" :class="`is-${paper.source_type}`" aria-hidden="true">
          <span>{{ sourceLabel(paper.source_type) }}</span>
          <strong>试卷</strong>
          <i />
          <i />
          <i />
        </div>
        <div class="paper-card__body">
          <div class="paper-card__topline">
            <span class="paper-chip is-format">{{ sourceLabel(paper.source_type) }}</span>
            <span v-if="paper.year" class="paper-chip">{{ paper.year }}</span>
            <span v-if="paper.exam_type" class="paper-chip">{{ paper.exam_type }}</span>
            <span v-if="paper.grade" class="paper-chip">{{ paper.grade }}</span>
          </div>
          <h2>{{ paper.title || `未命名试卷 #${paper.id}` }}</h2>
          <p class="paper-card__meta">
            {{
              [
                paper.province,
                paper.city,
                paper.district,
                paper.semester,
                paper.textbook_version,
              ].filter(Boolean).join(' · ') || '来源信息待补充'
            }}
          </p>
          <div class="paper-card__progress-heading">
            <span>核心标签完整度</span>
            <strong>{{ paper.tagged_question_count }} / {{ paper.question_count }} 道</strong>
          </div>
          <div
            class="paper-card__progress"
            role="progressbar"
            aria-label="核心标签完整度"
            :aria-valuenow="progressFor(paper)"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <span :style="{ width: `${progressFor(paper)}%` }" />
          </div>
          <p class="paper-card__progress-note">
            {{ progressFor(paper) }}% 完整
            <template v-if="paper.tagged_any_question_count > paper.tagged_question_count">
              · 另有 {{ paper.tagged_any_question_count - paper.tagged_question_count }} 道已开始标注
            </template>
          </p>
          <footer>
            <span>更新于 {{ formatDate(paper.updated_at) }}</span>
            <button type="button" class="paper-button is-primary" @click="emit('open', paper)">
              查看试题
            </button>
          </footer>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.paper-library {
  display: grid;
  gap: 18px;
}

.paper-library__header {
  align-items: flex-end;
  display: flex;
  gap: 24px;
  justify-content: space-between;
}

.paper-library__eyebrow {
  color: var(--color-accent, #135e6b) !important;
  font-size: 11px !important;
  font-weight: 750;
  letter-spacing: .13em;
  margin: 0 0 5px !important;
}

.paper-library__header h1 {
  color: var(--color-text-primary, #1c2733);
  font-size: 30px;
  letter-spacing: -.04em;
  margin: 0;
}

.paper-library__header p {
  color: var(--color-text-secondary, #5c6672);
  font-size: 14px;
  margin: 7px 0 0;
}

.paper-library__stats {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  justify-content: flex-end;
}

.paper-library__stats span {
  color: var(--color-text-secondary, #5c6672);
  font-size: 12px;
}

.paper-library__stats strong {
  color: var(--color-text-primary, #1c2733);
  font-size: 19px;
  margin-right: 2px;
}

.paper-library__filters {
  align-items: flex-end;
  background: #fff;
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 12px;
  display: grid;
  gap: 10px;
  grid-template-columns: minmax(240px, 1fr) repeat(4, minmax(118px, 150px)) auto;
  padding: 14px;
}

.paper-library__filters label {
  color: var(--color-text-secondary, #5c6672);
  display: grid;
  font-size: 11px;
  font-weight: 650;
  gap: 5px;
}

.paper-library__filters input,
.paper-library__filters select {
  background: #fff;
  border: 1px solid var(--color-border-strong, #d8dbdf);
  border-radius: 8px;
  color: var(--color-text-primary, #1c2733);
  font: inherit;
  font-size: 13px;
  height: 36px;
  min-width: 0;
  padding: 0 10px;
}

.paper-library__filters input:focus,
.paper-library__filters select:focus {
  border-color: var(--color-accent, #135e6b);
  box-shadow: 0 0 0 3px rgb(19 94 107 / 10%);
  outline: none;
}

.paper-library__grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.paper-card {
  background: #fff;
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 12px;
  box-shadow: 0 1px 2px rgb(28 39 51 / 5%);
  display: grid;
  grid-template-columns: 108px minmax(0, 1fr);
  min-height: 230px;
  overflow: hidden;
  transition: border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
}

.paper-card:hover {
  border-color: rgb(19 94 107 / 35%);
  box-shadow: 0 8px 26px rgb(28 39 51 / 8%);
  transform: translateY(-1px);
}

.paper-card__cover {
  align-items: center;
  background: #edf4f3;
  border-right: 1px solid var(--color-border, #e2e4e7);
  color: var(--color-accent, #135e6b);
  display: flex;
  flex-direction: column;
  justify-content: center;
  overflow: hidden;
  padding: 16px;
  position: relative;
}

.paper-card__cover::before {
  background: currentColor;
  content: "";
  height: 100%;
  left: 0;
  opacity: .84;
  position: absolute;
  top: 0;
  width: 4px;
}

.paper-card__cover.is-pdf {
  background: #f8f0ed;
  color: #8a4b37;
}

.paper-card__cover.is-other {
  background: #f1f2f4;
  color: #5c6672;
}

.paper-card__cover span {
  font-size: 12px;
  font-weight: 750;
  letter-spacing: .08em;
}

.paper-card__cover strong {
  font-size: 25px;
  letter-spacing: .08em;
  margin: 9px 0 16px;
}

.paper-card__cover i {
  background: currentColor;
  height: 2px;
  margin: 3px 0;
  opacity: .18;
  width: 56px;
}

.paper-card__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 16px 17px 14px;
}

.paper-card__topline {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.paper-chip {
  background: #f4f5f6;
  border-radius: 999px;
  color: var(--color-text-secondary, #5c6672);
  font-size: 11px;
  padding: 3px 8px;
}

.paper-chip.is-format {
  background: rgb(19 94 107 / 10%);
  color: var(--color-accent, #135e6b);
  font-weight: 700;
}

.paper-card h2 {
  color: var(--color-text-primary, #1c2733);
  display: -webkit-box;
  font-size: 16px;
  line-height: 1.45;
  margin: 11px 0 5px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.paper-card__meta {
  color: var(--color-text-tertiary, #6b7684);
  font-size: 12px;
  margin: 0;
}

.paper-card__progress-heading {
  color: var(--color-text-secondary, #5c6672);
  display: flex;
  font-size: 11px;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 15px;
}

.paper-card__progress-heading strong {
  color: var(--color-text-primary, #1c2733);
}

.paper-card__progress {
  background: #edf0f1;
  border-radius: 999px;
  height: 6px;
  margin-top: 7px;
  overflow: hidden;
}

.paper-card__progress span {
  background: var(--color-accent, #135e6b);
  border-radius: inherit;
  display: block;
  height: 100%;
  min-width: 2px;
}

.paper-card__progress-note {
  color: var(--color-text-tertiary, #6b7684);
  font-size: 11px;
  margin: 6px 0 0;
}

.paper-card footer {
  align-items: center;
  border-top: 1px solid var(--color-border, #e2e4e7);
  color: var(--color-text-tertiary, #6b7684);
  display: flex;
  font-size: 11px;
  justify-content: space-between;
  margin-top: 12px;
  padding-top: 11px;
}

.paper-button {
  align-items: center;
  background: #fff;
  border: 1px solid var(--color-border-strong, #d8dbdf);
  border-radius: 8px;
  color: var(--color-text-primary, #1c2733);
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: 12px;
  font-weight: 650;
  height: 34px;
  justify-content: center;
  padding: 0 13px;
}

.paper-button:hover {
  background: #f6f7f7;
  border-color: #b9c0c5;
}

.paper-button.is-primary {
  background: var(--color-accent, #135e6b);
  border-color: var(--color-accent, #135e6b);
  color: #fff;
}

.paper-button.is-primary:hover {
  background: var(--color-accent-hover, #0e4a54);
}

.paper-button.is-quiet {
  color: var(--color-text-secondary, #5c6672);
}

.paper-button.is-review {
  background: #fff8e9;
  border-color: #efdcb0;
  color: #805a16;
  gap: 6px;
}

.paper-button.is-review:hover {
  background: #fff3d7;
  border-color: #e2c67f;
}

.paper-button.is-review strong {
  background: #805a16;
  border-radius: 999px;
  color: #fff;
  font-size: 10px;
  line-height: 18px;
  margin: 0;
  min-width: 18px;
  padding: 0 5px;
}

.paper-library__state {
  align-items: center;
  background: #fff;
  border: 1px dashed var(--color-border-strong, #d8dbdf);
  border-radius: 12px;
  color: var(--color-text-secondary, #5c6672);
  display: flex;
  flex-direction: column;
  gap: 8px;
  justify-content: center;
  min-height: 180px;
  padding: 24px;
  text-align: center;
}

.paper-library__state p {
  margin: 0;
}

.paper-library__state.is-error {
  color: #9a4136;
}

@media (max-width: 1260px) {
  .paper-library__filters {
    grid-template-columns: minmax(220px, 1fr) repeat(2, minmax(130px, 1fr));
  }

  .paper-library__grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .paper-library__header {
    align-items: flex-start;
    flex-direction: column;
  }

  .paper-library__stats {
    justify-content: flex-start;
  }

  .paper-library__filters {
    grid-template-columns: 1fr 1fr;
  }

  .paper-search {
    grid-column: 1 / -1;
  }

  .paper-card {
    grid-template-columns: 78px minmax(0, 1fr);
  }
}
</style>
