<script setup lang="ts">
import type { StudentAnalysisItem } from '../../api/analysis'
import type { ResourceState } from '../../stores/workbench'

defineProps<{
  questionId: string | null
  items: StudentAnalysisItem[]
  state: ResourceState
  updatedAt: string | null
}>()

defineEmits<{
  retry: []
  openReview: [questionId: string]
}>()

function score(value: number | null): string {
  return value === null ? '暂不可用' : String(value)
}

function safeEvidenceUrl(value: string): string | null {
  return value.startsWith('/api/') ? value : null
}

function displayTime(value: string | null): string {
  return value?.replace('T', ' ').replace('Z', '') ?? '时间暂不可用'
}
</script>

<template>
  <section class="workbench-section student-detail" aria-labelledby="student-analysis-title">
    <header class="workbench-section__heading">
      <div>
        <p class="workbench-eyebrow">题目证据下钻</p>
        <h2 id="student-analysis-title">当前题目学生明细</h2>
      </div>
      <button
        v-if="questionId"
        type="button"
        class="workbench-secondary-button"
        @click="$emit('openReview', questionId)"
      >
        进入评分复核
      </button>
    </header>

    <p v-if="questionId === null" class="workbench-empty-copy">请选择题目查看学生明细</p>
    <p v-else-if="state === 'idle'" class="workbench-state-copy" role="status">正在准备学生明细…</p>
    <p v-else-if="state === 'loading' && items.length === 0" class="workbench-state-copy" role="status">
      正在读取学生明细…
    </p>
    <div v-else-if="state === 'error'" class="workbench-inline-error" role="alert">
      <p>学生明细暂时无法读取</p>
      <button type="button" class="workbench-secondary-button" @click="$emit('retry')">重新加载学生明细</button>
    </div>
    <template v-else>
      <p v-if="state === 'loading'" class="workbench-state-copy" role="status">正在更新学生明细…</p>
      <div v-if="state === 'stale-error'" class="workbench-stale" role="alert">
        <span>学生明细可能不是最新 · 上次更新 {{ displayTime(updatedAt) }}</span>
        <button type="button" class="workbench-link-button" @click="$emit('retry')">重新加载学生明细</button>
      </div>
      <p v-if="items.length === 0" class="workbench-empty-copy">当前题目没有学生明细</p>
      <div v-else class="workbench-table-wrap">
      <table class="analysis-table student-table">
        <caption class="sr-only">当前题目学生得分、扣分与证据入口</caption>
        <thead>
          <tr>
            <th scope="col">学生</th>
            <th scope="col">班级</th>
            <th scope="col">得分</th>
            <th scope="col">扣分与原因</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.detail_id">
            <td>
              <strong>{{ item.student_name }}</strong>
              <span class="student-table__secondary">{{ item.student_code ?? '学号暂不可用' }}</span>
            </td>
            <td>{{ item.class_name }}</td>
            <td>{{ score(item.score_awarded) }} / {{ score(item.max_score) }}</td>
            <td>
              {{ item.deduction_amount === null ? '扣分暂不可用' : `扣 ${item.deduction_amount} 分` }}
              <span class="student-table__secondary">{{ item.deduction_reason ?? '未记录扣分原因' }}</span>
            </td>
            <td class="student-table__actions">
              <a v-if="safeEvidenceUrl(item.evidence_url)" :href="safeEvidenceUrl(item.evidence_url) ?? undefined">
                查看答卷证据
              </a>
              <button type="button" class="workbench-link-button" @click="$emit('openReview', item.question_id)">
                进入评分复核
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </template>
  </section>
</template>
