<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { ApiError } from '@/api/errors'
import AppButton from '@/components/design-system/AppButton.vue'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'

import { affairR1Api, studentR1Api, type AffairDetail, type AffairProfileDraft, type StudentCard } from '../api/r1'

const props = defineProps<{
  open: boolean
  affair: AffairDetail
  subjectId: string | null
}>()
const emit = defineEmits<{
  close: []
  reload: []
}>()

const card = ref<StudentCard | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const loadError = ref('')

const drafts = computed<AffairProfileDraft[]>(() => (
  // props.subjectId 为对外学生编号（稳定学籍标识）；草稿同时带内部 subject_id 与 student_ref。
  (props.affair.profile_update_drafts ?? []).filter((item) => (item.student_ref ?? item.subject_id) === props.subjectId && item.state !== 'discarded')
))
const studentName = computed(() => drafts.value[0]?.display_name || card.value?.subject.display_name || '学生')
const pendingDrafts = computed(() => drafts.value.filter((item) => item.state === 'pending'))
const confirmedDrafts = computed(() => drafts.value.filter((item) => item.state === 'confirmed'))

watch(() => (props.open && props.subjectId ? props.subjectId : null), async (subjectId) => {
  if (!subjectId) return
  loading.value = true; loadError.value = ''; error.value = ''; card.value = null
  try {
    card.value = await studentR1Api.studentCard(subjectId)
  } catch {
    loadError.value = '学生当前档案暂时无法读取。'
  } finally { loading.value = false }
}, { immediate: true })

async function confirm(draft: AffairProfileDraft): Promise<void> {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    await affairR1Api.confirmProfileDraft(props.affair.affair_id, draft.draft_id)
    emit('reload')
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) {
      error.value = '档案已在别处更新，请刷新重核。'
      emit('reload')
    } else {
      error.value = '确认写入没有完成；档案没有变化，请重试。'
    }
  } finally { busy.value = false }
}

async function discard(draft: AffairProfileDraft): Promise<void> {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    await affairR1Api.discardProfileDraft(props.affair.affair_id, draft.draft_id)
    emit('reload')
  } catch {
    error.value = '不写入操作没有完成；请重试。'
  } finally { busy.value = false }
}
</script>

<template>
  <Sheet :open="open" @update:open="(value: boolean) => { if (!value) emit('close') }">
    <SheetContent side="right" class="sop-drawer">
      <SheetHeader>
        <SheetTitle>{{ studentName }} · 学生档案</SheetTitle>
        <SheetDescription>{{ card?.subject.class_label || '班级待核对' }}</SheetDescription>
      </SheetHeader>

      <section class="drawer__block">
        <h4>当前档案</h4>
        <p v-if="loading" class="muted">正在读取…</p>
        <p v-else-if="loadError" class="error" role="alert">{{ loadError }}</p>
        <template v-else-if="card?.current_profile">
          <p>{{ card.current_profile.summary || '当前档案还没有总体摘要。' }}</p>
          <div v-for="dimension in card.current_profile.dimensions" :key="dimension.key" class="drawer__dimension">
            <b>{{ dimension.label }}</b><span>{{ dimension.items.join('；') }}</span>
          </div>
        </template>
        <p v-else class="muted">这名学生还没有已建立的当前档案。</p>
      </section>

      <section class="drawer__block">
        <h4>AI 拟写入的更新</h4>
        <p v-if="!drafts.length" class="muted">当前没有这名学生的拟写入内容。</p>
        <article v-for="draft in pendingDrafts" :key="draft.draft_id" class="draft-card">
          <header>
            <span class="draft-badge">草稿待确认</span>
          </header>
          <p>{{ draft.display_name || studentName }}：{{ draft.record_summary }}</p>
          <small v-if="draft.source">来源：{{ draft.source }}</small>
          <div class="draft-card__actions">
            <AppButton variant="primary" :disabled="busy" @click="confirm(draft)">确认写入档案</AppButton>
            <AppButton variant="ghost" :disabled="busy" @click="discard(draft)">不写入</AppButton>
          </div>
        </article>
        <article v-for="draft in confirmedDrafts" :key="draft.draft_id" class="draft-card draft-card--confirmed">
          <header><span class="draft-badge confirmed">已确认写入</span></header>
          <p>{{ draft.display_name || studentName }}：{{ draft.record_summary }}</p>
        </article>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
      </section>

      <p class="drawer__note muted">档案草稿只读展示；确认后才真正写入学生档案，不写入则草稿被丢弃。</p>
    </SheetContent>
  </Sheet>
</template>

<style scoped>
.sop-drawer{display:flex;flex-direction:column;gap:16px;overflow-y:auto;padding-bottom:24px}
.drawer__block{display:grid;gap:8px}
.drawer__block h4{margin:0;font-size:14px}
.drawer__block p{margin:0;font-size:13px;line-height:1.6}
.drawer__dimension{display:grid;gap:2px;font-size:12px}
.drawer__dimension span{color:var(--color-text-secondary)}
.draft-card{display:grid;gap:8px;padding:12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--muted)}
.draft-card--confirmed{opacity:.75}
.draft-card p{margin:0;font-size:13px}
.draft-card small{color:var(--color-text-secondary)}
.draft-card__actions{display:flex;gap:8px}
.draft-badge{padding:1px 8px;border-radius:999px;background:var(--color-warning-subtle);color:var(--color-warning);font-size:11px}
.draft-badge.confirmed{background:var(--color-success-subtle);color:var(--color-success)}
.muted{color:var(--color-text-secondary);font-size:12px}
.error{color:var(--destructive);font-size:12px}
.drawer__note{margin:0}
</style>
