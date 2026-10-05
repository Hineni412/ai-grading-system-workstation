<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useMasteryOverviewStore } from '../../stores/mastery-overview'
const curriculum = useCurriculumScopeStore()
const store = useMasteryOverviewStore()
const classes = computed(() => [...new Set(store.students.map(s => s.class_name?.trim()).filter((s): s is string => !!s))]
  .sort((a, b) => a.localeCompare(b, 'zh')))
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
        <p>当前范围：所选教学学期的全部学生或单个班级，包含本学期考试和已发布训练。</p>
        <p>有证据学生：该知识点或技能有作答观测的学生。四档人数之和等于有证据学生人数。</p>
        <p>明显薄弱人数占比：明显薄弱人数 ÷ 该项有证据学生人数。没有证据显示“无证据”。</p>
        <p>有学生明显薄弱的项：至少 1 名学生明显薄弱的知识点或技能；知识点、技能分别计数。</p>
        <p>至少 1 项明显薄弱的学生：本册知识点或技能至少 1 项明显薄弱，分母为本册有证据学生。</p>
        <p>群体掌握度是有观测学生的等权平均；80% 群体区间是各生区间边界的平均。</p>
        <p>本册数字只计当前教材知识点与技能。往届内容单列，不计入本册数字。</p>
      </div>
    </details>
  </div>
  <FeedbackBanner v-if="store.studentsError" role="alert" tone="error" description="班级和学生列表暂时无法读取。"
    action-label="重新加载筛选项" @action="store.loadStudents()" />
</template>
