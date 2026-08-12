<script setup lang="ts">
import { computed } from 'vue'

import { Label } from '@/components/ui/label'

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
  <div class="app-field grid min-w-0 gap-2" :data-invalid="Boolean(error)">
    <Label class="app-field__label text-sm font-medium" :for="id">
      {{ label }}<span v-if="required" aria-hidden="true"> *</span>
    </Label>
    <slot
      :inputId="id"
      :ariaDescribedby="describedBy"
      :ariaInvalid="error ? 'true' : undefined"
      :ariaRequired="required ? 'true' : undefined"
    />
    <p v-if="hint" :id="`${id}-hint`" class="app-field__hint m-0 text-xs leading-normal text-(--color-text-secondary)">
      {{ hint }}
    </p>
    <p v-if="error" :id="`${id}-error`" class="app-field__error m-0 text-xs leading-normal text-destructive">
      {{ error }}
    </p>
  </div>
</template>
