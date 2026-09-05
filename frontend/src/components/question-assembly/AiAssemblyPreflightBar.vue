<script setup lang="ts">
import type { AiAssemblyPreflight } from '../../api/ai-assembly'
import AppButton from '../design-system/AppButton.vue'

defineProps<{
  preflight: AiAssemblyPreflight | null
  loading?: boolean
  confirming?: boolean
  confirmLabel?: string
}>()

const emit = defineEmits<{
  confirm: []
}>()
</script>

<template>
  <div class="ai-assembly-preflight" data-testid="ai-assembly-preflight">
    <p v-if="loading">正在估算本次模型调用…</p>
    <template v-else-if="preflight">
      <template v-if="preflight.configured">
        <p>
          将使用模型 <strong>{{ preflight.model_name }}</strong>
          （{{ preflight.service_name }}），调用 {{ preflight.call_count }} 次，
          预计消耗约 {{ preflight.estimated_total_tokens }} tokens。
        </p>
        <AppButton variant="primary" :disabled="confirming" @click="emit('confirm')">
          {{ confirming ? '正在提交…' : (confirmLabel ?? '确认并生成细目表') }}
        </AppButton>
      </template>
      <p v-else class="ai-assembly-preflight__warning">
        尚未配置内容生成模型。请先到"模型配置"页为"题库与评分标准生成"选择模型，再回来使用 AI 组卷。
      </p>
    </template>
    <p v-else>预检信息暂不可用，请稍后重试。</p>
  </div>
</template>

<style scoped>
.ai-assembly-preflight {
  align-items: center;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel, 10px);
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  justify-content: space-between;
  padding: 12px 16px;
}

.ai-assembly-preflight p {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin: 0;
}

.ai-assembly-preflight strong {
  color: var(--color-text-primary);
}

.ai-assembly-preflight__warning {
  color: var(--color-warning) !important;
}
</style>
