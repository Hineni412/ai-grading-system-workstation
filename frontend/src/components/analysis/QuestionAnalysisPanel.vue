<script setup lang="ts">
import { computed } from 'vue'

import type { QuestionAnalysisItem } from '../../api/analysis'
import type { ResourceState } from '../../stores/workbench'
import QuestionAnalysisTable from './QuestionAnalysisTable.vue'

const props = defineProps<{
  classes: string[]
  items: QuestionAnalysisItem[]
  state: ResourceState
  error: string
  updatedAt: string | null
  selectedClass: string | null
  selectedQuestionId: string | null
}>()

defineEmits<{
  selectClass: [className: string | null]
  selectQuestion: [questionId: string]
  retry: []
}>()

const selectedItem = computed(() =>
  props.items.find((item) => item.question_id === props.selectedQuestionId) ?? props.items[0] ?? null,
)

function displayTime(value: string | null): string {
  return value?.replace('T', ' ').replace('Z', '') ?? '时间暂不可用'
}
</script>

<template>
  <section class="workbench-section analysis-panel" aria-labelledby="question-analysis-title">
    <header class="workbench-section__heading analysis-panel__heading">
      <div>
        <p class="workbench-eyebrow">按现有评分结果汇总</p>
        <h2 id="question-analysis-title">班级题目分析</h2>
      </div>
      <label class="workbench-field" for="analysis-class">
        <span>班级</span>
        <select
          id="analysis-class"
          :value="selectedClass ?? ''"
          @change="$emit('selectClass', ($event.target as HTMLSelectElement).value || null)"
        >
          <option value="">全部班级</option>
          <option v-for="className in classes" :key="className" :value="className">
            {{ className }}
          </option>
        </select>
      </label>
    </header>

    <p v-if="state === 'loading' && items.length === 0" class="workbench-state-copy" role="status">
      正在读取题目分析…
    </p>
    <div v-else-if="state === 'error'" class="workbench-inline-error" role="alert">
      <p>分析数据暂时无法读取；工作台其他内容仍可使用</p>
      <button type="button" class="workbench-secondary-button" @click="$emit('retry')">
        重新加载分析
      </button>
    </div>
    <template v-else>
      <p v-if="state === 'loading'" class="workbench-state-copy" role="status">正在更新题目分析…</p>
      <div v-if="state === 'stale-error'" class="workbench-stale" role="alert">
        <span>数据可能不是最新 · 上次更新 {{ displayTime(updatedAt) }}</span>
        <button type="button" class="workbench-link-button" @click="$emit('retry')">
          重新加载分析
        </button>
      </div>
      <p v-if="items.length === 0" class="workbench-empty-copy">当前考试还没有已批改题目</p>
      <template v-else>
        <p class="analysis-summary">
          当前共 {{ items.length }} 个题目汇总；选择题目可查看学生得分与扣分证据。
        </p>
        <p v-if="selectedItem" class="analysis-sample-copy">
          本题基于 {{ selectedItem.attempt_count }} 份已批改作答
        </p>
        <p v-if="selectedItem?.attempt_count === 1" class="workbench-sample-warning" role="note">
          当前仅 1 份已批改作答，不代表整体情况
        </p>
        <QuestionAnalysisTable
          :items="items"
          :selected-question-id="selectedQuestionId"
          @select="$emit('selectQuestion', $event)"
        />
      </template>
    </template>
  </section>
</template>
