<script setup lang="ts">
import AppIconButton from '../design-system/AppIconButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle } from 'reka-ui'
import { knowledgeLeafLabel } from '../../api/question-bank'
import type { TrainingDiagnosis, TrainingStudentProfile } from '../../api/training'
import AppButton from '../design-system/AppButton.vue'
const props = defineProps<{ student: TrainingStudentProfile | null; diagnosis: TrainingDiagnosis }>()
const open = defineModel<boolean>('open', { default: false })
const emit = defineEmits<{ 'wrong-book': [studentId: string] }>()
const points = computed(() => {
  const kinds = new Map((props.diagnosis.knowledge_catalog ?? []).map(node => [node.knowledge_key, node.node_kind]))
  return [...new Map((props.student?.weak_points ?? []).filter(point => ['weak', 'unsteady'].includes(point.tier ?? '')
    && ['skill', 'topic'].includes(kinds.get(point.knowledge_key) ?? '')).map(point => [point.knowledge_key, point])).values()]
    .sort((a, b) => (a.mastery ?? 1) - (b.mastery ?? 1)).slice(0, 10)
})
</script>
<template>
  <DialogRoot v-model:open="open"><DialogPortal><DialogOverlay class="practice-drawer-overlay fx-overlay" />
    <DialogContent class="practice-student-drawer fx-drawer-right" :aria-describedby="undefined">
      <header><DialogTitle>{{ student?.student_name }}</DialogTitle><AppIconButton label="关闭学生详情" @click="open = false" icon="close" /></header>
      <p>{{ student?.student_code }} · {{ student?.class_id || '未分班' }}</p>
      <p>本学期考试得分率 <strong>{{ student?.score_rate_source === 'current_exam' && typeof student.score_rate === 'number' ? `${Math.round(student.score_rate * 100)}%` : '无成绩' }}</strong></p>
      <h3>明显薄弱与还不稳</h3>
      <ul><li v-for="point in points" :key="point.knowledge_key"><span>{{ knowledgeLeafLabel(point.knowledge_point) }}</span><small>{{ point.mastery === null || point.mastery === undefined ? '—' : `${Math.round(point.mastery * 100)}%` }} · {{ point.tier === 'weak' ? '明显薄弱' : '还不稳' }}</small></li></ul>
      <StatePanel v-if="!points.length" kind="empty" compact title="暂无明显薄弱或还不稳的条目。" />
      <footer><AppButton variant="primary" @click="student && emit('wrong-book', student.student_id)">只给此人出错题本</AppButton><RouterLink v-if="student" :to="{ name: 'student-evidence', params: { studentId: student.student_id }, query: { from: 'student' } }">查看作答证据 →</RouterLink></footer>
    </DialogContent>
  </DialogPortal></DialogRoot>
</template>
<style scoped>
.practice-drawer-overlay{position:fixed;inset:0;background:var(--color-overlay-mask);z-index:80}.practice-student-drawer{position:fixed;inset:0 0 0 auto;width:min(400px,100vw);z-index:81;padding:var(--space-5);background:var(--color-bg-surface);box-shadow:var(--shadow-overlay);overflow:auto;color:var(--color-text-primary)}header{display:flex;justify-content:space-between;align-items:center;gap:var(--space-3);font-size:var(--font-size-h3);font-weight:var(--font-weight-semibold)}p{font-size:var(--font-size-dense);color:var(--color-text-secondary);line-height:1.7}h3{font-size:var(--font-size-h3);margin-top:var(--space-6)}ul{list-style:none;padding:0}li{display:flex;justify-content:space-between;gap:var(--space-3);padding:var(--space-3) 0;border-bottom:1px solid var(--color-border-subtle);font-size:var(--font-size-dense)}small{color:var(--color-text-muted);white-space:nowrap}footer{display:grid;gap:var(--space-4);margin-top:var(--space-6)}a{color:var(--color-accent);font-size:var(--font-size-dense);text-decoration:none}
</style>
