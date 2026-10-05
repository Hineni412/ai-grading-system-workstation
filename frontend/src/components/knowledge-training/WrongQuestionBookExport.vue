<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { useWrongQuestionBookExport } from '../../features/training/wrong-question-book-export'
const props = defineProps<{ studentIds: string[]; volumeId: string; scopeKeys: string[]; valid: boolean; blockedReason?: string }>()
const sessionIds = defineModel<number[] | null>('sessionIds', { default: null })
const includeSourceLabel = defineModel<boolean>('includeSourceLabel', { default: true })
const includeAnswerSpace = defineModel<boolean>('includeAnswerSpace', { default: true })
const { preview, loading, submitting, downloading, downloaded, message, job, jobs, busy, result,
  missing, emptyStudents, failedStudents, canSelect, loadPreview, recover, submit, download, reset } = useWrongQuestionBookExport(
    () => ({ studentIds: props.studentIds, volumeId: props.volumeId, scopeKeys: props.scopeKeys }), sessionIds, includeSourceLabel, includeAnswerSpace)
</script>
<template>
  <div class="wrong-book-export">
    <template v-if="canSelect">
      <fieldset class="wrong-book-exams" :disabled="!valid"><legend>本学期考试</legend>
        <label v-for="exam in preview?.sessions ?? []" :key="exam.session_id"><input v-model="sessionIds" type="checkbox" :value="exam.session_id"><span>{{ exam.session_name }}<small>已选学生错题 {{ preview?.session_wrong_counts[String(exam.session_id)] ?? 0 }} 道</small></span></label>
        <p v-if="preview && !preview.sessions.length">当前学期没有所选学生的考试成绩。</p>
      </fieldset>
      <div class="wrong-book-options"><label><input v-model="includeSourceLabel" type="checkbox">每题标注来源（考试 · 题号）</label><label><input v-model="includeAnswerSpace" type="checkbox">每题后留作答空白</label></div>
      <p v-if="loading" role="status">正在统计错题…</p>
      <p v-else-if="preview" class="wrong-book-summary" role="status">{{ preview.students.length }} 人 · 可导出 {{ preview.question_count }} 道错题 · 缺题库原题 {{ preview.missing_items.length }} 道 · {{ preview.empty_students.length }} 人无错题不生成<span v-if="scopeKeys.length"> · 不在所选章节 {{ preview.out_of_scope_count }} 道</span></p>
    </template>
    <div v-if="job" aria-live="polite"><template v-if="busy"><p>{{ job.detail || '正在生成错题本…' }}</p><progress :value="job.progress" max="1" /></template><p v-else-if="job.status === 'succeeded'">已完成 · {{ result.question_count || 0 }} 道错题</p><p v-else>本次导出未完成，请重新选择后导出。</p><p v-if="jobs.syncErrors[job.id]">任务状态暂时无法更新。<AppButton @click="jobs.refresh(job.id)">查询任务</AppButton></p></div>
    <details v-if="missing.length"><summary>缺少题库原题（{{ missing.length }} 道）</summary><ul><li v-for="(item, index) in missing" :key="index">{{ item.student_name }} · {{ item.session_name }} · {{ item.question_id }}</li></ul></details>
    <details v-if="emptyStudents.length"><summary>未生成学生（{{ emptyStudents.length }} 人）</summary><ul><li v-for="(item, index) in emptyStudents" :key="index">{{ item.student_name }} · {{ item.reason }}</li></ul></details>
    <p v-for="(item, index) in failedStudents" :key="index" role="alert">失败：{{ item.student_name }}（{{ item.reason }}）</p>
    <p v-if="message" role="alert">{{ message }}</p><p v-if="downloaded" role="status">下载已完成，本机临时导出文件已清除。</p><StatePanel v-if="!valid" kind="empty" compact :title="blockedReason || '当前不可导出'" />
    <footer><AppButton v-if="canSelect && !preview && !loading && valid" @click="loadPreview">重新加载</AppButton>
      <AppButton v-if="canSelect" variant="primary" :disabled="!valid || loading || !sessionIds?.length || !preview?.question_count" :loading="submitting" @click="submit">{{ studentIds.length === 1 ? '导出错题本（Word）' : `导出错题本（${Math.max(0, (preview?.students.length ?? 0) - (preview?.empty_students.length ?? 0))} 份 Word · ZIP）` }}</AppButton>
      <AppButton v-else-if="!job" :loading="submitting" @click="recover">查询此次提交</AppButton>
      <AppButton v-if="job?.status === 'succeeded' && result.download_url && !downloaded" variant="primary" :loading="downloading" @click="download">下载{{ String(result.filename).endsWith('.zip') ? '全部错题本' : '错题本' }}</AppButton><AppButton v-if="job && !busy" :disabled="downloading" @click="reset">重新选择导出</AppButton>
    </footer>
  </div>
</template>
<style scoped>
.wrong-book-export{font-size:var(--font-size-dense)}fieldset{margin:0;padding:0;border:0}legend{font-weight:var(--font-weight-semibold);margin-bottom:var(--space-2)}.wrong-book-exams label{display:flex;align-items:start;gap:var(--space-2);padding:var(--space-2) 0}.wrong-book-exams span{display:grid;gap:3px}.wrong-book-exams small{font-size:var(--font-size-caption);color:var(--color-text-muted)}.wrong-book-options{display:grid;gap:var(--space-3);margin:var(--space-4) 0}.wrong-book-options label{display:flex;align-items:center;gap:var(--space-2)}input{accent-color:var(--color-accent)}p{line-height:1.7;font-size:var(--font-size-caption);color:var(--color-text-secondary)}.wrong-book-summary{padding:var(--space-3);background:var(--color-bg-subtle);border-radius:var(--radius-control)}details{margin:var(--space-3) 0;font-size:var(--font-size-caption)}summary{cursor:pointer}ul{padding-left:var(--space-4);line-height:1.8}progress{width:100%}footer{display:grid;gap:var(--space-2);margin-top:var(--space-4)}
</style>
