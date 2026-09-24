<script setup lang="ts">
import { computed } from 'vue'

import type { TrainingOverviewDistribution } from '../../api/training'

const props = defineProps<{
  distribution: TrainingOverviewDistribution
}>()

const segments = computed(() => {
  const { weak, review, stable, missing } = props.distribution
  const total = weak + review + stable + missing
  return ([
    ['weak', '待补强', weak],
    ['review', '需巩固', review],
    ['stable', '较稳定', stable],
    ['missing', '证据不足', missing],
  ] as const)
    .filter(([, , count]) => count > 0)
    .map(([tier, label, count]) => ({
      tier,
      label: `${label} ${count} 人`,
      width: total > 0 ? (count / total) * 100 : 0,
    }))
})
</script>

<template>
  <span class="overview-tier-bar" role="img" :aria-label="segments.map(s => s.label).join('，')">
    <i
      v-for="segment in segments"
      :key="segment.tier"
      :class="`is-${segment.tier}`"
      :title="segment.label"
      :style="{ width: `${segment.width}%` }"
    />
  </span>
</template>
