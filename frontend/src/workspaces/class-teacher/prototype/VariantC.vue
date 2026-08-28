// PROTOTYPE — throwaway, ?variant= 切换，mock 数据，验收后删除
<script setup lang="ts">
import { computed, ref } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'

import { kindLabel, statusLabel, type PrototypeStep, type SopPrototype } from './mock'
import PrototypeAside from './PrototypeAside.vue'

const props = defineProps<{ proto: SopPrototype }>()

const focusId = ref('')
const showRecordInput = ref(false)

const currentStep = computed<PrototypeStep>(() => {
  const actionable = props.proto.state.steps.find(
    (step) => (step.status === 'in_progress' || step.status === 'pending') && props.proto.prereqsDone(step),
  )
  return actionable ?? props.proto.state.steps[props.proto.state.steps.length - 1] as PrototypeStep
})
const focused = computed<PrototypeStep>(() => (focusId.value ? props.proto.stepBy(focusId.value) : currentStep.value))
const completedSteps = computed(() => props.proto.state.steps.filter((step) => step.status === 'completed'))
const futureSteps = computed(() => props.proto.state.steps.filter(
  (step) => step.status !== 'completed' && step.status !== 'pruned' && step.id !== currentStep.value.id,
))

function completeAndContinue(): void {
  const step = focused.value
  props.proto.completeStep(step.id)
  if (step.status === 'completed') {
    focusId.value = ''
    showRecordInput.value = false
  }
}

function chooseAndContinue(optionKey: string): void {
  props.proto.chooseBranch(optionKey)
  focusId.value = ''
}

function miniNodes(): PrototypeStep[] {
  return ['1', '2', '3', '4', '6', '7'].map((id) => props.proto.stepBy(id))
}
</script>

<template>
  <div class="variant-c">
    <PrototypeAside :proto="proto" layout="rail" />

    <section class="focus-area">
      <nav class="mini-flow" aria-label="迷你流程图">
        <template v-for="(step, index) in miniNodes()" :key="step.id">
          <button
            type="button"
            class="mini-dot"
            :class="[`is-${step.status}`, { current: step.id === focused.id }]"
            :title="step.title"
            @click="focusId = step.id"
          >
            {{ step.kind === 'branch' ? '◇' : step.id }}
          </button>
          <span v-if="step.id === '4'" class="mini-fork" aria-hidden="true">
            <button
              v-for="branchId in ['5a', '5b']"
              :key="branchId"
              type="button"
              class="mini-dot mini-dot--branch"
              :class="[`is-${proto.stepBy(branchId).status}`, { current: branchId === focused.id }]"
              :title="proto.stepBy(branchId).title"
              @click="focusId = branchId"
            >{{ branchId === '5a' ? 'a' : 'b' }}</button>
          </span>
          <span v-if="index < miniNodes().length - 1" class="mini-link" aria-hidden="true" />
        </template>
      </nav>

      <article class="focus-card" :class="`is-${focused.status}`">
        <header>
          <span class="kind" :data-kind="focused.kind">{{ kindLabel[focused.kind] }}</span>
          <span class="status" :data-status="focused.status">{{ statusLabel[focused.status] }}</span>
          <span v-if="focused.aiUpdated" class="ai-badge">AI 已更新</span>
        </header>
        <h2>{{ focused.title }}</h2>
        <p v-if="focused.hint" class="hint">{{ focused.hint }}</p>
        <p v-if="focused.aiUpdated" class="ai-note">{{ focused.aiNote }}</p>
        <p v-if="focused.status === 'locked'" class="locked-note">前置步骤未完成，这一步还未解锁。</p>
        <p v-else-if="focused.status === 'pruned'" class="locked-note">该分支已在分流判断中被剪枝。</p>
        <p v-else-if="focused.status === 'completed'" class="done-note">这一步已完成。</p>
        <p v-if="focused.record" class="record">已记录：{{ focused.record }}</p>

        <template v-if="focused.kind === 'branch' && focused.status !== 'completed'">
          <div class="big-actions">
            <AppButton
              v-for="option in proto.state.branchOptions"
              :key="option.key"
              variant="primary"
              block
              :disabled="!proto.canChooseBranch()"
              @click="chooseAndContinue(option.key)"
            >
              {{ option.label }}
            </AppButton>
          </div>
        </template>
        <template v-else-if="focused.status === 'in_progress' || focused.status === 'pending'">
          <div v-if="showRecordInput" class="record-area">
            <textarea v-model="focused.recordDraft" rows="4" placeholder="记录这一步实际做了什么、学生怎么回应"></textarea>
            <AppButton variant="secondary" :disabled="!focused.recordDraft.trim()" @click="proto.saveRecord(focused.id)">保存记录</AppButton>
          </div>
          <div class="big-actions">
            <AppButton variant="secondary" block @click="showRecordInput = !showRecordInput">
              {{ showRecordInput ? '收起处理记录' : '填写处理记录' }}
            </AppButton>
            <AppButton variant="primary" block :disabled="!proto.canComplete(focused)" @click="completeAndContinue">完成并继续</AppButton>
          </div>
        </template>
        <AppButton v-if="focused.id !== currentStep.id" variant="ghost" @click="focusId = ''">回到当前步骤</AppButton>
      </article>

      <details class="done-accordion">
        <summary>已完成 {{ completedSteps.length }} 步</summary>
        <ul>
          <li v-for="step in completedSteps" :key="step.id">
            <strong>{{ step.title }}</strong>
            <span v-if="step.kind === 'branch' && step.chosenOption">
              — 已选 {{ proto.state.branchOptions.find((o) => o.key === step.chosenOption)?.label }}
            </span>
            <p v-if="step.record">记录：{{ step.record }}</p>
          </li>
        </ul>
      </details>

      <section class="future" aria-label="后续步骤">
        <h3>后续步骤</h3>
        <div class="future__list">
          <button
            v-for="step in futureSteps"
            :key="step.id"
            type="button"
            class="future__thumb"
            :class="`is-${step.status}`"
            @click="focusId = step.id"
          >
            <span class="status" :data-status="step.status">{{ statusLabel[step.status] }}</span>
            <span class="future__title">{{ step.title }}</span>
          </button>
        </div>
      </section>
    </section>
  </div>
</template>

<style scoped>
.variant-c{display:grid;grid-template-columns:250px minmax(0,1fr);gap:16px;align-items:start}
.focus-area{display:grid;gap:16px}
.mini-flow{display:flex;align-items:center;justify-content:center;gap:0;padding:14px 20px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.mini-dot{flex:none;width:34px;height:34px;border:2px solid var(--border);border-radius:50%;background:var(--card);font:inherit;font-size:13px;font-weight:700;color:var(--color-text-secondary);cursor:pointer}
.mini-dot.is-completed{border-color:var(--color-success,#16a34a);color:var(--color-success,#16a34a)}
.mini-dot.is-in_progress,.mini-dot.current{border-color:var(--primary);color:var(--primary)}
.mini-dot.is-pruned{text-decoration:line-through;opacity:.5}
.mini-link{flex:1;min-width:14px;height:2px;background:var(--border)}
.mini-fork{display:grid;gap:6px;padding:4px 8px}
.mini-dot--branch{width:24px;height:24px;font-size:11px;border-style:dashed}
.focus-card{display:grid;gap:12px;justify-items:start;padding:28px 32px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.focus-card header{display:flex;gap:8px;align-items:center}
.focus-card h2{margin:0;font-size:22px}
.focus-card.is-locked,.focus-card.is-pruned{opacity:.7}
.big-actions{display:grid;gap:10px;width:100%;max-width:420px}
.record-area{display:grid;gap:8px;width:100%;max-width:420px}
.record-area textarea{padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;line-height:1.5;resize:vertical}
.record-area textarea:focus-visible{border-color:var(--ring);box-shadow:var(--focus-ring);outline:0}
.done-accordion{padding:12px 16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--muted)}
.done-accordion summary{cursor:pointer;font-weight:600;font-size:14px}
.done-accordion ul{margin:10px 0 0;padding-left:18px;display:grid;gap:8px;font-size:13px}
.done-accordion p{margin:2px 0 0;font-size:12px;color:var(--color-text-secondary)}
.future h3{margin:0 0 8px;font-size:14px}
.future__list{display:flex;flex-wrap:wrap;gap:8px}
.future__thumb{display:grid;gap:2px;padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--muted);font:inherit;font-size:12px;color:var(--muted-foreground);cursor:pointer;text-align:left}
.future__thumb.is-pending{background:var(--card);color:var(--foreground)}
.kind{padding:1px 8px;border-radius:999px;font-size:11px;background:var(--muted);color:var(--color-text-secondary)}
.kind[data-kind="safety"]{background:var(--color-danger-subtle);color:var(--destructive)}
.kind[data-kind="branch"]{background:var(--color-warning-subtle);color:var(--color-warning)}
.kind[data-kind="communicate"]{background:var(--color-info-subtle,var(--muted));color:var(--color-info,var(--primary))}
.status{font-size:11px;color:var(--color-text-secondary)}
.status[data-status="completed"]{color:var(--color-success,#16a34a)}
.status[data-status="in_progress"]{color:var(--primary);font-weight:700}
.ai-badge{padding:1px 8px;border-radius:999px;background:var(--color-info-subtle,var(--muted));color:var(--primary);font-size:11px}
.ai-note{margin:0;font-size:12px;color:var(--primary)}
.hint{margin:0;font-size:12px;color:var(--color-info,var(--primary))}
.locked-note,.done-note{margin:0;font-size:13px;color:var(--muted-foreground)}
.record{margin:0;font-size:13px;color:var(--color-text-secondary)}
</style>
