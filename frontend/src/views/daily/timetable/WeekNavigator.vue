<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { weekTitle } from './timetableModel'

const props = withDefaults(defineProps<{
  weekStart: string
  weekNo: number | null
  busy?: boolean
}>(), {
  busy: false,
})

const emit = defineEmits<{
  navigate: [direction: 'prev' | 'today' | 'next']
  anchor: [weekNo: number]
}>()

const correcting = ref(props.weekNo === null)
// type="number" 的输入框 v-model 可能拿到 number，提交前统一转成字符串再校验。
const anchorInput = ref<string | number>('')
const anchorError = ref('')

const title = computed(() => weekTitle(props.weekStart, props.weekNo))

watch(
  () => props.weekNo,
  (weekNo) => {
    // 周次未设定时保持引导展开；设定成功后收起。
    correcting.value = weekNo === null
    anchorError.value = ''
  },
)

function submitAnchor(): void {
  const value = Number(String(anchorInput.value ?? '').trim())
  if (!Number.isInteger(value) || value < 1 || value > 40) {
    anchorError.value = '请输入 1 到 40 之间的周次数字。'
    return
  }
  anchorError.value = ''
  emit('anchor', value)
}
</script>

<template>
  <div class="week-navigator" :class="{ 'is-unset': weekNo === null }">
    <div class="week-navigator__bar">
      <div class="week-navigator__buttons">
        <button type="button" :disabled="busy" aria-label="上一周" @click="emit('navigate', 'prev')">
          ◀ 上一周
        </button>
        <button type="button" :disabled="busy" @click="emit('navigate', 'today')">本周</button>
        <button type="button" :disabled="busy" aria-label="下一周" @click="emit('navigate', 'next')">
          下一周 ▶
        </button>
      </div>
      <h2 class="week-navigator__title">{{ title }}</h2>
      <button
        v-if="!correcting"
        type="button"
        class="week-navigator__correct"
        :disabled="busy"
        @click="correcting = true"
      >
        校正周次
      </button>
    </div>

    <div v-if="correcting" class="week-navigator__anchor">
      <p v-if="weekNo === null" class="week-navigator__guide">
        周次未设定：输入当前查看的周是第几周，系统会自动顺推其它周。
      </p>
      <label>
        把当前查看的周设为第
        <input
          v-model="anchorInput"
          type="number"
          min="1"
          max="40"
          step="1"
          inputmode="numeric"
          :disabled="busy"
          @keyup.enter="submitAnchor"
        >
        周
      </label>
      <button type="button" :disabled="busy" @click="submitAnchor">确定</button>
      <button v-if="weekNo !== null" type="button" :disabled="busy" @click="correcting = false">
        取消
      </button>
      <p v-if="anchorError" class="week-navigator__error" role="alert">{{ anchorError }}</p>
    </div>
  </div>
</template>

<style scoped>
.week-navigator {
  display: grid;
  gap: var(--space-2);
  padding: 10px 14px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.week-navigator.is-unset {
  border-color: var(--color-warning);
  background: var(--color-warning-subtle);
}

.week-navigator__bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.week-navigator__buttons {
  display: flex;
  gap: var(--space-2);
}

.week-navigator__buttons button,
.week-navigator__correct,
.week-navigator__anchor button {
  min-height: var(--control-height-small);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
  cursor: pointer;
}

.week-navigator__buttons button:disabled,
.week-navigator__correct:disabled,
.week-navigator__anchor button:disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}

.week-navigator__title {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.week-navigator__anchor {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-secondary);
}

.week-navigator__guide {
  flex-basis: 100%;
  margin: 0;
  color: var(--color-text-secondary);
}

.week-navigator__anchor input {
  width: 72px;
  min-height: var(--control-height-small);
  margin: 0 4px;
  padding: 0 8px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
  text-align: center;
}

.week-navigator__error {
  flex-basis: 100%;
  margin: 0;
  color: var(--color-danger);
}
</style>
