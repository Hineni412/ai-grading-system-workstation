<script lang="ts">
export type StatusTone =
  | 'neutral'
  | 'info'
  | 'success'
  | 'warning'
  | 'danger'
  | 'ai'
  | 'teacher'
</script>

<script setup lang="ts">
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const props = defineProps<{
  tone: StatusTone
  label: string
}>()

/* 颜色走 tokens.css 令牌（经 Tailwind 任意值引用），保持原有语义色体系 */
const toneClasses: Record<StatusTone, string> = {
  neutral: 'border-border bg-secondary text-(--color-text-secondary)',
  info: 'border-(--color-info)/20 bg-(--color-info-subtle) text-(--color-info)',
  success: 'border-(--color-success)/20 bg-(--color-success-subtle) text-(--color-success)',
  warning: 'border-(--color-warning)/20 bg-(--color-warning-subtle) text-(--color-warning)',
  danger: 'border-destructive/20 bg-(--color-danger-subtle) text-destructive',
  ai: 'border-(--color-ai)/20 bg-(--color-ai-subtle) text-(--color-ai)',
  teacher: 'border-(--color-teacher)/20 bg-(--color-teacher-subtle) text-(--color-teacher)',
}
</script>

<template>
  <Badge
    as="span"
    variant="outline"
    class="status-badge max-w-full whitespace-normal wrap-anywhere"
    :class="cn(toneClasses[props.tone])"
    data-testid="status-badge"
    :data-tone="tone"
    :aria-label="`状态：${label}`"
  >
    {{ label }}
  </Badge>
</template>
