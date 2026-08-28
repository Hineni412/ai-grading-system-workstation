<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { intakeApi } from '../api/intake'
import { studentR1Api, type DirectorySubject, type ExistingRosterStudent } from '../api/r1'
import { supportApi } from '../api/support'
import { formatClassLabel } from '../format_class_label'
import AppButton from '@/components/design-system/AppButton.vue'
import StatusBadge from '@/components/design-system/StatusBadge.vue'

type CardBadge = { tone: 'warning' | 'info'; label: string }

const emit = defineEmits<{ select: [subject: DirectorySubject] }>()
const items = ref<DirectorySubject[]>([])
const rosterItems = ref<ExistingRosterStudent[]>([])
const openingStudentCode = ref('')
const classLabel = ref('')
const loading = ref(false)
const message = ref('')
const showHistory = ref(false)
const total = ref(0)
const subjectByStudentId = computed(() => new Map(items.value.map(item => [item.source_student_id, item])))

function subjectForRosterStudent(student: ExistingRosterStudent): DirectorySubject | undefined {
  return subjectByStudentId.value.get(student.source_key)
    || subjectByStudentId.value.get(student.student_code)
}

function cardIdentity(student: ExistingRosterStudent): string {
  const subject = subjectForRosterStudent(student)
  const classText = formatClassLabel(student.class_label)
  return subject?.source_student_id ? `学号 ${subject.source_student_id} · ${classText}` : classText
}

function cardBadges(student: ExistingRosterStudent): CardBadge[] {
  const subject = subjectForRosterStudent(student)
  if (!subject) return []
  const badges: CardBadge[] = []
  if (subject.attention_pending_count) {
    badges.push({ tone: 'warning', label: `${subject.attention_pending_count} 项待跟进` })
  }
  if (subject.support_plan_count) {
    badges.push({ tone: 'info', label: `进行中方案 ${subject.support_plan_count}` })
  }
  return badges
}

function lastConfirmedLabel(value: string | null | undefined): string {
  if (!value) return ''
  const day = new Date(value)
  if (Number.isNaN(day.getTime())) return ''
  const diff = Math.floor((Date.now() - day.getTime()) / 86400000)
  if (diff <= 0) return '今天'
  if (diff === 1) return '昨天'
  return `${diff} 天前`
}

function cardFootnote(student: ExistingRosterStudent): string {
  const subject = subjectForRosterStudent(student)
  if (!subject) return '还没有档案 · 点卡片开始建立'
  const updated = lastConfirmedLabel(subject.last_confirmed_at)
  if (updated) return `最近更新：${updated}`
  return '已建档案 · 还没有确认记录'
}

async function load(): Promise<void> {
  loading.value = true
  message.value = ''
  try {
    const preference = await intakeApi.homeroom()
    classLabel.value = preference.homeroom_class || ''
    if (!classLabel.value) {
      items.value = []
      rosterItems.value = []
      total.value = 0
      return
    }
    if (showHistory.value) {
      const page = await studentR1Api.directory({
        classLabel: classLabel.value, state: 'active', rosterState: 'historical', sort: 'name_asc', pageSize: 100,
      })
      items.value = page.items
      rosterItems.value = []
      total.value = page.total
    } else {
      const [roster, directory] = await Promise.all([
        studentR1Api.rosterSource({ classLabel: classLabel.value, pageSize: 100 }),
        studentR1Api.directory({
          classLabel: classLabel.value, state: 'active', sort: 'name_asc', pageSize: 100,
        }),
      ])
      rosterItems.value = roster.items
      items.value = directory.items
      total.value = roster.total
    }
  } catch {
    items.value = []
    rosterItems.value = []
    total.value = 0
    message.value = '学生基本信息暂时无法读取；已保存的班级和学生记录没有改变。'
  } finally {
    loading.value = false
  }
}

async function toggleHistory(): Promise<void> {
  showHistory.value = !showHistory.value
  await load()
}

async function openRosterStudent(student: ExistingRosterStudent): Promise<void> {
  const subject = student.subject_id
    ? items.value.find(item => item.subject_id === student.subject_id)
    : subjectForRosterStudent(student)
  if (subject) {
    emit('select', subject)
    return
  }
  if (openingStudentCode.value) return
  openingStudentCode.value = student.student_code
  message.value = ''
  try {
    const created = await supportApi.createSubject({
      source_student_id: student.source_key,
      display_name: student.display_name,
      class_label: student.class_label || null,
    })
    const emptyDossier: DirectorySubject = {
      ...created,
      student_ref: created.student_ref ?? created.subject_id,
      support_record_count: 0,
      support_plan_count: 0,
      confirmed_entry_count: 0,
      projection_state: 'none',
      attention_pending_count: 0,
      last_confirmed_at: null,
      roster_state: 'active',
    }
    items.value = [...items.value, emptyDossier]
    emit('select', emptyDossier)
  } catch {
    message.value = '这名学生的空白档案暂时无法打开，原有学生资料没有改变。'
  } finally {
    openingStudentCode.value = ''
  }
}

onMounted(() => { void load() })
</script>

<template>
  <section class="student-overview-list">
    <header>
      <div>
        <p>我的班主任班级</p>
        <h2>{{ classLabel ? formatClassLabel(classLabel) : '尚未设置班级' }}</h2>
        <span v-if="classLabel">{{ total }} 名学生 · 点击卡片进入学生当前档案</span>
      </div>
      <a href="/students">去学生管理更换班级</a>
    </header>

    <div v-if="message" class="student-overview-list__message" role="alert">
      <span>{{ message }}</span>
      <AppButton variant="secondary" :disabled="loading" @click="load">
        {{ loading ? '正在重新读取…' : '重新读取学生名单' }}
      </AppButton>
    </div>
    <div v-if="loading" class="student-overview-list__empty" role="status">正在读取当前班学生…</div>
    <div v-else-if="!classLabel" class="student-overview-list__empty">
      <strong>还没有设置班主任班级</strong>
      <p>先在学生管理中选择班级，设置后这里会直接显示当前班学生。</p>
      <a href="/students">去学生管理设置</a>
    </div>
    <div v-else-if="showHistory && !items.length" class="student-overview-list__empty">
      <strong>没有历史学生档案</strong><p>返回当前班继续查看学生。</p>
    </div>
    <div v-else-if="!showHistory && !rosterItems.length" class="student-overview-list__empty">
      <strong>当前班级还没有学生</strong><p>请先在学生管理中导入或调整学生班级。</p>
    </div>
    <div v-else-if="!showHistory" class="student-overview-list__grid" aria-label="当前班学生">
      <button v-for="student in rosterItems" :key="student.source_key" type="button" :disabled="openingStudentCode === student.student_code" @click="openRosterStudent(student)">
        <span class="student-overview-list__rail" aria-hidden="true" />
        <strong>{{ student.display_name }}</strong>
        <small>{{ cardIdentity(student) }}</small>
        <span v-if="cardBadges(student).length" class="student-overview-list__badges">
          <StatusBadge v-for="badge in cardBadges(student)" :key="badge.label" :tone="badge.tone" :label="badge.label" />
        </span>
        <span v-else>{{ cardFootnote(student) }}</span>
      </button>
    </div>
    <div v-else class="student-overview-list__grid" aria-label="历史学生">
      <button v-for="item in items" :key="item.subject_id" type="button" @click="emit('select', item)">
        <span class="student-overview-list__rail" aria-hidden="true" />
        <strong>{{ item.display_name }}</strong><small>历史学生记录</small><span>{{ item.confirmed_entry_count }} 条已确认记录</span>
      </button>
    </div>
    <footer v-if="classLabel"><AppButton variant="ghost" @click="toggleHistory">{{ showHistory ? '返回当前班学生' : '查看历史学生与旧记录' }}</AppButton></footer>
  </section>
</template>

<style scoped>
.student-overview-list{border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}header{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--space-4);padding:var(--space-5);border-bottom:1px solid var(--border)}header div{display:grid;gap:3px}header p,header h2{margin:0}header p{color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}header span{color:var(--color-text-secondary)}header a,.student-overview-list__empty a{color:var(--primary);font-weight:650}.student-overview-list__grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:var(--space-3);padding:var(--space-5)}.student-overview-list__grid>button{position:relative;display:grid;min-height:126px;gap:7px;justify-items:start;padding:var(--space-4) var(--space-4) var(--space-4) calc(var(--space-4) + 5px);overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);color:var(--foreground);font:inherit;text-align:left;cursor:pointer}.student-overview-list__grid>button:hover,.student-overview-list__grid>button:focus-visible{border-color:var(--primary)}.student-overview-list__grid small,.student-overview-list__grid button>span:last-child{color:var(--color-text-secondary)}.student-overview-list__badges{display:flex;flex-wrap:wrap;gap:6px}.student-overview-list__rail{position:absolute;inset-block:0;inset-inline-start:0;width:4px;background:var(--primary)}.student-overview-list__empty{display:grid;min-height:260px;place-content:center;justify-items:center;gap:8px;padding:var(--space-5);text-align:center}.student-overview-list__empty p{margin:0;color:var(--color-text-secondary)}.student-overview-list__message{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);margin:0;padding:var(--space-3) var(--space-5);background:var(--color-danger-subtle);color:var(--destructive)}footer{display:flex;justify-content:flex-end;padding:0 var(--space-5) var(--space-5)}@media(max-width:760px){.student-overview-list__message{align-items:flex-start;flex-direction:column}}
</style>
