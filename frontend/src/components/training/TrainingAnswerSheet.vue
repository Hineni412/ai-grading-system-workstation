<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TrainingScanPage, TrainingSubmission } from '../../api/training'
import { issueLabel } from '../../features/training/training-return'
const props = defineProps<{ submission?: TrainingSubmission; issue?: TrainingScanPage; pages: TrainingScanPage[]; currentPage: number }>()
const emit = defineEmits<{ page: [number: number] }>()
const numbers = computed(() => Array.from({ length: props.submission?.expected_total_pages ?? 0 }, (_, i) => i + 1))
const page = computed(() => props.issue ?? props.pages.filter(p => p.submission_id === props.submission?.submission_id && p.state === 'assigned').find(p => p.page_number === props.currentPage))
const failed = ref(false)
watch(() => page.value?.preview_url, () => { failed.value = false })
</script>
<template>
  <section class="return-answer" aria-label="原始扫描答卷">
    <header><strong>{{ issue ? `上传文件第 ${issue.upload_page_number} 页 · 异常页` : `${submission?.student_name || submission?.student_code || submission?.student_id} · V${submission?.series_version} · ${submission?.expected_total_pages} 页` }}</strong><nav v-if="!issue" aria-label="答卷页码"><button v-for="n in numbers" :key="n" type="button" :aria-pressed="currentPage === n" @click="emit('page', n)">第 {{ n }} 页</button></nav><a v-if="page" :href="page.preview_url" target="_blank" rel="noopener">放大查看</a></header>
    <p v-if="issue" class="answer-issue">{{ issueLabel(issue.issue_code) }}</p>
    <div class="return-answer__body"><div v-if="!page" class="missing-page">缺第 {{ currentPage }} 页</div><p v-else-if="failed">扫描页暂时无法显示 · <a :href="page.preview_url" target="_blank" rel="noopener">放大查看</a></p><a v-else :href="page.preview_url" target="_blank" rel="noopener"><img :src="page.preview_url" :alt="issue ? `待人工核对的上传文件第 ${issue.upload_page_number} 页` : `原始答卷第 ${currentPage} 页`" @error="failed = true"></a></div>
  </section>
</template>
<style scoped>
.return-answer{display:flex;flex-direction:column;min-width:0;min-height:0}header{display:flex;align-items:center;flex-wrap:wrap;gap:var(--space-2);padding:var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);font-size:var(--font-size-dense)}header strong{overflow-wrap:anywhere}header>a{margin-left:auto;font-size:var(--font-size-caption);white-space:nowrap}a{color:var(--color-accent)}nav{display:flex;gap:var(--space-1);flex-wrap:wrap}nav button{padding:var(--space-1) var(--space-2);border:0;background:transparent;font:inherit;font-size:var(--font-size-caption);border-radius:var(--radius-control);cursor:pointer}nav button[aria-pressed=true]{background:var(--color-accent-subtle);color:var(--color-accent)}.answer-issue{padding:var(--space-2) var(--space-3);background:var(--color-warning-subtle);color:var(--color-warning);font-size:var(--font-size-dense);margin:var(--space-2) 0 0}
.return-answer__body{flex:1;min-height:0;overflow:auto;padding:var(--space-4);text-align:center}.return-answer__body>a{display:block}.return-answer img{display:block;width:100%;height:auto;background:white;box-shadow:var(--shadow-raised)}.return-answer p{font-size:var(--font-size-dense)}.missing-page{display:grid;place-items:center;aspect-ratio:210/297;border:1px dashed var(--color-border-strong);background:var(--color-bg-subtle);color:var(--color-text-muted)}
</style>
