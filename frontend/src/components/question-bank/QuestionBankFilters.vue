<script setup lang="ts">
import { reactive } from 'vue'

import type { QuestionBankFilters, QuestionBankSort, QuestionBankTagStatus } from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'

const store = useQuestionBankStore()
const draft = reactive({
  keyword: '',
  questionNumber: '',
  knowledgePoint: '',
  paperId: '',
  year: '',
  examType: '',
  examScope: '',
  grade: '',
  questionType: '',
  difficultyMin: '',
  difficultyMax: '',
  tagStatus: 'all' as QuestionBankTagStatus,
  sort: 'newest' as QuestionBankSort,
})

function optionalNumber(value: string): number | undefined {
  const parsed = Number(value)
  return value && Number.isFinite(parsed) ? parsed : undefined
}

function apply(): void {
  const difficultyMin = optionalNumber(draft.difficultyMin)
  const difficultyMax = optionalNumber(draft.difficultyMax)
  const hasDifficultyRange = difficultyMin !== undefined && difficultyMax !== undefined
  const filters: QuestionBankFilters = {
    page: 1,
    pageSize: store.pageSize,
    keyword: draft.keyword,
    questionNumber: draft.questionNumber,
    knowledgePoint: draft.knowledgePoint,
    paperIds: draft.paperId ? [Number(draft.paperId)] : [],
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
  store.filterDraft = filters
  void store.loadQuestions(filters)
}

function reset(): void {
  Object.assign(draft, {
    keyword: '',
    questionNumber: '',
    knowledgePoint: '',
    paperId: '',
    year: '',
    examType: '',
    examScope: '',
    grade: '',
    questionType: '',
    difficultyMin: '',
    difficultyMax: '',
    tagStatus: 'all',
    sort: 'newest',
  })
  apply()
}
</script>

<template>
  <form class="qb-filters" aria-label="题库筛选" @submit.prevent="apply">
    <label class="qb-field qb-field--wide">
      <span>关键词</span>
      <input v-model="draft.keyword" type="search" placeholder="搜索题干或答案">
    </label>
    <label class="qb-field">
      <span>题号</span>
      <input v-model="draft.questionNumber" placeholder="如 12">
    </label>
    <label class="qb-field">
      <span>知识点</span>
      <input v-model="draft.knowledgePoint" placeholder="如 二次函数">
    </label>
    <label class="qb-field">
      <span>试卷</span>
      <select v-model="draft.paperId">
        <option value="">全部试卷</option>
        <option v-for="paper in store.papers" :key="paper.id" :value="String(paper.id)">
          {{ paper.title || `试卷 #${paper.id}` }}
        </option>
      </select>
    </label>
    <label class="qb-field">
      <span>标注状态</span>
      <select v-model="draft.tagStatus">
        <option value="all">全部</option>
        <option value="tagged">核心标签齐全</option>
        <option value="untagged">待完善</option>
      </select>
    </label>
    <details class="qb-more-filters">
      <summary>更多筛选</summary>
      <div class="qb-more-filters__grid">
        <label class="qb-field"><span>年份</span><input v-model="draft.year" placeholder="2025"></label>
        <label class="qb-field"><span>考试类型</span><input v-model="draft.examType" placeholder="期末"></label>
        <label class="qb-field"><span>考试范围</span><input v-model="draft.examScope" placeholder="如 期中"></label>
        <label class="qb-field"><span>年级</span><input v-model="draft.grade" placeholder="九年级"></label>
        <label class="qb-field"><span>题型</span><input v-model="draft.questionType" placeholder="解答题"></label>
        <label class="qb-field"><span>最低难度</span><input v-model="draft.difficultyMin" type="number" min="1" max="10"></label>
        <label class="qb-field"><span>最高难度</span><input v-model="draft.difficultyMax" type="number" min="1" max="10"></label>
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
      </div>
    </details>
    <div class="qb-filters__actions">
      <button type="button" class="qb-button qb-button--quiet" @click="reset">清除</button>
      <button type="submit" class="qb-button qb-button--primary">应用筛选</button>
    </div>
  </form>
</template>
