<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { studentR1Api, type SupportOverview } from '../api/r1'
import { formatClassLabel } from '../format_class_label'
import AppButton from '@/components/design-system/AppButton.vue'

const emit = defineEmits<{ select: [subjectId: string] }>()
const overview = ref<SupportOverview | null>(null)
const failed = ref(false)

const kindLabels: Record<string, string> = {
  fact: '可核对事实',
  student_statement: '学生陈述',
  reported_statement: '转述信息',
  teacher_observation: '教师观察',
  provisional_judgment: '阶段性判断',
  professional_conclusion: '专业结论',
}
const dueSoon = computed(() => overview.value?.follow_ups.filter((item) => item.due_soon) ?? [])
const others = computed(() => overview.value?.follow_ups.filter((item) => !item.due_soon) ?? [])

function day(value: string | null): string {
  return value ? value.slice(0, 10) : ''
}

async function load(): Promise<void> {
  failed.value = false
  try {
    overview.value = await studentR1Api.supportOverview()
  } catch {
    failed.value = true
  }
}

onMounted(() => { void load() })
</script>

<template>
  <section class="support-overview">
    <header>
      <div><p>支持记录 · 全班</p><h2>全班支持总览</h2></div>
      <span>点任意学生进入这名学生的支持记录</span>
    </header>
    <div v-if="failed" class="empty" role="alert">
      <p>全班支持总览暂时无法读取，现有记录未受影响。</p>
      <AppButton variant="secondary" @click="load">重新读取</AppButton>
    </div>
    <template v-else-if="overview">
      <div v-if="!overview.has_records" class="empty">
        <h3>还没有支持记录</h3>
        <p>从首页对话记录学生情况，或先在学生目录打开学生档案，再补录支持记录。</p>
      </div>
      <template v-else>
        <section class="block">
          <h3>近期要跟进</h3>
          <p v-if="!dueSoon.length" class="hint">近期 {{ overview.review_soon_days }} 天内没有到期复查。</p>
          <button v-for="item in dueSoon" :key="item.subject_id" type="button" class="row" @click="emit('select', item.subject_id)">
            <strong>{{ item.display_name }}</strong>
            <span>{{ formatClassLabel(item.class_label) }}</span>
            <small>复查日期 {{ day(item.next_review_at) }}</small>
          </button>
        </section>
        <section v-if="others.length" class="block">
          <h3>其他有记录的学生</h3>
          <button v-for="item in others" :key="item.subject_id" type="button" class="row" @click="emit('select', item.subject_id)">
            <strong>{{ item.display_name }}</strong>
            <span>{{ formatClassLabel(item.class_label) }}</span>
            <small>{{ item.next_review_at ? `复查 ${day(item.next_review_at)}` : `最近记录 ${day(item.last_record_at) || '—'}` }} · {{ item.active_record_count }} 条</small>
          </button>
        </section>
        <section class="block">
          <h3>全班最近支持记录</h3>
          <button v-for="item in overview.recent_records" :key="item.record_id" type="button" class="row row--record" @click="emit('select', item.subject_id)">
            <span class="record-line"><strong>{{ item.display_name }}</strong><em>{{ kindLabels[item.record_kind] ?? item.record_kind }}</em><time>{{ day(item.observed_at) }}</time></span>
            <span class="excerpt">{{ item.excerpt }}</span>
          </button>
        </section>
      </template>
    </template>
  </section>
</template>

<style scoped>
.support-overview{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--border)}
header p{margin:0 0 2px;color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.07em}
h2{margin:0;font-size:var(--font-size-h2)}
header>span{max-width:420px;color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.block{padding:var(--space-3) var(--space-5);border-bottom:1px solid var(--color-border-subtle)}
.block h3{margin:var(--space-1) 0 var(--space-2)}
.hint{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.row{display:flex;align-items:baseline;gap:var(--space-3);width:100%;padding:var(--space-2) 0;border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left;font:inherit;cursor:pointer}
.row:hover{background:var(--accent)}
.row span{color:var(--color-text-secondary)}
.row small{margin-left:auto;color:var(--muted-foreground)}
.row--record{display:grid;gap:2px}
.record-line{display:flex;gap:var(--space-3);align-items:baseline}
.record-line em{font-style:normal;color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.record-line time{margin-left:auto;color:var(--muted-foreground);font-size:var(--font-size-dense)}
.excerpt{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.empty{display:grid;place-items:center;align-content:center;gap:var(--space-2);min-height:260px;padding:var(--space-6);text-align:center}
.empty h3{margin-bottom:0}
.empty p{max-width:460px;color:var(--color-text-secondary)}
</style>
