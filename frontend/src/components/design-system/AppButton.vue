<script setup lang="ts">
import { computed, ref } from 'vue'

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
  ripple?: boolean
}>(), {
  variant: 'secondary',
  type: 'button',
  disabled: false,
  loading: false,
  loadingLabel: '正在处理',
  block: false,
  ripple: false,
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
    props.ripple && 'relative overflow-hidden',
  ),
)

// 点击涟漪（来源：Inspira UI · Ripple Button 的效果，按需开启，默认关闭）。
// 颜色用 currentColor：跟随按钮文字色，不引入新的颜色令牌。
const RIPPLE_DURATION_MS = 600
const ripples = ref<Array<{ key: number; x: number; y: number; size: number }>>([])
let rippleKey = 0

function handleRippleClick(event: MouseEvent): void {
  if (!props.ripple || props.disabled || props.loading) return
  const button = event.currentTarget as HTMLElement | null
  if (!button) return
  // jsdom 下 getBoundingClientRect 全为 0，涟漪尺寸为 0，不产生可见副作用。
  const rect = button.getBoundingClientRect()
  const size = Math.max(rect.width, rect.height)
  const key = ++rippleKey
  ripples.value.push({
    key,
    x: event.clientX - rect.left - size / 2,
    y: event.clientY - rect.top - size / 2,
    size,
  })
  setTimeout(() => {
    ripples.value = ripples.value.filter((ripple) => ripple.key !== key)
  }, RIPPLE_DURATION_MS)
}
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
    @click="handleRippleClick"
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
    <span
      v-if="ripple"
      class="app-button__ripples pointer-events-none absolute inset-0"
      aria-hidden="true"
    >
      <span
        v-for="rippleItem in ripples"
        :key="rippleItem.key"
        class="app-button__ripple absolute rounded-full opacity-30"
        :style="{
          width: `${rippleItem.size}px`,
          height: `${rippleItem.size}px`,
          top: `${rippleItem.y}px`,
          left: `${rippleItem.x}px`,
        }"
      />
    </span>
  </Button>
</template>

<style scoped>
.app-button__ripple {
  background: currentcolor;
  transform: scale(0);
  animation: app-button-rippling 600ms ease-out;
}

@keyframes app-button-rippling {
  to {
    transform: scale(2);
    opacity: 0;
  }
}
</style>
