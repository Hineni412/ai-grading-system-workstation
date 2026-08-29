<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import type { WorkNode, WorkView } from '../api/work'
import type { OrdinaryWorkModule } from './createOrdinaryWorkModule'
import WorkNodeInspector from './WorkNodeInspector.vue'

const props = defineProps<{ module: OrdinaryWorkModule }>()
const emit = defineEmits<{ openRestricted: [projectionId: string, projectionType: string | null] }>()
const anchor = ref(new Date().toISOString().slice(0, 10))
const mode = ref<WorkView>('week')
const dates = computed(() => {
  const start = new Date(`${anchor.value}T12:00:00`)
  const weekday = (start.getDay() + 6) % 7
  start.setDate(start.getDate() - weekday)
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date(start); date.setDate(start.getDate() + index)
    return date.toISOString().slice(0, 10)
  })
})
const nodesFor = (date: string): WorkNode[] => props.module.snapshot.value?.nodes.filter((node) => node.due_date === date) ?? []
function move(days: number) { const value = days === 0 ? new Date() : new Date(`${anchor.value}T12:00:00`); if (days) value.setDate(value.getDate() + days); anchor.value = value.toISOString().slice(0, 10); void props.module.load(mode.value, anchor.value) }
function selectMode(value: WorkView) { mode.value = value; void props.module.load(value, anchor.value) }
function nodeTitle(nodeId: string) { return props.module.snapshot.value?.nodes.find((item) => item.node_id === nodeId)?.title ?? '已变化的工作' }
function forwardRestricted(projectionId: string, projectionType: string | null) { emit('openRestricted', projectionId, projectionType) }
onMounted(() => { void props.module.load('week', anchor.value) })
</script>

<template>
  <section class="calendar-surface">
    <header><div><p>日历与唯一工作图</p><h2>把时间、包含、依赖和接续放在一起看</h2></div><div class="controls"><nav aria-label="查看范围"><button v-for="value in (['week','all'] as const)" :key="value" :aria-current="mode===value?'page':undefined" @click="selectMode(value)">{{ {week:'本周',all:'全部'}[value] }}</button></nav><nav v-if="mode==='week'" aria-label="切换周"><button @click="move(-7)">上一周</button><button @click="move(0)">本周</button><button @click="move(7)">下一周</button></nav></div></header>
    <div class="calendar-layout">
      <div class="canvas">
      <div v-if="mode==='week'" class="week" role="grid" aria-label="一周工作">
        <section v-for="date in dates" :key="date" role="gridcell" :class="{ today: date === new Date().toISOString().slice(0,10) }">
          <h3><span>{{ ['周一','周二','周三','周四','周五','周六','周日'][dates.indexOf(date)] }}</span><time>{{ date.slice(5) }}</time></h3>
          <button v-for="node in nodesFor(date)" :key="node.node_id" type="button" :data-kind="node.classification" @click="module.inspect(node)"><i aria-hidden="true"></i><span>{{ node.title }}</span><small>{{ node.status }}</small></button>
          <p v-if="!nodesFor(date).length">—</p>
        </section>
      </div>
      <section class="relations" aria-label="工作关系图">
        <header><strong>工作关系</strong><span>只显示同一 WorkGraph 中的正式节点</span></header>
        <div class="node-strip">
          <button v-for="node in module.snapshot.value?.nodes" :key="node.node_id" type="button" :data-kind="node.classification" @click="module.inspect(node)"><i></i><span>{{ node.title }}</span><small>{{ node.due_date || '未定日期' }} · {{ node.status }}</small></button>
        </div>
        <ul v-if="module.snapshot.value?.edges.length"><li v-for="edge in module.snapshot.value.edges" :key="`${edge.source_node_id}-${edge.relation}-${edge.target_node_id}`"><span>{{ nodeTitle(edge.source_node_id) }}</span><strong>{{ {contains:'包含',depends_on:'依赖',next:'接续',review_of:'复查'}[edge.relation] }}</strong><span>{{ nodeTitle(edge.target_node_id) }}</span></li></ul>
        <p v-else>{{ module.snapshot.value?.nodes.length ? '当前节点之间没有已确认的关系。' : (mode==='all' ? '整个工作图为空。' : '当前筛选范围没有工作。') }}</p>
      </section>
      </div>
      <WorkNodeInspector :module="module" @open-restricted="forwardRestricted" />
    </div>
  </section>
</template>

<style scoped>
.calendar-surface { overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }.calendar-surface>header { display:flex; justify-content:space-between; align-items:end; padding:var(--space-5); border-bottom:1px solid var(--border); }.calendar-surface header p { margin:0 0 2px; color:var(--primary); font-size:var(--font-size-caption); font-weight:700; letter-spacing:.08em; }.calendar-surface h2 { margin:0; font-size:var(--font-size-h2); }.controls{display:grid;gap:var(--space-1)}nav { display:flex;justify-content:flex-end;gap:var(--space-1); }nav button { min-height:36px; padding-inline:var(--space-2);border:1px solid var(--border); background:var(--card); border-radius:var(--radius); color:var(--foreground); cursor:pointer; }nav button:hover{background:var(--accent)}nav button[aria-current="page"]{background:var(--accent);color:var(--primary);font-weight:600;border-color:var(--primary)}.calendar-layout{display:grid;grid-template-columns:minmax(0,1fr) 380px}.canvas{min-width:0}.week{display:grid;grid-template-columns:repeat(7,minmax(110px,1fr));min-height:380px;overflow:auto}.week section{padding:var(--space-3);border-right:1px solid var(--color-border-subtle)}.week section.today{background:var(--accent)}.week h3{display:flex;justify-content:space-between;margin:0 0 var(--space-3);font-size:var(--font-size-dense)}.week h3 time{color:var(--muted-foreground);font-weight:400}.week button{display:grid;grid-template-columns:4px 1fr;gap:var(--space-2);width:100%;margin-bottom:var(--space-2);padding:var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card);text-align:left;cursor:pointer}.week button:hover{border-color:var(--primary)}.week button i{grid-row:1/3;background:var(--primary);border-radius:2px}.week button[data-kind="restricted_projection"] i{background:var(--color-warning)}.week button small{color:var(--muted-foreground)}.week section>p{color:var(--muted-foreground)}.relations{padding:var(--space-4);border-top:1px solid var(--border);background:var(--muted)}.relations>header{display:flex;justify-content:space-between}.relations header span,.relations>p{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.node-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:var(--space-2);margin-top:var(--space-3)}.node-strip button{display:grid;grid-template-columns:4px 1fr;gap:4px var(--space-2);padding:var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card);text-align:left;cursor:pointer}.node-strip button:hover{border-color:var(--primary)}.node-strip i{grid-row:1/3;background:var(--primary);border-radius:2px}.node-strip button[data-kind="restricted_projection"] i{background:var(--color-warning)}.node-strip small{color:var(--muted-foreground)}.relations ul{display:grid;gap:var(--space-1);padding:0;list-style:none}.relations li{display:grid;grid-template-columns:1fr auto 1fr;gap:var(--space-2);padding:var(--space-2);border-bottom:1px solid var(--color-border-subtle)}.relations li strong{color:var(--primary)}
@media(max-width:1100px){.calendar-layout{grid-template-columns:1fr}.week{grid-template-columns:repeat(7,minmax(150px,1fr))}}
</style>
