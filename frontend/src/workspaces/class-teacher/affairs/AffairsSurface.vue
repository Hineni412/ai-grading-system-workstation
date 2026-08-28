<script setup lang="ts">
import { onMounted, ref } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'
import StatusBadge from '@/components/design-system/StatusBadge.vue'

import { affairR1Api, type AffairSummary } from '../api/r1'

const emit = defineEmits<{ open: [affairId: string] }>()

const items = ref<AffairSummary[]>([])
const busy = ref(false)
const error = ref('')
const baselines = ['学生安全与紧急处置','欺凌线索核实','家校沟通','纪律与教育支持','缺勤与返校','阶段性关怀']

function stateTone(state: string): { tone: 'info' | 'success' | 'danger'; label: string } {
  if (state === 'discarded') return { tone: 'danger', label: '已弃用' }
  if (state === 'closed') return { tone: 'success', label: '已结案' }
  return { tone: 'info', label: '进行中' }
}

async function load() {
  busy.value = true; error.value = ''
  try {
    items.value = await affairR1Api.list()
  } catch { error.value = '事务列表暂时无法读取。' } finally { busy.value = false }
}

onMounted(() => { void load() })
</script>

<template>
  <section class="affairs">
    <header><div><p>连续事务</p><h2>每件事只沿一条流程推进</h2></div><AppButton variant="secondary" @click="load">刷新</AppButton></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <nav aria-label="事务列表" class="affair-list">
      <strong v-if="items.length" class="list-heading">已保存事务 · {{ items.length }}</strong>
      <button v-for="item in items" :key="item.affair_id" type="button" @click="emit('open', item.affair_id)">
        <i aria-hidden="true"></i>
        <span>
          <strong>{{ item.title }}</strong>
          <small>{{ item.current_step_count }} 个当前步骤 · {{ item.completed_step_count }} 个已完成</small>
        </span>
        <StatusBadge class="affair-state" :tone="stateTone(item.state).tone" :label="stateTone(item.state).label" />
      </button>
      <div v-if="!items.length && !busy" class="baselines"><strong>当前没有事务</strong><p>学校流程基线仍可查看：</p><span v-for="baseline in baselines" :key="baseline">{{ baseline }}</span></div>
    </nav>
  </section>
</template>

<style scoped>
.affairs{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--border)}
header p{margin:0 0 2px;color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}
h2{margin:0;font-size:var(--font-size-h2)}
.affair-list{display:grid;padding:var(--space-3)}
.affair-list>button{display:grid;grid-template-columns:4px 1fr auto;gap:var(--space-2);align-items:center;width:100%;padding:var(--space-3);border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left;cursor:pointer;font:inherit}
.affair-list>button:hover{background:var(--accent)}
.affair-list i{align-self:stretch;background:var(--primary);border-radius:2px}
.affair-list span{display:grid;gap:3px}
.affair-list small{color:var(--color-text-secondary)}
.affair-state{justify-self:end}
.baselines{display:grid;gap:var(--space-1);padding:var(--space-3)}
.baselines span{padding:var(--space-1);border-bottom:1px solid var(--color-border-subtle);font-size:var(--font-size-dense)}
.list-heading{display:block;padding:var(--space-1) var(--space-2);color:var(--color-text-secondary);font-size:var(--font-size-caption)}
.error{padding:var(--space-3);color:var(--destructive)}
.affair-list>button:focus-visible{outline:2px solid var(--ring);outline-offset:1px}
</style>
