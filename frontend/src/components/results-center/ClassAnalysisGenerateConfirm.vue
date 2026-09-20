<script setup lang="ts">
import type { ModelTaskBinding } from '../../api/model-profiles'
import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{
  kind: 'narrative' | 'causes'
  loading: boolean
  binding: ModelTaskBinding | null
  error: string
  submitting: boolean
  callCount: number
}>()

const emit = defineEmits<{
  close: []
  confirm: []
}>()
</script>

<template>
  <form
    class="class-analysis__confirm"
    aria-labelledby="regenerate-confirm-title"
    data-testid="regenerate-confirm-dialog"
    @submit.prevent="emit('confirm')"
  >
    <div class="class-analysis__dialog-heading">
      <div>
        <p class="results-center__eyebrow">AI 内容生成确认</p>
        <h3 id="regenerate-confirm-title">{{ props.kind === 'causes' ? '整理本场各题错因' : '生成班级分析' }}</h3>
      </div>
      <button type="button" class="class-analysis__link" @click="emit('close')">关闭</button>
    </div>

    <p v-if="props.loading" role="status">正在读取模型配置…</p>

    <template v-else>
      <p>
        {{ props.kind === 'causes' ? '各班共用逐题归并结果，结合作答与题目依据；输入未变且已升级的题目直接复用，旧结果保留。最多调用' : '将为本场各班分别生成分析，最多调用' }} {{ props.callCount }} 次内容生成模型（{{
          props.binding?.profile_name ?? '未配置'
        }}<template v-if="props.binding?.model"> · {{ props.binding.model }}</template>），
        实际费用取决于服务商定价。AI 分析内容仅供参考，建议抽查后再使用。
      </p>
      <p
        v-if="props.binding && !props.binding.profile_name"
        class="class-analysis__error"
        role="alert"
        data-testid="regenerate-not-configured"
      >
        未配置内容生成模型，请前往 设置→模型配置 绑定后重试。
      </p>
      <p v-if="props.error" class="class-analysis__error" role="alert" data-testid="regenerate-error">
        {{ props.error }}
      </p>
    </template>

    <div class="class-analysis__dialog-actions">
      <span>确认后才会发起模型调用并产生费用。</span>
      <div>
        <AppButton variant="secondary" @click="emit('close')">取消</AppButton>
        <AppButton
          variant="primary"
          type="submit"
          data-testid="regenerate-confirm"
          :disabled="
            props.loading
              || props.submitting
              || props.binding?.profile_name == null
              || props.binding?.profile_name === ''
          "
          :loading="props.submitting"
          loading-label="正在提交"
        >
          {{ props.kind === 'causes' ? '确认整理错因' : '确认生成' }}
        </AppButton>
      </div>
    </div>
  </form>
</template>
