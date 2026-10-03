<script setup lang="ts">
import { computed } from 'vue'

import type { TrainingOverviewDistribution } from '../../api/training'

const props = defineProps<{
  distribution: TrainingOverviewDistribution
  showCounts?: boolean
}>()

const segments = computed(() => {
  const { weak, unsteady, stable, insufficient } = props.distribution
  const total = weak + unsteady + stable + insufficient
  return ([
    ['weak', '明显薄弱', weak],
    ['unsteady', '还不稳', unsteady],
    ['stable', '较稳定', stable],
    ['insufficient', '证据不足', insufficient],
  ] as const)
    .filter(([, , count]) => count > 0)
    .map(([tier, label, count]) => ({
      tier,
      count,
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
    >{{ showCounts ? segment.count : '' }}</i>
  </span>
</template>
