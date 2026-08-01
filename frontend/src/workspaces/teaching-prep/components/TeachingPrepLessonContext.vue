<script setup lang="ts">
import { computed } from 'vue'

import { useTeachingPrepWorkbenchContext } from '../workbench/context'

const props = withDefaults(defineProps<{ compact?: boolean }>(), {
  compact: false,
})
const workbench = useTeachingPrepWorkbenchContext()
const lesson = computed(() => workbench.catalog.selectedLesson)
const semester = computed(() => workbench.catalog.selectedSemester)
</script>

<template>
  <div class="tp-lesson-context" :class="{ 'is-compact': props.compact }">
    <span class="tp-lesson-context__eyebrow">当前课时</span>
    <strong>{{ lesson?.title ?? '尚未选择课时' }}</strong>
    <span v-if="lesson">{{ lesson.duration_minutes ?? 45 }} 分钟</span>
    <span v-if="semester">{{ semester.school_year }} · {{ semester.term === 'first' ? '第一学期' : '第二学期' }}</span>
    <span class="tp-safe-note">原课件不会被覆盖</span>
  </div>
</template>
