<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import AppButton from '../design-system/AppButton.vue'
defineProps<{ names: string[]; retry: boolean; batch: boolean }>()
const emit = defineEmits<{ confirm: []; close: [] }>()
const dialog = ref<HTMLElement | null>(null)
const previous = document.activeElement as HTMLElement | null
onMounted(async () => { await nextTick(); dialog.value?.querySelector<HTMLButtonElement>('button')?.focus() })
onBeforeUnmount(() => { if (previous?.isConnected) previous.focus({ preventScroll: true }) })
function keydown(e: KeyboardEvent) {
  e.stopPropagation()
  if (e.key === 'Escape') { e.preventDefault(); emit('close') }
  if (e.key !== 'Tab') return
  const buttons = [...(dialog.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])]
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
  e.preventDefault(); buttons[(index + (e.shiftKey ? -1 : 1) + buttons.length) % buttons.length]?.focus()
}
</script>
<template>
  <Teleport to="body"><div class="return-dialog-backdrop fx-overlay" @click.self="emit('close')"><section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="training-cost-title" aria-describedby="training-cost-description" class="return-cost-dialog fx-dialog" @keydown="keydown"><h2 id="training-cost-title">{{ retry ? '重试这一份' : batch ? `判定 ${names.length} 份` : '判定这一份' }}</h2><p>{{ names.slice(0, 10).join('、') }}{{ names.length > 10 ? ` 等 ${names.length} 人` : '' }}</p><p id="training-cost-description">{{ batch ? `将逐份发送这 ${names.length} 份答卷、题目和判定点，共 ${names.length} 次模型请求，产生费用` : `将发送本份训练答卷、题目和判定点，${retry ? '追加 1 次模型请求' : '调用模型 1 次'}并产生费用` }}；实际费用以模型服务商计费为准；失败后不会自动重试。</p><footer><AppButton variant="secondary" @click="emit('close')">取消</AppButton><AppButton variant="primary" @click="emit('confirm')">{{ batch ? `确认判定 ${names.length} 份` : retry ? '确认重试一次' : '确认判定' }}</AppButton></footer></section></div></Teleport>
</template>
<style scoped>
.return-dialog-backdrop{position:fixed;inset:0;z-index:var(--shell-dialog-z-index,200);display:grid;place-items:center;padding:var(--space-4);background:var(--color-overlay-mask)}.return-cost-dialog{width:min(520px,100%);max-height:85vh;overflow:auto;background:var(--color-bg-surface);border-radius:var(--radius-overlay);box-shadow:var(--shadow-overlay);padding:var(--space-5)}h2{font-size:var(--font-size-h3);font-weight:var(--font-weight-semibold);margin:0}p{font-size:var(--font-size-dense);line-height:var(--line-height-body);overflow-wrap:anywhere}footer{display:flex;justify-content:flex-end;gap:var(--space-2);margin-top:var(--space-5)}
</style>
