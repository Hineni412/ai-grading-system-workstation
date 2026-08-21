<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { studentR1Api, type AcademicOverview } from '../api/r1'
import { formatClassLabel } from '../format_class_label'
import AppButton from '@/components/design-system/AppButton.vue'

const emit = defineEmits<{ select: [subjectId: string] }>()
const overview = ref<AcademicOverview | null>(null)
const failed = ref(false)

async function load(): Promise<void> {
  failed.value = false
  try {
    overview.value = await studentR1Api.academicOverview()
  } catch {
    failed.value = true
  }
}

onMounted(() => { void load() })
</script>

<template>
  <section class="academic-overview">
    <header>
      <div><p>学业证据 · 全班</p><h2>全班学业概览</h2></div>
      <span>均分只按教师确认的成绩计算；缺考不计入</span>
    </header>
    <div v-if="failed" class="empty" role="alert">
      <p>全班学业概览暂时无法读取，已登记的成绩未受影响。</p>
      <AppButton variant="secondary" @click="load">重新读取</AppButton>
    </div>
    <template v-else-if="overview">
      <div v-if="!overview.sessions.length" class="empty">
        <h3>还没有登记大考成绩</h3>
        <p>用上方上传入口登记第一场次；登记后这里会出现场次列表和班级统计。</p>
      </div>
      <template v-else>
        <section class="block">
          <h3>已登记场次</h3>
          <div v-for="session in overview.sessions" :key="session.session_id" class="session">
            <strong>{{ session.title }}</strong>
            <span>{{ session.subject_names.join('、') || '未标注学科' }}</span>
            <small>{{ session.occurred_on }} · 登记 {{ session.member_count }} 人次</small>
          </div>
        </section>
        <section v-if="overview.latest_session" class="block">
          <h3>最近一场 · {{ overview.latest_session.title }}（{{ overview.latest_session.occurred_on }}）</h3>
          <div v-for="subject in overview.latest_session.subjects" :key="subject.subject_name" class="subject-stats">
            <div class="subject-stats__head">
              <strong>{{ subject.subject_name }}</strong>
              <span>均分 {{ subject.average }} · 最高 {{ subject.maximum }} · 最低 {{ subject.minimum }} · {{ subject.count }} 人</span>
            </div>
            <div class="bands">
              <span v-for="band in subject.bands" :key="band.label" :class="{ muted: !band.count }">{{ band.label }} {{ band.count }}人</span>
            </div>
          </div>
          <p v-if="!overview.latest_session.subjects.length" class="hint">这一场还没有可统计的数值成绩。</p>
        </section>
      </template>
      <section class="block">
        <h3>未决关注卡</h3>
        <p v-if="!overview.attention_students.length" class="hint">当前没有等待教师决定的关注卡。</p>
        <button v-for="item in overview.attention_students" :key="item.subject_id" type="button" class="row" @click="emit('select', item.subject_id)">
          <strong>{{ item.display_name }}</strong>
          <span>{{ formatClassLabel(item.class_label) }}</span>
          <small>{{ item.pending_count }} 张待决定</small>
        </button>
      </section>
    </template>
  </section>
</template>

<style scoped>
.academic-overview{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--border)}
header p{margin:0 0 2px;color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.07em}
h2{margin:0;font-size:var(--font-size-h2)}
header>span{max-width:420px;color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.block{padding:var(--space-3) var(--space-5);border-bottom:1px solid var(--color-border-subtle)}
.block h3{margin:var(--space-1) 0 var(--space-2)}
.hint{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.session{display:flex;align-items:baseline;gap:var(--space-3);padding:var(--space-2) 0;border-bottom:1px solid var(--color-border-subtle)}
.session span{color:var(--color-text-secondary)}
.session small{margin-left:auto;color:var(--muted-foreground)}
.subject-stats{padding:var(--space-2) 0;border-bottom:1px solid var(--color-border-subtle)}
.subject-stats__head{display:flex;justify-content:space-between;gap:var(--space-3)}
.subject-stats__head span{color:var(--color-text-secondary)}
.bands{display:flex;flex-wrap:wrap;gap:var(--space-2);margin-top:var(--space-1)}
.bands span{padding:2px var(--space-2);border:1px solid var(--border);border-radius:var(--radius);font-size:var(--font-size-dense)}
.bands .muted{color:var(--muted-foreground)}
.row{display:flex;align-items:baseline;gap:var(--space-3);width:100%;padding:var(--space-2) 0;border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left;font:inherit;cursor:pointer}
.row:hover{background:var(--accent)}
.row span{color:var(--color-text-secondary)}
.row small{margin-left:auto;color:var(--muted-foreground)}
.empty{display:grid;place-items:center;align-content:center;gap:var(--space-2);min-height:200px;padding:var(--space-6);text-align:center}
.empty h3{margin-bottom:0}
.empty p{max-width:460px;color:var(--color-text-secondary)}
</style>
