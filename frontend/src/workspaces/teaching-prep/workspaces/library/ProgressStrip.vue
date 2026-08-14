<script setup lang="ts">
import { computed } from 'vue'

import { useTeachingPrepCatalogStore } from '../../stores/catalog'

const catalog = useTeachingPrepCatalogStore()
const emit = defineEmits<{ 'open-import': [] }>()

const steps = computed(() => catalog.libraryProgressSteps)
// 当前步骤 = 第一个未完成的步骤；全部完成时不高亮
const currentIndex = computed(() => steps.value.findIndex(step => !step.done))
</script>

<template>
  <div class="tp-progress-strip" aria-label="资料准备进度">
    <template v-for="(step, index) in steps" :key="step.key">
      <span
        v-if="index > 0"
        class="tp-progress-strip__sep"
        :class="{ 'is-done': steps[index - 1]?.done }"
        aria-hidden="true"
      />
      <button
        v-if="step.key === 'import'"
        type="button"
        class="tp-progress-strip__node"
        :class="{
          'is-done': step.done,
          'is-current': index === currentIndex,
        }"
        data-testid="progress-import"
        @click="emit('open-import')"
      >
        <span class="tp-progress-strip__dot">{{ step.done ? '✓' : index + 1 }}</span>
        <span>{{ step.label }}</span>
        <span v-if="index === currentIndex" class="tp-progress-strip__hint">· {{ step.hint }}</span>
      </button>
      <span
        v-else
        class="tp-progress-strip__node"
        :class="{
          'is-done': step.done,
          'is-current': index === currentIndex,
        }"
      >
        <span class="tp-progress-strip__dot">{{ step.done ? '✓' : index + 1 }}</span>
        <span>{{ step.label }}</span>
        <span v-if="index === currentIndex" class="tp-progress-strip__hint">· {{ step.hint }}</span>
      </span>
    </template>
  </div>
</template>
