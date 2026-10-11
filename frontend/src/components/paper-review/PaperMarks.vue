<script setup lang="ts">
import type { PaperMark } from '../../api/paper-review'
defineProps<{ mark?: PaperMark; busy: boolean; questionId: string }>()
defineEmits<{ change: [mark: PaperMark] }>()
</script>
<template>
  <span class="pr-marks" @click.stop @keydown.stop>
    <button v-for="value in (['keep', 'fix'] satisfies PaperMark[])" :key="value" type="button" :disabled="busy"
      :class="{ active: mark === value }" :aria-pressed="mark === value" :aria-label="`${questionId} ${value === 'keep' ? '留' : '改'}`"
      @click="$emit('change', value)">{{ value === 'keep' ? '留' : '改' }}</button>
  </span>
</template>
