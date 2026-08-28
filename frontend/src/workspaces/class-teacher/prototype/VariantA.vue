// PROTOTYPE — throwaway, ?variant= 切换，mock 数据，验收后删除
<script setup lang="ts">
import { computed, ref } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'

import { kindLabel, statusLabel, type SopPrototype } from './mock'
import PrototypeAside from './PrototypeAside.vue'

const props = defineProps<{ proto: SopPrototype }>()

const selectedId = ref('1')
const drawerStudent = ref('')
const selected = computed(() => props.proto.stepBy(selectedId.value))
const drawerStudentData = computed(() => props.proto.state.students.find((item) => item.name === drawerStudent.value) ?? null)

function selectNode(id: string): void {
  selectedId.value = id
  drawerStudent.value = ''
}

function openDrawer(name: string): void {
  drawerStudent.value = name
}

// —— SVG 流程图手写布局（mock 结构固定，7 节点）——
interface FlowNode {
  id: string
  cx: number
  cy: number
  w: number
  h: number
  shape: 'rect' | 'diamond'
}

const nodeW = 190
const nodeH = 64
const nodeLayout: FlowNode[] = [
  { id: '1', cx: 360, cy: 60, w: nodeW, h: nodeH, shape: 'rect' },
  { id: '2', cx: 360, cy: 160, w: nodeW, h: nodeH, shape: 'rect' },
  { id: '3', cx: 360, cy: 260, w: nodeW, h: nodeH, shape: 'rect' },
  { id: '4', cx: 360, cy: 375, w: 210, h: 96, shape: 'diamond' },
  { id: '5a', cx: 175, cy: 510, w: nodeW, h: nodeH, shape: 'rect' },
  { id: '5b', cx: 545, cy: 510, w: nodeW, h: nodeH, shape: 'rect' },
  { id: '6', cx: 360, cy: 640, w: nodeW, h: nodeH, shape: 'rect' },
  { id: '7', cx: 360, cy: 735, w: nodeW, h: nodeH, shape: 'rect' },
]

interface FlowEdge {
  d: string
  label?: string
  lx?: number
  ly?: number
  step?: string
}

const edges: FlowEdge[] = [
  { d: 'M360,92 V128' },
  { d: 'M360,192 V228' },
  { d: 'M360,292 V327' },
  { d: 'M255,375 H175 V478', label: '普通调解', lx: 168, ly: 363, step: '5a' },
  { d: 'M465,375 H545 V478', label: '欺凌上报', lx: 552, ly: 363, step: '5b' },
  { d: 'M360,423 V608', label: '先与家长沟通', lx: 370, ly: 512 },
  { d: 'M175,542 V585 H330 V608', step: '5a' },
  { d: 'M545,542 V585 H390 V608', step: '5b' },
  { d: 'M360,672 V703' },
]

function diamondPoints(node: FlowNode): string {
  return `${node.cx - node.w / 2},${node.cy} ${node.cx},${node.cy - node.h / 2} ${node.cx + node.w / 2},${node.cy} ${node.cx},${node.cy + node.h / 2}`
}

function wrapTitle(title: string, per = 12): string[] {
  const lines: string[] = []
  for (let index = 0; index < title.length; index += per) lines.push(title.slice(index, index + per))
  return lines.slice(0, 2)
}

function edgeClass(edge: FlowEdge): string {
  if (!edge.step) return ''
  return `edge--${props.proto.stepBy(edge.step).status}`
}
</script>

<template>
  <div class="variant-a">
    <PrototypeAside :proto="proto" layout="rail" student-click="drawer" @open-student="openDrawer" />

    <section class="flow" aria-label="SOP 流程图">
      <svg viewBox="0 0 720 790" class="flow__svg" role="img" aria-label="SOP 流程图：确认安全、记录、判断分流、分支处理、家长沟通、复查">
        <defs>
          <marker id="proto-arrow" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto" markerUnits="userSpaceOnUse">
            <path d="M0,0 L9,4.5 L0,9 z" class="arrowhead" />
          </marker>
        </defs>

        <g v-for="edge in edges" :key="edge.d" class="edge" :class="edgeClass(edge)">
          <path :d="edge.d" class="edge__line" marker-end="url(#proto-arrow)" />
          <text v-if="edge.label" :x="edge.lx" :y="edge.ly" class="edge__label" :text-anchor="(edge.lx ?? 0) > 400 ? 'start' : 'end'">{{ edge.label }}</text>
        </g>

        <g
          v-for="node in nodeLayout"
          :key="node.id"
          class="flow-node"
          :class="[`is-${proto.stepBy(node.id).status}`, { selected: selectedId === node.id && !drawerStudent }]"
          @click="selectNode(node.id)"
        >
          <rect
            v-if="selectedId === node.id && !drawerStudent"
            :x="node.cx - node.w / 2 - 6"
            :y="node.cy - node.h / 2 - 6"
            :width="node.w + 12"
            :height="node.h + 12"
            rx="12"
            class="node-ring"
          />
          <rect
            v-if="node.shape === 'rect'"
            :x="node.cx - node.w / 2"
            :y="node.cy - node.h / 2"
            :width="node.w"
            :height="node.h"
            rx="8"
            class="node-box"
          />
          <polygon v-else :points="diamondPoints(node)" class="node-box" />
          <text :x="node.cx" :y="node.cy - 14" class="node-kind" :class="`node-kind--${proto.stepBy(node.id).kind}`">{{ kindLabel[proto.stepBy(node.id).kind] }}</text>
          <text
            v-for="(line, lineIndex) in wrapTitle(proto.stepBy(node.id).title)"
            :key="lineIndex"
            :x="node.cx"
            :y="node.cy + 5 + lineIndex * 15"
            class="node-title"
          >{{ line }}</text>
          <g v-if="proto.stepBy(node.id).status === 'completed'" class="node-check">
            <circle :cx="node.cx + node.w / 2" :cy="node.cy - node.h / 2" r="9" />
            <text :x="node.cx + node.w / 2" :y="node.cy - node.h / 2 + 4">✓</text>
          </g>
          <text v-if="proto.stepBy(node.id).aiUpdated" :x="node.cx + node.w / 2 - 6" :y="node.cy + node.h / 2 - 5" class="node-ai">AI</text>
        </g>
      </svg>
      <p class="flow__legend">点击节点查看/处理该步骤；菱形为判断分支，连线文字为分支去向。</p>
    </section>

    <aside class="panel" aria-label="详情面板">
      <template v-if="drawerStudentData">
        <header class="panel__head">
          <h3>{{ drawerStudentData.name }} · 学生档案</h3>
          <button type="button" class="panel__close" aria-label="关闭" @click="drawerStudent = ''">×</button>
        </header>
        <p class="drawer__class">{{ drawerStudentData.classLabel }}</p>
        <section class="drawer__block">
          <h4>当前档案</h4>
          <p>{{ drawerStudentData.summary }}</p>
        </section>
        <section class="drawer__block drawer__update">
          <h4>
            AI 拟写入的更新
            <span class="draft-badge" :class="{ confirmed: drawerStudentData.confirmed }">{{ drawerStudentData.confirmed ? '已确认写入' : '草稿待确认' }}</span>
          </h4>
          <p>{{ drawerStudentData.name }}：{{ drawerStudentData.line }}</p>
          <AppButton variant="primary" :disabled="drawerStudentData.confirmed" @click="proto.confirmStudentWrite(drawerStudentData.name)">
            {{ drawerStudentData.confirmed ? '已确认' : '确认写入档案' }}
          </AppButton>
        </section>
        <p class="drawer__note">档案草稿只读展示；确认动作在这里完成，不需要单独的"确认建立"步骤。（原型演示，仅内存态）</p>
      </template>

      <template v-else-if="selected">
        <header class="panel__head">
          <h3>步骤检查器</h3>
          <button type="button" class="panel__close" aria-label="关闭" @click="selectedId = ''">×</button>
        </header>
        <header class="inspector__head">
          <span class="kind" :data-kind="selected.kind">{{ kindLabel[selected.kind] }}</span>
          <span class="status" :data-status="selected.status">{{ statusLabel[selected.status] }}</span>
        </header>
        <h3 class="inspector__title">{{ selected.title }}</h3>
        <p v-if="selected.aiUpdated" class="ai-note">{{ selected.aiNote }}</p>
        <p v-if="selected.status === 'locked'" class="locked-note">前置步骤未完成，暂不能处理。</p>
        <p v-else-if="selected.status === 'pruned'" class="locked-note">该分支已在分流判断中被剪枝。</p>

        <template v-if="selected.kind === 'branch'">
          <div class="branch-options">
            <AppButton
              v-for="option in proto.state.branchOptions"
              :key="option.key"
              :variant="selected.chosenOption === option.key ? 'primary' : 'secondary'"
              :disabled="!proto.canChooseBranch()"
              @click="proto.chooseBranch(option.key)"
            >
              {{ option.label }}
            </AppButton>
          </div>
        </template>

        <template v-if="selected.status !== 'pruned'">
          <label class="record-input">
            <span>处理记录</span>
            <textarea v-model="selected.recordDraft" rows="4" placeholder="记录这一步实际做了什么、学生怎么回应"></textarea>
          </label>
          <div class="inspector__actions">
            <AppButton variant="secondary" :disabled="!selected.recordDraft.trim()" @click="proto.saveRecord(selected.id)">保存记录</AppButton>
            <AppButton v-if="selected.kind !== 'branch'" variant="primary" :disabled="!proto.canComplete(selected)" @click="proto.completeStep(selected.id)">标记完成</AppButton>
          </div>
          <p v-if="selected.record" class="record">已记录：{{ selected.record }}</p>
        </template>
      </template>

      <p v-else class="panel__empty">点击流程图节点查看步骤详情；点击左侧学生姓名查看档案草稿。</p>
    </aside>
  </div>
</template>

<style scoped>
.variant-a{display:grid;grid-template-columns:250px minmax(0,1fr) 300px;gap:16px;align-items:start}
.flow{padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);overflow-x:auto}
.flow__svg{display:block;width:100%;max-width:760px;min-width:560px;margin:0 auto;height:auto}
.flow__legend{margin:8px 0 0;font-size:12px;color:var(--muted-foreground);text-align:center}
.edge__line{fill:none;stroke:#9a94c4;stroke-width:1.5}
.edge--pruned{opacity:.35}
.edge--pruned .edge__line{stroke-dasharray:5 4}
.edge--completed .edge__line{stroke:#16a34a}
.arrowhead{fill:#9a94c4}
.edge--completed .arrowhead{fill:#16a34a}
.edge__label{font-size:11px;fill:#6b6794;paint-order:stroke;stroke:var(--card);stroke-width:4}
.flow-node{cursor:pointer}
.node-box{fill:#ececf8;stroke:#9a94c4;stroke-width:1.5}
.flow-node.is-in_progress .node-box{stroke:#4f46e5;stroke-width:2.5}
.flow-node.is-completed .node-box{stroke:#16a34a;fill:#eaf6ee}
.flow-node.is-locked{opacity:.5}
.flow-node.is-pruned{opacity:.45}
.flow-node.is-pruned .node-title{text-decoration:line-through}
.node-ring{fill:none;stroke:var(--ring,#4f46e5);stroke-width:2;stroke-dasharray:5 4}
.node-kind{font-size:10px;text-anchor:middle;fill:#8a87a8}
.node-kind--safety{fill:#dc2626}
.node-kind--branch{fill:#b45309}
.node-kind--communicate{fill:#2563eb}
.node-title{font-size:12.5px;text-anchor:middle;fill:#2a2740}
.node-check circle{fill:#16a34a}
.node-check text{font-size:11px;text-anchor:middle;fill:#fff}
.node-ai{font-size:9px;text-anchor:end;fill:#4f46e5;font-weight:700}
.panel{position:sticky;top:16px;display:grid;gap:12px;justify-items:start;padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.panel__head{display:flex;width:100%;justify-content:space-between;align-items:center}
.panel__head h3{margin:0;font-size:15px}
.panel__close{width:26px;height:26px;border:1px solid var(--border);border-radius:50%;background:var(--muted);font-size:14px;line-height:1;cursor:pointer;color:var(--color-text-secondary)}
.panel__empty{margin:0;font-size:13px;color:var(--muted-foreground)}
.drawer__class{margin:0;font-size:12px;color:var(--color-text-secondary)}
.drawer__block{width:100%;padding-top:10px;border-top:1px solid var(--border)}
.drawer__block h4{display:flex;align-items:center;gap:8px;margin:0 0 6px;font-size:13px}
.drawer__block p{margin:0;font-size:13px;line-height:1.6}
.drawer__update{padding:12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--muted)}
.drawer__update p{margin:0 0 10px}
.draft-badge{padding:1px 8px;border-radius:999px;background:var(--color-warning-subtle);color:var(--color-warning);font-size:11px}
.draft-badge.confirmed{background:var(--color-success-subtle);color:var(--color-success)}
.drawer__note{margin:0;font-size:12px;color:var(--muted-foreground)}
.inspector__head{display:flex;gap:8px}
.inspector__title{margin:0;font-size:15px}
.kind{padding:1px 8px;border-radius:999px;font-size:11px;background:var(--muted);color:var(--color-text-secondary)}
.kind[data-kind="safety"]{background:var(--color-danger-subtle);color:var(--destructive)}
.kind[data-kind="branch"]{background:var(--color-warning-subtle);color:var(--color-warning)}
.kind[data-kind="communicate"]{background:var(--color-info-subtle,var(--muted));color:var(--color-info,var(--primary))}
.status{font-size:11px;color:var(--color-text-secondary)}
.status[data-status="completed"]{color:var(--color-success)}
.status[data-status="in_progress"]{color:var(--primary);font-weight:700}
.locked-note{margin:0;font-size:12px;color:var(--muted-foreground)}
.ai-note{margin:0;font-size:12px;color:var(--primary)}
.branch-options{display:grid;gap:8px;width:100%}
.record-input{display:grid;gap:6px;width:100%;font-size:13px;font-weight:600}
.record-input textarea{padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;line-height:1.5;resize:vertical}
.record-input textarea:focus-visible{border-color:var(--ring);box-shadow:var(--focus-ring);outline:0}
.inspector__actions{display:flex;gap:8px}
.record{margin:0;font-size:12px;color:var(--color-text-secondary)}
</style>
