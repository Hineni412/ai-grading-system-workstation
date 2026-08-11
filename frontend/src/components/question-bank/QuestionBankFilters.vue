<script setup lang="ts">
import { reactive } from 'vue'

import type {
  QuestionBankFilters,
  QuestionBankSort,
  QuestionBankTagStatus,
} from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'
import DifficultyRangeFilter from './DifficultyRangeFilter.vue'
import QuestionSortControl from './QuestionSortControl.vue'

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
  specialType: '',
  questionType: '',
  examScope: '',
  year: '',
  examType: '',
  grade: '',
  difficultyMin: 1,
  difficultyMax: 10,
  tagStatus: 'all' as QuestionBankTagStatus,
  // 试卷内查看时默认按题号排列，题库全局检索时默认按难度。
  sort: (props.paperId ? 'paper_order' : 'difficulty_desc') as QuestionBankSort,
})

function buildFilters(): QuestionBankFilters {
  const hasDifficultyRange = draft.difficultyMin !== 1 || draft.difficultyMax !== 10
  return {
    page: 1,
    pageSize: store.pageSize,
    keyword: draft.keyword,
    questionNumber: draft.questionNumber,
    knowledgePoint: draft.knowledgePoint,
    specialTypes: draft.specialType ? [draft.specialType] : [],
    paperIds: props.paperId ? [props.paperId] : [],
    years: draft.year ? [draft.year] : [],
    examTypes: draft.examType ? [draft.examType] : [],
    examScopes: draft.examScope ? [draft.examScope] : [],
    grades: draft.grade ? [draft.grade] : [],
    questionTypes: draft.questionType ? [draft.questionType] : [],
    difficultyMin: hasDifficultyRange ? draft.difficultyMin : undefined,
    difficultyMax: hasDifficultyRange ? draft.difficultyMax : undefined,
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
    specialType: '',
    questionType: '',
    examScope: '',
    year: '',
    examType: '',
    grade: '',
    difficultyMin: 1,
    difficultyMax: 10,
    tagStatus: 'all',
    sort: props.paperId ? 'paper_order' : 'difficulty_desc',
  })
  apply()
}

function changeSort(sort: QuestionBankSort): void {
  draft.sort = sort
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
    <div class="qb-field">
      <span>排序</span>
      <QuestionSortControl v-model="draft.sort" :show-paper-order="Boolean(props.paperId)" @change="changeSort" />
    </div>

    <DifficultyRangeFilter
      v-model:min="draft.difficultyMin"
      v-model:max="draft.difficultyMax"
      class="qb-difficulty-filter"
      @change="apply"
    />

    <details class="qb-more-filters">
      <summary>更多筛选</summary>
      <div class="qb-more-filters__grid">
        <label class="qb-field"><span>题号</span><input v-model="draft.questionNumber" placeholder="如：12"></label>
        <label class="qb-field"><span>特殊题型/考法</span><input v-model="draft.specialType" placeholder="如：动态几何题"></label>
        <label class="qb-field"><span>章节/范围</span><input v-model="draft.examScope" placeholder="如：函数"></label>
        <label class="qb-field"><span>年份</span><input v-model="draft.year" placeholder="2026"></label>
        <label class="qb-field"><span>试卷类型</span><input v-model="draft.examType" placeholder="如：期末"></label>
        <label class="qb-field"><span>年级</span><input v-model="draft.grade" placeholder="如：九年级"></label>
      </div>
    </details>

    <div class="qb-filters__actions">
      <button type="button" class="qb-button is-quiet" @click="reset">清除</button>
      <button type="submit" class="qb-button is-primary">应用筛选</button>
    </div>
  </form>
</template>
