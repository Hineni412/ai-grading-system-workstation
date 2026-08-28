// PROTOTYPE — throwaway, ?variant= 切换，mock 数据，验收后删除
<script setup lang="ts">
import { ref } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'

import { kindLabel, statusLabel, type SopPrototype } from './mock'
import PrototypeAside from './PrototypeAside.vue'

const props = defineProps<{ proto: SopPrototype }>()

const columns = [
  { title: '安全处置', ids: ['1'] },
  { title: '核实了解', ids: ['2', '3'] },
  { title: '判断分流', ids: ['4', '5a', '5b'] },
  { title: '沟通跟进', ids: ['6', '7'] },
]

const expandedId = ref('')

function toggle(id: string): void {
  expandedId.value = expandedId.value === id ? '' : id
}
</script>

<template>
  <div class="variant-b">
    <PrototypeAside :proto="proto" layout="strip" />

    <section class="board" aria-label="阶段看板">
      <div v-for="column in columns" :key="column.title" class="board__column">
        <h3>{{ column.title }}</h3>

        <template v-for="id in column.ids" :key="id">
          <article
            v-if="!['5a', '5b'].includes(id)"
            class="board-card"
            :class="[`is-${proto.stepBy(id).status}`, { expanded: expandedId === id }]"
            @click="toggle(id)"
          >
            <header>
              <span class="kind" :data-kind="proto.stepBy(id).kind">{{ kindLabel[proto.stepBy(id).kind] }}</span>
              <span class="status" :data-status="proto.stepBy(id).status">{{ statusLabel[proto.stepBy(id).status] }}</span>
            </header>
            <strong>{{ proto.stepBy(id).title }}</strong>
            <small v-if="proto.stepBy(id).hint" class="hint">{{ proto.stepBy(id).hint }}</small>
            <span v-if="proto.stepBy(id).aiUpdated" class="ai-badge">AI 已更新</span>
            <span v-if="proto.stepBy(id).chosenOption" class="chosen">
              已选：{{ proto.state.branchOptions.find((o) => o.key === proto.stepBy(id).chosenOption)?.label }}
            </span>
            <p v-if="proto.stepBy(id).record" class="record">已记录：{{ proto.stepBy(id).record }}</p>

            <div v-if="expandedId === id" class="board-card__detail" @click.stop>
              <template v-if="proto.stepBy(id).kind === 'branch'">
                <AppButton
                  v-for="option in proto.state.branchOptions"
                  :key="option.key"
                  :variant="proto.stepBy(id).chosenOption === option.key ? 'primary' : 'secondary'"
                  :disabled="!proto.canChooseBranch()"
                  @click="proto.chooseBranch(option.key)"
                >
                  {{ option.label }}
                </AppButton>
              </template>
              <textarea v-model="proto.stepBy(id).recordDraft" rows="3" placeholder="记录这一步实际做了什么"></textarea>
              <div class="actions">
                <AppButton variant="secondary" :disabled="!proto.stepBy(id).recordDraft.trim()" @click="proto.saveRecord(id)">保存记录</AppButton>
                <AppButton v-if="proto.stepBy(id).kind !== 'branch'" variant="primary" :disabled="!proto.canComplete(proto.stepBy(id))" @click="proto.completeStep(id)">标记完成</AppButton>
              </div>
            </div>
          </article>
        </template>

        <div v-if="column.title === '判断分流'" class="branch-stack">
          <p class="branch-stack__label">备选方案（选一剪其余）</p>
          <article
            v-for="id in ['5a', '5b']"
            :key="id"
            class="board-card board-card--option"
            :class="[`is-${proto.stepBy(id).status}`, { expanded: expandedId === id }]"
            @click="toggle(id)"
          >
            <header>
              <span class="kind" :data-kind="proto.stepBy(id).kind">{{ kindLabel[proto.stepBy(id).kind] }}</span>
              <span class="status" :data-status="proto.stepBy(id).status">{{ statusLabel[proto.stepBy(id).status] }}</span>
            </header>
            <strong>{{ proto.stepBy(id).title }}</strong>
            <span v-if="proto.stepBy(id).aiUpdated" class="ai-badge">AI 已更新</span>
            <p v-if="proto.stepBy(id).record" class="record">已记录：{{ proto.stepBy(id).record }}</p>

            <div v-if="expandedId === id && proto.stepBy(id).status !== 'pruned'" class="board-card__detail" @click.stop>
              <textarea v-model="proto.stepBy(id).recordDraft" rows="3" placeholder="记录这一步实际做了什么"></textarea>
              <div class="actions">
                <AppButton variant="secondary" :disabled="!proto.stepBy(id).recordDraft.trim()" @click="proto.saveRecord(id)">保存记录</AppButton>
                <AppButton variant="primary" :disabled="!proto.canComplete(proto.stepBy(id))" @click="proto.completeStep(id)">标记完成</AppButton>
              </div>
            </div>
          </article>
          <p class="branch-stack__note">选择「需先与家长沟通」则两条备选都剪枝，直接进入家长沟通。</p>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.variant-b{display:grid;gap:16px}
.board{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;align-items:start}
.board__column{display:grid;gap:10px;padding:12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--muted)}
.board__column h3{margin:0;font-size:14px}
.board-card{display:grid;gap:6px;padding:12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);cursor:pointer;text-align:left}
.board-card.expanded{outline:2px solid var(--ring)}
.board-card.is-locked{opacity:.55}
.board-card.is-pruned{opacity:.6}
.board-card.is-pruned strong{text-decoration:line-through}
.board-card.is-completed{border-color:var(--color-success,#16a34a)}
.board-card header{display:flex;justify-content:space-between;align-items:center}
.board-card__detail{display:grid;gap:8px;padding-top:8px;border-top:1px dashed var(--border);cursor:default}
.board-card__detail textarea{padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;font-size:13px;line-height:1.5;resize:vertical}
.board-card__detail textarea:focus-visible{border-color:var(--ring);box-shadow:var(--focus-ring);outline:0}
.board-card--option{border-style:dashed}
.branch-stack{display:grid;gap:8px;padding:10px;border:1px dashed var(--border);border-radius:var(--radius)}
.branch-stack__label,.branch-stack__note{margin:0;font-size:12px;color:var(--muted-foreground)}
.kind{padding:1px 8px;border-radius:999px;font-size:11px;background:var(--muted);color:var(--color-text-secondary)}
.kind[data-kind="safety"]{background:var(--color-danger-subtle);color:var(--destructive)}
.kind[data-kind="branch"]{background:var(--color-warning-subtle);color:var(--color-warning)}
.kind[data-kind="communicate"]{background:var(--color-info-subtle,var(--muted));color:var(--color-info,var(--primary))}
.status{font-size:11px;color:var(--color-text-secondary)}
.status[data-status="completed"]{color:var(--color-success,#16a34a)}
.status[data-status="in_progress"]{color:var(--primary);font-weight:700}
.actions{display:flex;gap:8px}
.ai-badge{justify-self:start;padding:1px 8px;border-radius:999px;background:var(--color-info-subtle,var(--muted));color:var(--primary);font-size:11px}
.hint{color:var(--color-info,var(--primary))}
.record{margin:0;font-size:12px;color:var(--color-text-secondary)}
.chosen{font-size:12px;color:var(--primary)}
@media(max-width:1100px){.board{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
