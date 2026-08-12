<script setup lang="ts">
import { computed } from 'vue'

import { Button, type ButtonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { AppIconName } from '../../navigation'
import AppIcon from '../shell/AppIcon.vue'

defineOptions({ inheritAttrs: false })

const props = withDefaults(defineProps<{
  label: string
  icon: AppIconName
  type?: 'button' | 'submit' | 'reset'
  variant?: 'ghost' | 'secondary'
  disabled?: boolean
}>(), {
  type: 'button',
  variant: 'ghost',
  disabled: false,
})

const variantMap: Record<NonNullable<typeof props.variant>, ButtonVariants['variant']> = {
  ghost: 'ghost',
  secondary: 'outline',
}

const buttonClass = computed(() =>
  cn('app-icon-button flex-none', props.variant === 'secondary' && 'bg-card'),
)
</script>

<template>
  <Button
    v-bind="$attrs"
    :variant="variantMap[variant]"
    size="icon"
    :class="buttonClass"
    :data-variant="variant"
    :type="type"
    :disabled="disabled"
    :aria-label="label"
    :title="label"
  >
    <AppIcon :name="icon" />
  </Button>
</template>
