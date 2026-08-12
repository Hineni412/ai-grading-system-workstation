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
  neutral: 'border-border bg-(--color-bg-selected) text-(--color-text-secondary)',
  info: 'border-(--color-info) bg-(--color-info-subtle) text-(--color-info)',
  success: 'border-(--color-success) bg-(--color-success-subtle) text-(--color-success)',
  warning: 'border-(--color-warning) bg-(--color-warning-subtle) text-foreground',
  danger: 'border-destructive bg-(--color-danger-subtle) text-destructive',
  ai: 'border-(--color-ai) bg-(--color-ai-subtle) text-(--color-ai)',
  teacher: 'border-(--color-teacher) bg-(--color-teacher-subtle) text-(--color-teacher)',
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
