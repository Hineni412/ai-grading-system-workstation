<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { intakeApi } from '../api/intake'
import { studentR1Api, type DirectorySubject } from '../api/r1'

const props = defineProps<{ token: string }>()
const emit = defineEmits<{ select: [subject: DirectorySubject] }>()
const items = ref<DirectorySubject[]>([])
const classLabel = ref('')
const loading = ref(false)
const message = ref('')
const showHistory = ref(false)
const total = ref(0)
const currentItems = computed(() => items.value.filter(item => item.roster_state !== 'historical'))

async function load(): Promise<void> {
  loading.value = true
  message.value = ''
  try {
    const preference = await intakeApi.homeroom()
    classLabel.value = preference.homeroom_class || ''
    if (!classLabel.value) {
      items.value = []
      total.value = 0
      return
    }
    const page = await studentR1Api.directory(props.token, {
      classLabel: classLabel.value,
      state: 'active',
      rosterState: showHistory.value ? 'historical' : 'active',
      sort: 'name_asc',
      pageSize: 100,
    })
    items.value = page.items
    total.value = page.total
  } catch {
    items.value = []
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

onMounted(() => { void load() })
</script>

<template>
  <section class="student-overview-list">
    <header>
      <div>
        <p>我的班主任班级</p>
        <h2>{{ classLabel || '尚未设置班级' }}</h2>
        <span v-if="classLabel">{{ total }} 名学生 · 点击卡片查看结构化概览</span>
      </div>
      <a href="/students">去学生管理更换班级</a>
    </header>

    <p v-if="message" class="student-overview-list__message" role="alert">{{ message }}</p>
    <div v-if="loading" class="student-overview-list__empty" role="status">正在读取当前班学生…</div>
    <div v-else-if="!classLabel" class="student-overview-list__empty">
      <strong>还没有设置班主任班级</strong>
      <p>先在学生管理中选择班级，设置后这里会直接显示当前班学生。</p>
      <a href="/students">去学生管理设置</a>
    </div>
    <div v-else-if="!items.length" class="student-overview-list__empty">
      <strong>{{ showHistory ? '没有历史学生档案' : '当前班级还没有学生' }}</strong>
      <p>{{ showHistory ? '返回当前班继续查看学生。' : '请先在学生管理中导入或调整学生班级。' }}</p>
    </div>
    <div v-else class="student-overview-list__grid" aria-label="当前班学生">
      <button v-for="item in items" :key="item.subject_id" type="button" @click="emit('select', item)">
        <span class="student-overview-list__rail" aria-hidden="true" />
        <strong>{{ item.display_name }}</strong>
        <small>{{ item.projection_state === 'applied' ? '结构化概览已确认' : item.confirmed_entry_count ? '有已确认记录可整理' : '还没有结构化概览' }}</small>
        <span>{{ item.attention_pending_count ? `${item.attention_pending_count} 项待跟进` : '暂无待跟进' }}</span>
      </button>
    </div>
    <footer v-if="classLabel">
      <button type="button" @click="toggleHistory">{{ showHistory ? '返回当前班学生' : '查看历史学生与旧记录' }}</button>
    </footer>
  </section>
</template>

<style scoped>
.student-overview-list{border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}header{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--space-4);padding:var(--space-5);border-bottom:1px solid var(--color-border-subtle)}header div{display:grid;gap:3px}header p,header h2{margin:0}header p{color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}header span{color:var(--color-text-secondary)}header a,.student-overview-list__empty a{color:var(--color-accent-active);font-weight:650}.student-overview-list__grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:var(--space-3);padding:var(--space-5)}.student-overview-list__grid>button{position:relative;display:grid;min-height:126px;gap:7px;padding:var(--space-4) var(--space-4) var(--space-4) calc(var(--space-4) + 5px);overflow:hidden;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);color:var(--color-text-primary);font:inherit;text-align:left}.student-overview-list__grid>button:hover,.student-overview-list__grid>button:focus-visible{border-color:var(--color-accent);box-shadow:var(--shadow-card)}.student-overview-list__grid small,.student-overview-list__grid button>span:last-child{color:var(--color-text-secondary)}.student-overview-list__rail{position:absolute;inset-block:0;inset-inline-start:0;width:4px;background:var(--color-accent)}.student-overview-list__empty{display:grid;min-height:260px;place-content:center;justify-items:center;gap:8px;padding:var(--space-5);text-align:center}.student-overview-list__empty p{margin:0;color:var(--color-text-secondary)}.student-overview-list__message{margin:0;padding:var(--space-3) var(--space-5);background:var(--color-danger-subtle);color:var(--color-danger)}footer{display:flex;justify-content:flex-end;padding:0 var(--space-5) var(--space-5)}footer button{min-height:38px;border:0;background:transparent;color:var(--color-accent-active);font:inherit}
</style>
