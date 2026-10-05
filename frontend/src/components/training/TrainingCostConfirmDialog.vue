<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import AppDialog from '../design-system/AppDialog.vue'
defineProps<{ names: string[]; retry: boolean; batch: boolean }>()
const emit = defineEmits<{ confirm: []; close: [] }>()
</script>
<template>
  <AppDialog
    open
    :title="retry ? '重试这一份' : batch ? `判定 ${names.length} 份` : '判定这一份'"
    @update:open="emit('close')"
  >
    <p>{{ names.slice(0, 10).join('、') }}{{ names.length > 10 ? ` 等 ${names.length} 人` : '' }}</p>
    <p>{{ batch ? `将逐份发送这 ${names.length} 份答卷、题目和判定点，共 ${names.length} 次模型请求，产生费用` : `将发送本份训练答卷、题目和判定点，${retry ? '追加 1 次模型请求' : '调用模型 1 次'}并产生费用` }}；实际费用以模型服务商计费为准；失败后不会自动重试。</p>
    <template #footer>
      <AppButton variant="secondary" @click="emit('close')">取消</AppButton>
      <AppButton variant="primary" @click="emit('confirm')">{{ batch ? `确认判定 ${names.length} 份` : retry ? '确认重试一次' : '确认判定' }}</AppButton>
    </template>
  </AppDialog>
</template>
<style scoped>
p{font-size:var(--font-size-dense);line-height:var(--line-height-body);overflow-wrap:anywhere}
</style>
