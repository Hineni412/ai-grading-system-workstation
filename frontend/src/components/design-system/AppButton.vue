<script setup lang="ts">
import { computed } from 'vue'

import { Button, type ButtonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'

defineOptions({ inheritAttrs: false })

const props = withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  type?: 'button' | 'submit' | 'reset'
  disabled?: boolean
  loading?: boolean
  loadingLabel?: string
  block?: boolean
}>(), {
  variant: 'secondary',
  type: 'button',
  disabled: false,
  loading: false,
  loadingLabel: '正在处理',
  block: false,
})

const variantMap: Record<NonNullable<typeof props.variant>, ButtonVariants['variant']> = {
  primary: 'default',
  secondary: 'outline',
  ghost: 'ghost',
  danger: 'destructive',
}

const buttonClass = computed(() =>
  cn(
    'app-button',
    props.variant === 'secondary' && 'bg-card',
    props.variant === 'ghost' && 'text-primary',
    props.loading && 'is-loading',
    props.block && 'is-block w-full',
  ),
)
</script>

<template>
  <Button
    v-bind="$attrs"
    :variant="variantMap[variant]"
    :class="buttonClass"
    :data-variant="variant"
    :type="type"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <span v-if="$slots.leading" class="app-button__icon inline-flex items-center" aria-hidden="true">
      <slot name="leading" />
    </span>
    <span
      v-if="loading"
      class="app-button__spinner size-3.5 animate-spin rounded-full border-2 border-current border-e-transparent"
      aria-hidden="true"
    />
    <span class="app-button__label inline-flex items-center"><slot /></span>
    <span v-if="loading" class="app-button__loading-label sr-only">{{ loadingLabel }}</span>
  </Button>
</template>
