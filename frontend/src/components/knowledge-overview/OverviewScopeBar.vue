<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useMasteryOverviewStore } from '../../stores/mastery-overview'
const curriculum = useCurriculumScopeStore()
const store = useMasteryOverviewStore()
const classes = computed(() => [...new Set(store.students.map(s => s.class_name?.trim()).filter((s): s is string => !!s))]
  .sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true })))
onMounted(() => { if (curriculum.loadState === 'ready') store.activate(curriculum.selectedVolumeId) })
watch([() => curriculum.loadState, () => curriculum.selectedVolumeId], () => {
  if (curriculum.loadState === 'ready') store.activate(curriculum.selectedVolumeId)
})
</script>
<template>
  <div class="knowledge-overview-scope-bar" role="group" aria-label="学生范围">
    <strong>{{ curriculum.selectedVolume?.label || '教学学期' }}</strong>
    <span>本学期 {{ store.overview?.exam_scope.sessions.length ?? '—' }} 场考试 + 已发布训练</span>
    <button type="button" :class="{ 'is-active': store.scopeSelection === 'all' }" :aria-pressed="store.scopeSelection === 'all'"
      @click="store.selectScope('all', curriculum.selectedVolumeId)">全部学生</button>
    <button v-for="name in classes" :key="name" type="button" :class="{ 'is-active': store.scopeSelection === name }"
      :aria-pressed="store.scopeSelection === name" @click="store.selectScope(name, curriculum.selectedVolumeId)">{{ /^\d+$/.test(name) ? `${name} 班` : name }}</button>
    <details class="overview-scope-definition"><summary>口径说明</summary>
      <div>
        <p>有证据学生：该项有作答观测的学生，四档人数之和等于此数。</p>
        <p>明显薄弱占比 = 明显薄弱人数 ÷ 该项有证据学生人数。</p>
        <p>往届内容单列，不计入本册数字。</p>
      </div>
    </details>
  </div>
  <FeedbackBanner v-if="store.studentsError" role="alert" tone="error" description="班级和学生列表暂时无法读取。"
    action-label="重新加载筛选项" @action="store.loadStudents()" />
</template>
