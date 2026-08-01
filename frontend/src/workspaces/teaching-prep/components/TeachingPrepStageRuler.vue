<script setup lang="ts">
import { useTeachingPrepWorkbenchContext } from '../workbench/context'

const workbench = useTeachingPrepWorkbenchContext()
</script>

<template>
  <nav class="tp-stage-ruler" aria-label="备课进度">
    <button
      v-for="(item, index) in workbench.stages.value"
      :key="item.id"
      class="tp-stage-ruler__step"
      :class="`is-${item.state}`"
      type="button"
      :aria-current="item.state === 'current' ? 'step' : undefined"
      :aria-label="`${item.label}：${item.explanation}`"
      @click="workbench.openStage(item.id)"
    >
      <span class="tp-stage-ruler__index" aria-hidden="true">
        {{ item.state === 'complete' ? '✓' : index + 1 }}
      </span>
      <span class="tp-stage-ruler__copy">
        <strong>{{ item.label }}</strong>
        <small>{{ item.explanation }}</small>
      </span>
    </button>
  </nav>
</template>
