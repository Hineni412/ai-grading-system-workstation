<script setup lang="ts">
import StatePanel from '../design-system/StatePanel.vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../ui/sheet'
import { knowledgeLeafLabel } from '../../api/question-bank'
import type { TrainingDiagnosis, TrainingStudentProfile } from '../../api/training'
import AppButton from '../design-system/AppButton.vue'
const props = defineProps<{ student: TrainingStudentProfile | null; diagnosis: TrainingDiagnosis }>()
const open = defineModel<boolean>('open', { default: false })
const emit = defineEmits<{ 'wrong-book': [studentId: string] }>()
const points = computed(() => {
  const kinds = new Map((props.diagnosis.knowledge_catalog ?? []).map(node => [node.knowledge_key, node.node_kind]))
  // Typed releases list question types here; legacy releases keep skills/topics.
  const listed = props.diagnosis.target_kind === 'type' ? ['type'] : ['skill', 'topic']
  return [...new Map((props.student?.weak_points ?? []).filter(point => ['weak', 'unsteady'].includes(point.tier ?? '')
    && listed.includes(kinds.get(point.knowledge_key) ?? '')).map(point => [point.knowledge_key, point])).values()]
    .sort((a, b) => (a.mastery ?? 1) - (b.mastery ?? 1)).slice(0, 10)
})
</script>
<template>
  <Sheet v-model:open="open"><SheetContent class="practice-student-drawer gap-0" :aria-describedby="undefined">
      <SheetHeader class="p-0"><SheetTitle>{{ student?.student_name }}</SheetTitle></SheetHeader>
      <p>{{ student?.student_code }} · {{ student?.class_id || '未分班' }}</p>
      <p>本学期考试得分率 <strong>{{ student?.score_rate_source === 'current_exam' && typeof student.score_rate === 'number' ? `${Math.round(student.score_rate * 100)}%` : '无成绩' }}</strong></p>
      <h3>明显薄弱与还不稳</h3>
      <ul><li v-for="point in points" :key="point.knowledge_key"><span>{{ knowledgeLeafLabel(point.knowledge_point) }}</span><small>{{ point.mastery === null || point.mastery === undefined ? '—' : `${Math.round(point.mastery * 100)}%` }} · {{ point.tier === 'weak' ? '明显薄弱' : '还不稳' }}</small></li></ul>
      <StatePanel v-if="!points.length" kind="empty" compact title="暂无明显薄弱或还不稳的条目。" />
      <footer><AppButton variant="primary" @click="student && emit('wrong-book', student.student_id)">只给此人出错题本</AppButton><RouterLink v-if="student" :to="{ name: 'student-evidence', params: { studentId: student.student_id }, query: { from: 'student' } }">查看作答证据 →</RouterLink></footer>
    </SheetContent></Sheet>
</template>
<style scoped>
.practice-student-drawer{width:min(400px,100vw);padding:var(--space-5);overflow-y:auto;color:var(--color-text-primary)}[data-slot="sheet-header"]{margin-bottom:var(--space-2)}p{font-size:var(--font-size-dense);color:var(--color-text-secondary);line-height:1.7}h3{font-size:var(--font-size-h3);margin-top:var(--space-6)}ul{list-style:none;padding:0}li{display:flex;justify-content:space-between;gap:var(--space-3);padding:var(--space-3) 0;border-bottom:1px solid var(--color-border-subtle);font-size:var(--font-size-dense)}small{color:var(--color-text-muted);white-space:nowrap}footer{display:grid;gap:var(--space-4);margin-top:var(--space-6)}a{color:var(--color-accent);font-size:var(--font-size-dense);text-decoration:none}
</style>
