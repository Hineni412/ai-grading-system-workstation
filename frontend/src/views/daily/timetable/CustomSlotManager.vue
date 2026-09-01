<script setup lang="ts">
import { ref } from 'vue'

import type { TimetableSlot } from '../../../api/daily'
import { customPositionText, slotTime } from './timetableModel'

withDefaults(defineProps<{
  slots: TimetableSlot[]
  busy?: boolean
}>(), {
  busy: false,
})

const emit = defineEmits<{
  create: [payload: { label: string; start_text?: string; end_text?: string; position: number }]
  remove: [slot: TimetableSlot]
}>()

const label = ref('')
const startText = ref('')
const endText = ref('')
const position = ref(4)
const formError = ref('')

const positionOptions = Array.from({ length: 9 }, (_, value) => ({
  value,
  label: customPositionText(value),
}))

function submit(): void {
  if (!label.value.trim()) {
    formError.value = '时段名称不能为空。'
    return
  }
  formError.value = ''
  emit('create', {
    label: label.value.trim(),
    ...(startText.value.trim() ? { start_text: startText.value.trim() } : {}),
    ...(endText.value.trim() ? { end_text: endText.value.trim() } : {}),
    position: position.value,
  })
  label.value = ''
  startText.value = ''
  endText.value = ''
}
</script>

<template>
  <section class="custom-slots" aria-label="自定义时段管理">
    <h3>自定义时段（午练 / 午休 / 延时等）</h3>
    <ul v-if="slots.length" class="custom-slots__list">
      <li v-for="slot in slots" :key="slot.slot_key">
        <span class="custom-slots__name">{{ slot.label }}</span>
        <span v-if="slotTime(slot)" class="custom-slots__time">{{ slotTime(slot) }}</span>
        <span class="custom-slots__position">{{ customPositionText(slot.position ?? 0) }}</span>
        <button type="button" :disabled="busy" @click="emit('remove', slot)">删除</button>
      </li>
    </ul>
    <p v-else class="custom-slots__empty">还没有自定义时段，可在下方添加午练、午休或延时。</p>

    <form class="custom-slots__form" @submit.prevent="submit">
      <label>
        名称
        <input v-model="label" type="text" maxlength="20" placeholder="如：午练" :disabled="busy">
      </label>
      <label>
        开始（可留空）
        <input v-model="startText" type="text" maxlength="20" placeholder="12:30" :disabled="busy">
      </label>
      <label>
        结束（可留空）
        <input v-model="endText" type="text" maxlength="20" placeholder="13:10" :disabled="busy">
      </label>
      <label>
        位置
        <select v-model.number="position" :disabled="busy">
          <option v-for="option in positionOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </label>
      <button type="submit" :disabled="busy">添加时段</button>
      <p v-if="formError" class="custom-slots__error" role="alert">{{ formError }}</p>
    </form>
  </section>
</template>

<style scoped>
.custom-slots {
  display: grid;
  gap: var(--space-2);
  padding: 12px 14px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.custom-slots h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.custom-slots__list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.custom-slots__list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.custom-slots__name {
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}

.custom-slots__time,
.custom-slots__position,
.custom-slots__empty {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.custom-slots__empty {
  margin: 0;
}

.custom-slots__list button,
.custom-slots__form button {
  min-height: var(--control-height-small);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
  cursor: pointer;
}

.custom-slots__form button[type='submit'] {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  font-weight: var(--font-weight-medium);
}

.custom-slots__form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--space-2);
}

.custom-slots__form label {
  display: grid;
  gap: 4px;
  color: var(--color-text-secondary);
}

.custom-slots__form input,
.custom-slots__form select {
  min-height: var(--control-height-default);
  padding: 0 10px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
}

.custom-slots__error {
  flex-basis: 100%;
  margin: 0;
  color: var(--color-danger);
}
</style>
