<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  id: string
  label: string
  hint?: string
  error?: string
  required?: boolean
}>()

const describedBy = computed(() =>
  [props.hint ? `${props.id}-hint` : '', props.error ? `${props.id}-error` : '']
    .filter(Boolean)
    .join(' ') || undefined,
)
</script>

<template>
  <div class="app-field" :data-invalid="Boolean(error)">
    <label class="app-field__label" :for="id">
      {{ label }}<span v-if="required" aria-hidden="true"> *</span>
    </label>
    <slot
      :inputId="id"
      :ariaDescribedby="describedBy"
      :ariaInvalid="error ? 'true' : undefined"
      :ariaRequired="required ? 'true' : undefined"
    />
    <p v-if="hint" :id="`${id}-hint`" class="app-field__hint">{{ hint }}</p>
    <p v-if="error" :id="`${id}-error`" class="app-field__error">{{ error }}</p>
  </div>
</template>

<style scoped>
.app-field {
  display: grid;
  min-width: 0;
  gap: var(--space-2);
}

.app-field__label {
  color: var(--color-text-primary);
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-medium);
}

.app-field__hint,
.app-field__error {
  margin: 0;
  font-size: var(--font-size-caption);
  line-height: var(--line-height-body);
}

.app-field__hint {
  color: var(--color-text-secondary);
}

.app-field__error {
  color: var(--color-danger);
}
</style>
