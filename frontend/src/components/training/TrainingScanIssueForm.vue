<script setup lang="ts">
import { ref, watch } from 'vue'
import type { TrainingScanBatch, TrainingScanPage } from '../../api/training'
import { issueLabel } from '../../features/training/training-return'
import AppButton from '../design-system/AppButton.vue'
const props = defineProps<{ batch: TrainingScanBatch; page: TrainingScanPage; busy: boolean }>()
const emit = defineEmits<{ resolve: [action: 'match' | 'replace' | 'dismiss', paperId: string, pageNumber: number] }>()
const paperId = ref(''), pageNumber = ref(1)
watch(() => props.page.scan_page_id, () => {
  const missing = props.batch.submissions.find(s => s.status === 'manual_review' && s.missing_pages.length)
  paperId.value = missing?.paper_instance_id ?? ''; pageNumber.value = missing?.missing_pages[0] ?? 1
}, { immediate: true })
</script>
<template>
  <div class="return-issue-form"><p>{{ issueLabel(page.issue_code) }}</p><label>匹配到<select v-model="paperId" class="app-input" :disabled="busy"><option value="">请选择学生训练卷</option><option v-for="c in batch.candidates" :key="c.paper_instance_id" :value="c.paper_instance_id">{{ c.student_name || c.student_code || c.student_id }} · V{{ c.series_version }}</option></select></label><label>页码<input v-model.number="pageNumber" class="app-input" type="number" min="1" :max="batch.candidates.find(c => c.paper_instance_id === paperId)?.total_pages || 100" :disabled="busy"></label><div><AppButton variant="primary" :disabled="busy" @click="emit('resolve', 'match', paperId, pageNumber)">人工匹配</AppButton><AppButton variant="secondary" :disabled="busy" @click="emit('resolve', 'replace', paperId, pageNumber)">明确替换该页</AppButton><AppButton variant="ghost" :disabled="busy" @click="emit('resolve', 'dismiss', paperId, pageNumber)">忽略此页</AppButton></div></div>
</template>
<style scoped>
.return-issue-form{display:grid;gap:var(--space-4);font-size:var(--font-size-dense)}p{margin:0;color:var(--color-warning)}label{display:grid;gap:var(--space-2)}input{max-width:100px}select{min-width:0;max-width:100%}.return-issue-form>div{display:flex;flex-wrap:wrap;gap:var(--space-2)}
</style>
