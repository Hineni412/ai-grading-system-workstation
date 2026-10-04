<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PersonalizedPaperInstance } from '../../api/training'
import AppButton from '../design-system/AppButton.vue'
const props = defineProps<{ instances: PersonalizedPaperInstance[]; busy: boolean; supplement?: boolean }>()
const emit = defineEmits<{ import: [files: File[], ids?: string[]] }>()
const selectedIds = ref<string[]>([])
const files = ref<File[]>([])
const adjusting = ref(false)
const frozen = computed(() => props.instances.filter(p => p.status === 'frozen'))
watch(frozen, value => {
  const latest = new Map<string, PersonalizedPaperInstance>()
  for (const p of value) if (!latest.has(p.student_id) || latest.get(p.student_id)!.series_version < p.series_version) latest.set(p.student_id, p)
  selectedIds.value = [...latest.values()].map(p => p.paper_instance_id)
}, { immediate: true })
function choose(event: Event) { files.value = [...((event.target as HTMLInputElement).files ?? [])] }
function drop(event: DragEvent) { if (!props.busy) files.value = [...(event.dataTransfer?.files ?? [])] }
function submit() { emit('import', [...files.value], props.supplement ? undefined : [...selectedIds.value]) }
</script>
<template>
  <section class="return-start" aria-label="回收答卷">
    <h2 v-if="!supplement">回收答卷</h2>
    <p v-if="!frozen.length && !supplement">先生成 PDF 训练卷，再回收答卷。</p>
    <template v-else>
      <div v-if="!supplement" class="return-start__count">本次回收 <b>{{ selectedIds.length }}</b> 份训练卷（每人最新版本）<AppButton variant="ghost" :aria-expanded="adjusting" @click="adjusting = !adjusting">调整 ▾</AppButton></div>
      <fieldset v-if="adjusting && !supplement"><legend class="sr-only">选择回收训练卷</legend><label v-for="p in frozen" :key="p.paper_instance_id"><input v-model="selectedIds" type="checkbox" :value="p.paper_instance_id" :disabled="busy">{{ p.student_name || p.student_code || p.student_id }} · V{{ p.series_version }} · {{ p.pages.length }} 页</label></fieldset>
      <label class="return-dropzone" @dragover.prevent @drop.prevent="drop"><strong>选择或拖入扫描文件</strong><span>PDF / JPG / PNG，可多选</span><input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" :disabled="busy" @change="choose"></label>
      <div v-if="files.length" class="return-files"><span>已选 {{ files.length }} 个文件</span><ul><li v-for="(file, i) in files" :key="i">{{ file.name }}</li></ul></div>
      <AppButton variant="primary" :disabled="busy || !files.length || (!supplement && !selectedIds.length)" @click="submit">{{ busy ? '正在识别归组…' : `导入并归组（${files.length} 个文件）` }}</AppButton>
    </template>
  </section>
</template>
<style scoped>
.return-start{max-width:760px;padding:var(--space-5);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);font-size:var(--font-size-dense)}
.return-start h2{font-size:var(--font-size-h3);margin:0 0 var(--space-3)}.return-start__count{display:flex;align-items:center;flex-wrap:wrap;gap:var(--space-2);margin-bottom:var(--space-3)}
fieldset{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:var(--space-2);margin:0 0 var(--space-3);padding:var(--space-3);border:1px solid var(--color-border-subtle);border-radius:var(--radius-control)}fieldset label{display:flex;align-items:center;gap:var(--space-2)}
.return-dropzone{position:relative;display:flex;flex-direction:column;align-items:center;gap:var(--space-2);padding:var(--space-6);border:1px dashed var(--color-border-strong);border-radius:var(--radius-panel);cursor:pointer;margin-bottom:var(--space-3)}.return-dropzone:focus-within{box-shadow:var(--focus-ring)}.return-dropzone input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}.return-dropzone span{font-size:var(--font-size-caption);color:var(--color-text-muted)}
.return-files{margin-block:var(--space-3);overflow-wrap:anywhere}.return-files ul{padding-left:var(--space-4);margin:var(--space-2) 0;color:var(--color-text-secondary)}
</style>
