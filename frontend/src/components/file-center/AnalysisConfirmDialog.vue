<script setup lang="ts">
import type { AnalysisPreflight, ReportType } from '../../api/exports'
import AppButton from '../design-system/AppButton.vue'
import AppDialog from '../design-system/AppDialog.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { formatTokenCount, reportTypeLabel } from './report-format'

const props = defineProps<{
  preflight: AnalysisPreflight | null
  loading: boolean
  reportType: ReportType | null
  submitting: boolean
}>()
const emit = defineEmits<{
  close: []
  confirm: []
}>()
</script>

<template>
  <AppDialog
    open
    :title="props.reportType ? reportTypeLabel(props.reportType) : '分析报告'"
    width="wide"
    :dismissible="!props.submitting"
    data-testid="analysis-confirm-dialog"
    @update:open="emit('close')"
  >
    <form @submit.prevent="emit('confirm')">
      <StatePanel
        v-if="loading"
        kind="loading"
        title="正在读取生成条件…"
      />

      <template v-else-if="preflight">
        <p
          v-if="!preflight.configured"
          class="file-center__warning"
          role="alert"
          data-testid="analysis-not-configured"
        >
          未配置内容生成模型，请前往 设置→模型配置 绑定后重试。
        </p>

        <div class="excel-settings-preview" aria-label="生成条件概览">
          <div>
            <span>目标服务</span>
            <strong data-testid="analysis-service">
              {{ preflight.service_name ?? '未配置' }}
            </strong>
          </div>
          <div>
            <span>模型</span>
            <strong data-testid="analysis-model">
              {{ preflight.model_name ?? '未配置' }}
            </strong>
          </div>
          <div>
            <span>模型调用次数</span>
            <strong data-testid="analysis-call-count">
              {{ preflight.call_count + preflight.cause_call_count }} 次
            </strong>
          </div>
          <div>
            <span>文本 token 量（粗略估算）</span>
            <strong data-testid="analysis-tokens">
              {{ formatTokenCount(preflight.estimated_total_tokens + preflight.cause_estimated_tokens) }}
            </strong>
          </div>
        </div>

        <p
          v-if="preflight.cause_total_questions > 0"
          class="excel-settings-dialog__explanation"
          data-testid="analysis-cause-count"
        >
          其中先整理错因：{{ preflight.cause_call_count }} 次调用（共
          {{ preflight.cause_total_questions }} 道失分题，已整理或整理失败的题不重复调用）；
          报告叙述：{{ preflight.call_count }} 次调用。
        </p>

        <p
          v-if="preflight.cache_hits > 0"
          class="excel-settings-dialog__explanation"
          data-testid="analysis-cache-hits"
        >
          其中 {{ preflight.cache_hits }} 份复用已生成内容，不重复计费。
        </p>

        <p class="excel-settings-dialog__explanation">
          将向上述服务发送题目资料和学生答卷图片，请使用支持图片的内容生成模型。
          图片用量另计，实际费用取决于服务商定价。AI 分析内容仅供参考，最终成绩保持教师确认结果。
        </p>
      </template>

      <div class="excel-settings-dialog__actions">
        <span>确认后才会发起模型调用并产生费用。</span>
        <div>
          <AppButton variant="secondary"
            @click="emit('close')"
          >
            取消
          </AppButton>
          <AppButton variant="primary" type="submit"
            data-testid="confirm-analysis"
            :disabled="
              loading
              || preflight?.configured !== true
              || submitting
            "
          >
            确认生成
          </AppButton>
        </div>
      </div>
    </form>
  </AppDialog>
</template>
