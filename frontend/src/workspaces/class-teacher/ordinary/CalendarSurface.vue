<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import type { WorkNode } from '../api/work'
import type { OrdinaryWorkModule } from './createOrdinaryWorkModule'
import WorkNodeInspector from './WorkNodeInspector.vue'

const props = defineProps<{ module: OrdinaryWorkModule }>()
const emit = defineEmits<{ openRestricted: [projectionId: string, projectionType: string | null] }>()

// 一类一色：全部使用全局设计 token；状态（逾期/完成）用标记表达，不再占用颜色。
const KIND_META: Record<WorkNode['kind'], { label: string; color: string }> = {
  goal: { label: '目标', color: 'var(--color-success)' },
  task: { label: '任务', color: 'var(--color-accent)' },
  waiting: { label: '等待', color: 'var(--color-warning)' },
  decision: { label: '决策', color: 'var(--color-ai)' },
  collection: { label: '收缴', color: 'var(--color-info)' },
  communication: { label: '沟通', color: 'var(--color-teacher)' },
  sop: { label: '例行', color: 'var(--color-text-muted)' },
  restricted_projection: { label: '学生事项', color: 'var(--color-danger)' },
}
const STATUS_LABEL: Record<WorkNode['status'], string> = {
  draft: '草稿', pending: '待办', in_progress: '进行中',
  waiting: '等待中', completed: '已完成', cancelled: '已取消',
}
const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const MAX_BARS = 2 // 每格最多平铺条数，超出折叠为「+N 更多」

const todayISO = new Date().toISOString().slice(0, 10)
const monthAnchor = ref(`${todayISO.slice(0, 7)}-01`)
const openDay = ref<string | null>(null)

function parseDay(value: string) { return new Date(`${value}T12:00:00`) }
function isoDay(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

const monthLabel = computed(() => {
  const first = parseDay(monthAnchor.value)
  return `${first.getFullYear()} 年 ${first.getMonth() + 1} 月`
})
const isThisMonth = computed(() => monthAnchor.value === `${todayISO.slice(0, 7)}-01`)

// 月历网格：从含 1 日的周一起，到含月末的周日止，补位格显示相邻月份。
const cells = computed(() => {
  const first = parseDay(monthAnchor.value)
  const year = first.getFullYear()
  const month = first.getMonth()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const lead = (first.getDay() + 6) % 7
  const total = Math.ceil((lead + daysInMonth) / 7) * 7
  const start = new Date(year, month, 1 - lead, 12)
  return Array.from({ length: total }, (_, index) => {
    const date = new Date(start)
    date.setDate(start.getDate() + index)
    return { date: isoDay(date), day: date.getDate(), inMonth: date.getMonth() === month, column: index % 7 }
  })
})

// 已移出日历（cancelled）的节点不出现在月历上；历史仍保留在详情与事务案头。
const nodesByDate = computed(() => {
  const map = new Map<string, WorkNode[]>()
  for (const node of props.module.snapshot.value?.nodes ?? []) {
    if (!node.due_date || node.status === 'cancelled') continue
    const list = map.get(node.due_date) ?? []
    list.push(node)
    map.set(node.due_date, list)
  }
  return map
})
const nodesOn = (date: string): WorkNode[] => nodesByDate.value.get(date) ?? []
const isOverdue = (node: WorkNode) => Boolean(node.due_date && node.due_date < todayISO && !['completed', 'cancelled'].includes(node.status))
const cellHasOverdue = (date: string) => nodesOn(date).some(isOverdue)

function moveMonth(step: number) {
  const first = parseDay(monthAnchor.value)
  monthAnchor.value = isoDay(new Date(first.getFullYear(), first.getMonth() + step, 1, 12))
  openDay.value = null
}
function backToThisMonth() {
  monthAnchor.value = `${todayISO.slice(0, 7)}-01`
  openDay.value = null
}
function toggleDay(date: string) { openDay.value = openDay.value === date ? null : date }
function inspectNode(node: WorkNode) {
  openDay.value = null
  void props.module.inspect(node)
}
function forwardRestricted(projectionId: string, projectionType: string | null) { emit('openRestricted', projectionId, projectionType) }

onMounted(() => { void props.module.load('all') })
</script>

<template>
  <section class="calendar-surface">
    <header>
      <div>
        <p>日历</p>
        <h2>{{ monthLabel }}</h2>
      </div>
      <div class="controls">
        <nav aria-label="切换月份">
          <button type="button" @click="moveMonth(-1)">上一月</button>
          <button type="button" :disabled="isThisMonth" @click="backToThisMonth">本月</button>
          <button type="button" @click="moveMonth(1)">下一月</button>
        </nav>
        <p class="legend" aria-label="工作类型颜色说明">
          <span v-for="(meta, kind) in KIND_META" :key="kind"><i :style="{ background: meta.color }"></i>{{ meta.label }}</span>
        </p>
      </div>
    </header>
    <p v-if="module.error.value" class="load-error" role="alert">{{ module.error.value }}</p>
    <div class="calendar-layout">
      <div class="month" role="grid" aria-label="一个月的工作">
        <div v-for="weekday in WEEKDAYS" :key="weekday" class="dow" role="columnheader">{{ weekday }}</div>
        <section
          v-for="cell in cells"
          :key="cell.date"
          role="gridcell"
          class="cell"
          :class="{ dim: !cell.inMonth, today: cell.date === todayISO, 'has-overdue': cellHasOverdue(cell.date) }"
          :aria-label="cell.date"
        >
          <h3><time>{{ cell.day }}</time></h3>
          <button
            v-for="node in nodesOn(cell.date).slice(0, MAX_BARS)"
            :key="node.node_id"
            type="button"
            class="bar"
            :class="{ done: node.status === 'completed', rst: node.classification === 'restricted_projection' }"
            :style="{ '--kc': KIND_META[node.kind].color }"
            :title="node.title"
            @click="inspectNode(node)"
          ><span>{{ node.classification === 'restricted_projection' ? '🔒 ' : '' }}{{ isOverdue(node) ? '⚠ ' : '' }}{{ node.title }}</span></button>
          <button
            v-if="nodesOn(cell.date).length > MAX_BARS"
            type="button"
            class="more"
            :aria-expanded="openDay === cell.date"
            @click="toggleDay(cell.date)"
          >+{{ nodesOn(cell.date).length - MAX_BARS }} 更多</button>
          <div v-if="openDay === cell.date" class="day-pop" :class="{ edge: cell.column >= 5 }" role="dialog" :aria-label="`${cell.date} 的全部工作`">
            <h4>{{ cell.date.slice(5).replace('-', '月') }}日 {{ WEEKDAYS[cell.column] }}<button type="button" aria-label="关闭" @click="toggleDay(cell.date)">×</button></h4>
            <button
              v-for="node in nodesOn(cell.date)"
              :key="node.node_id"
              type="button"
              class="bar"
              :class="{ done: node.status === 'completed', rst: node.classification === 'restricted_projection' }"
              :style="{ '--kc': KIND_META[node.kind].color }"
              @click="inspectNode(node)"
            ><span>{{ node.classification === 'restricted_projection' ? '🔒 ' : '' }}{{ isOverdue(node) ? '⚠ ' : '' }}{{ node.title }}</span><small>{{ STATUS_LABEL[node.status] }}</small></button>
          </div>
        </section>
      </div>
      <WorkNodeInspector v-if="module.selected.value" :module="module" @open-restricted="forwardRestricted" />
      <aside v-else class="todo" aria-label="待办清单">
        <header><strong>待办清单</strong></header>
        <div class="todo-groups">
          <section v-if="module.snapshot.value?.overdue.length" class="group overdue" aria-label="已逾期">
            <header>已逾期<span>{{ module.snapshot.value.overdue.length }} 项</span></header>
            <button v-for="node in module.snapshot.value.overdue" :key="node.node_id" type="button" class="item" @click="inspectNode(node)">
              <i :style="{ background: KIND_META[node.kind].color }"></i>
              <span class="t">{{ node.classification === 'restricted_projection' ? '🔒 ' : '' }}{{ node.title }}</span>
              <small>原到期 {{ node.due_date?.slice(5).replace('-', '/') }}</small>
            </button>
          </section>
          <section v-if="module.snapshot.value?.today.length" class="group today-group" aria-label="今天要办">
            <header>今天要办<span>{{ module.snapshot.value.today.length }} 项</span></header>
            <button v-for="node in module.snapshot.value.today" :key="node.node_id" type="button" class="item" @click="inspectNode(node)">
              <i :style="{ background: KIND_META[node.kind].color }"></i>
              <span class="t">{{ node.classification === 'restricted_projection' ? '🔒 ' : '' }}{{ node.title }}</span>
              <small>{{ STATUS_LABEL[node.status] }}</small>
            </button>
          </section>
          <section v-if="module.snapshot.value?.waiting.length" class="group waiting-group" aria-label="等待他人">
            <header>等待他人<span>{{ module.snapshot.value.waiting.length }} 项</span></header>
            <button v-for="node in module.snapshot.value.waiting" :key="node.node_id" type="button" class="item" @click="inspectNode(node)">
              <i :style="{ background: KIND_META[node.kind].color }"></i>
              <span class="t">{{ node.classification === 'restricted_projection' ? '🔒 ' : '' }}{{ node.title }}</span>
              <small>{{ STATUS_LABEL[node.status] }}</small>
            </button>
          </section>
          <p v-if="module.snapshot.value && !module.snapshot.value.overdue.length && !module.snapshot.value.today.length && !module.snapshot.value.waiting.length" class="todo-empty">
            当前没有逾期、今天到期或等待中的工作。
          </p>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.calendar-surface { overflow: visible; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.calendar-surface > header { display: flex; justify-content: space-between; align-items: end; gap: var(--space-4); padding: var(--space-5); border-bottom: 1px solid var(--border); }
.calendar-surface header p { margin: 0 0 2px; color: var(--primary); font-size: var(--font-size-caption); font-weight: 700; letter-spacing: .08em; }
.calendar-surface h2 { margin: 0; font-size: var(--font-size-h2); }
.controls { display: grid; gap: var(--space-2); justify-items: end; }
nav { display: flex; gap: var(--space-1); }
nav button { min-height: 34px; padding-inline: var(--space-3); border: 1px solid var(--border); background: var(--card); border-radius: var(--radius); color: var(--foreground); cursor: pointer; }
nav button:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); }
nav button:disabled { opacity: var(--opacity-disabled); cursor: default; }
.legend { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 2px var(--space-3); margin: 0; font-size: var(--font-size-caption); color: var(--muted-foreground); letter-spacing: 0; }
.legend i { display: inline-block; width: 8px; height: 8px; border-radius: var(--radius-circle); margin-right: var(--space-1); vertical-align: 1px; }
.load-error { margin: var(--space-3) var(--space-5) 0; color: var(--color-danger); }
.calendar-layout { display: grid; grid-template-columns: minmax(0, 1fr) var(--shell-inspector-width); align-items: start; }

.month { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
.dow { padding: var(--space-2) var(--space-3); font-size: var(--font-size-caption); font-weight: 700; color: var(--muted-foreground); background: var(--muted); border-right: 1px solid var(--color-border-subtle); }
.dow:last-of-type { border-right: 0; }
.cell { position: relative; min-height: 108px; padding: var(--space-2); border-right: 1px solid var(--color-border-subtle); border-top: 1px solid var(--color-border-subtle); display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.cell:nth-child(7n) { border-right: 0; }
.cell.dim { background: var(--muted); color: var(--muted-foreground); }
.cell.today { background: var(--accent); }
.cell h3 { margin: 0 0 2px; }
.cell time { display: grid; place-items: center; width: 24px; height: 24px; border-radius: var(--radius-circle); font-size: var(--font-size-dense); font-weight: 400; }
.cell.today time { background: var(--primary); color: var(--card); font-weight: 700; }
.cell.has-overdue time { color: var(--color-danger); font-weight: 700; }
.cell.today.has-overdue time { color: var(--card); }
.cell.dim time { color: var(--muted-foreground); }

.bar { display: flex; align-items: center; gap: var(--space-1); width: 100%; min-height: 22px; padding: 1px var(--space-2); border: 0; border-radius: var(--radius-tag); background: var(--kc); color: var(--card); font-size: var(--font-size-caption); line-height: 1.35; text-align: left; cursor: pointer; }
.bar:hover { filter: brightness(1.08); }
.bar span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar.done { opacity: .5; }
.bar.done span { text-decoration: line-through; }
.bar.rst { background: var(--color-danger-subtle); color: var(--color-danger); border: 1px solid var(--color-danger); }
.bar small { flex: none; opacity: .85; }

.more { width: 100%; padding: 2px var(--space-2); border: 0; border-radius: var(--radius-tag); background: none; color: var(--primary); font-size: var(--font-size-caption); font-weight: 600; text-align: left; cursor: pointer; }
.more:hover { background: var(--accent); }

.day-pop { position: absolute; z-index: 20; top: 34px; left: var(--space-2); width: 240px; display: flex; flex-direction: column; gap: var(--space-2); padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); box-shadow: var(--shadow-overlay); }
.day-pop.edge { left: auto; right: var(--space-2); }
.day-pop h4 { display: flex; justify-content: space-between; align-items: center; margin: 0; font-size: var(--font-size-dense); }
.day-pop h4 button { border: 0; background: none; color: var(--muted-foreground); font-size: var(--font-size-h3); line-height: 1; cursor: pointer; }

.todo { min-height: 460px; border-left: 1px solid var(--border); background: var(--muted); }
.todo > header { padding: var(--space-4) var(--space-4) var(--space-2); }
.todo > header strong { font-size: var(--font-size-dense); }
.todo > header p { margin: 2px 0 0; font-size: var(--font-size-caption); color: var(--muted-foreground); }
.todo-groups { display: flex; flex-direction: column; gap: var(--space-3); padding: var(--space-2) var(--space-3) var(--space-4); }
.group { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); overflow: hidden; }
.group > header { display: flex; justify-content: space-between; align-items: center; padding: var(--space-2) var(--space-3); font-size: var(--font-size-caption); font-weight: 700; }
.group > header span { font-weight: 400; color: var(--muted-foreground); }
.group.overdue > header { color: var(--color-danger); background: var(--color-danger-subtle); }
.group.today-group > header { color: var(--primary); background: var(--accent); }
.group.waiting-group > header { color: var(--color-warning); background: var(--color-warning-subtle); }
.item { display: flex; align-items: center; gap: var(--space-2); width: 100%; padding: var(--space-2) var(--space-3); border: 0; border-top: 1px solid var(--color-border-subtle); background: none; font-size: var(--font-size-dense); text-align: left; cursor: pointer; }
.item:hover { background: var(--accent); }
.item i { flex: none; width: 8px; height: 8px; border-radius: var(--radius-circle); }
.item .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item small { flex: none; color: var(--muted-foreground); }
.group.overdue .item small { color: var(--color-danger); }
.todo-empty { margin: 0; padding: var(--space-4) var(--space-2); color: var(--muted-foreground); font-size: var(--font-size-dense); text-align: center; }

@media (max-width: 1100px) {
  .calendar-layout { grid-template-columns: 1fr; }
  .todo { border-left: 0; border-top: 1px solid var(--border); }
}
</style>
