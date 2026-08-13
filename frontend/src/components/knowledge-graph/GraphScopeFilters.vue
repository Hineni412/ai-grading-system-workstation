<script setup lang="ts">
import type { GraphQueryInput } from '../../api/graph'
import type { SessionSummary } from '../../api/sessions'
import type { StudentSummary } from '../../api/students'
import EvidenceScopeFilters from '../evidence/EvidenceScopeFilters.vue'

defineProps<{
  sessions: SessionSummary[]
  currentSessionId: number | null
  students: StudentSummary[]
  modelValue: GraphQueryInput | null
  applying: boolean
  scoreProfiles?: Record<string, Record<string, unknown>>
  curriculumVolumeId?: string | null
  evidenceFrom?: 'chapter' | 'student'
}>()

const emit = defineEmits<{ apply: [query: GraphQueryInput] }>()
</script>

<template>
  <EvidenceScopeFilters
    :sessions="sessions"
    :current-session-id="currentSessionId"
    :students="students"
    :model-value="modelValue"
    :applying="applying"
    :score-profiles="scoreProfiles ?? {}"
    :curriculum-volume-id="curriculumVolumeId"
    :evidence-from="evidenceFrom"
    apply-label="更新知识图谱"
    @apply="emit('apply', $event)"
  />
</template>
