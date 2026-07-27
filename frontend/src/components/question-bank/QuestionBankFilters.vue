<script setup lang="ts">
import { reactive } from 'vue'

import type {
  QuestionBankFilters,
  QuestionBankSort,
  QuestionBankTagStatus,
} from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'

const props = withDefaults(defineProps<{
  paperId?: number | null
}>(), {
  paperId: null,
})

const store = useQuestionBankStore()
const draft = reactive({
  keyword: '',
  questionNumber: '',
  knowledgePoint: '',
  questionType: '',
  examScope: '',
  year: '',
  examType: '',
  grade: '',
  difficultyMin: '',
  difficultyMax: '',
  tagStatus: 'all' as QuestionBankTagStatus,
  sort: 'newest' as QuestionBankSort,
})

function optionalNumber(value: string): number | undefined {
  const parsed = Number(value)
  return value && Number.isFinite(parsed) ? parsed : undefined
}

function buildFilters(): QuestionBankFilters {
  const difficultyMin = optionalNumber(draft.difficultyMin)
  const difficultyMax = optionalNumber(draft.difficultyMax)
  const hasDifficultyRange = difficultyMin !== undefined && difficultyMax !== undefined
  return {
    page: 1,
    pageSize: store.pageSize,
    keyword: draft.keyword,
    questionNumber: draft.questionNumber,
    knowledgePoint: draft.knowledgePoint,
    paperIds: props.paperId ? [props.paperId] : [],
    years: draft.year ? [draft.year] : [],
    examTypes: draft.examType ? [draft.examType] : [],
    examScopes: draft.examScope ? [draft.examScope] : [],
    grades: draft.grade ? [draft.grade] : [],
    questionTypes: draft.questionType ? [draft.questionType] : [],
    difficultyMin: hasDifficultyRange ? difficultyMin : undefined,
    difficultyMax: hasDifficultyRange ? difficultyMax : undefined,
    tagStatus: draft.tagStatus,
    sort: draft.sort,
  }
}

function apply(): void {
  const filters = buildFilters()
  store.filterDraft = filters
  void store.loadQuestions(filters)
}

function reset(): void {
  Object.assign(draft, {
    keyword: '',
    questionNumber: '',
    knowledgePoint: '',
    questionType: '',
    examScope: '',
    year: '',
    examType: '',
    grade: '',
    difficultyMin: '',
    difficultyMax: '',
    tagStatus: 'all',
    sort: 'newest',
  })
  apply()
}
</script>

<template>
  <form class="qb-filters" aria-label="试题筛选" @submit.prevent="apply">
    <label class="qb-field qb-field--search">
      <span>搜索</span>
      <input v-model="draft.keyword" type="search" placeholder="输入题干关键词">
    </label>
    <label class="qb-field">
      <span>题型</span>
      <input v-model="draft.questionType" placeholder="如：选择题">
    </label>
    <label class="qb-field">
      <span>知识点</span>
      <input v-model="draft.knowledgePoint" placeholder="如：二次函数">
    </label>
    <label class="qb-field">
      <span>标签完整度</span>
      <select v-model="draft.tagStatus">
        <option value="all">全部</option>
        <option value="tagged">核心标签完整</option>
        <option value="untagged">待完善</option>
      </select>
    </label>
    <label class="qb-field">
      <span>排序</span>
      <select v-model="draft.sort">
        <option value="newest">最近更新</option>
        <option value="difficulty">按难度</option>
        <option value="frequency_midterm">期中常见</option>
        <option value="frequency_final">期末常见</option>
        <option value="frequency_zhongkao">中考常见</option>
        <option value="frequency_contextual">情境题常见</option>
      </select>
    </label>

    <details class="qb-more-filters">
      <summary>更多筛选</summary>
      <div class="qb-more-filters__grid">
        <label class="qb-field"><span>题号</span><input v-model="draft.questionNumber" placeholder="如：12"></label>
        <label class="qb-field"><span>章节/范围</span><input v-model="draft.examScope" placeholder="如：函数"></label>
        <label class="qb-field"><span>年份</span><input v-model="draft.year" placeholder="2026"></label>
        <label class="qb-field"><span>试卷类型</span><input v-model="draft.examType" placeholder="如：期末"></label>
        <label class="qb-field"><span>年级</span><input v-model="draft.grade" placeholder="如：九年级"></label>
        <label class="qb-field"><span>最低难度</span><input v-model="draft.difficultyMin" type="number" min="1" max="10"></label>
        <label class="qb-field"><span>最高难度</span><input v-model="draft.difficultyMax" type="number" min="1" max="10"></label>
      </div>
    </details>

    <div class="qb-filters__actions">
      <button type="button" class="qb-button is-quiet" @click="reset">清除</button>
      <button type="submit" class="qb-button is-primary">应用筛选</button>
    </div>
  </form>
</template>
