<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  fetchStudentKeyStepRepeats,
  type KeyStepRepeat,
  type KeyStepRepeatItem,
} from '../../api/students'

const props = defineProps<{
  studentId: number | string
  curriculumVolumeId: string
  onlyDeducted: boolean
  pageSize: number
  jumpMessage: string
}>()
const emit = defineEmits<{
  jump: [payload: { sessionId: number; questionId: string; key: string; page: number | null }]
}>()

const route = useRoute()
const data = ref<{ repeated: KeyStepRepeat[]; singles: KeyStepRepeat[] } | null>(null)
const singlesOpen = ref(false)
const highlightKey = ref('')
let controller: AbortController | null = null

const repeatedRows = computed(() => [...(data.value?.repeated ?? [])]
  .sort((a, b) => b.fails - a.fails || a.name.localeCompare(b.name, 'zh-CN')))
const singleRows = computed(() => [...(data.value?.singles ?? [])]
  .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')))
const queryKey = computed(() => typeof route.query.keyStep === 'string' ? route.query.keyStep : '')

function shortSessionName(name: string): string {
  return name.replace(/学情反馈$/, '') || name
}

function itemLabel(item: KeyStepRepeatItem): string {
  return `${shortSessionName(item.session_name)} ${item.question_id.replace('(P', '(')}`
}

function jumpToItem(entry: KeyStepRepeat, item: KeyStepRepeatItem): void {
  emit('jump', {
    sessionId: item.session_id,
    questionId: item.question_id,
    key: entry.key,
    page: item.evidence_page,
  })
}

function applyQueryKey(): void {
  const key = queryKey.value
  if (!key) return
  if (singleRows.value.some((entry) => entry.key === key)) singlesOpen.value = true
  highlightKey.value = key
}

async function load(): Promise<void> {
  controller?.abort()
  if (!props.curriculumVolumeId) {
    data.value = null
    return
  }
  controller = new AbortController()
  const active = controller
  try {
    const response = await fetchStudentKeyStepRepeats(
      props.studentId,
      props.curriculumVolumeId,
      { onlyDeducted: props.onlyDeducted, pageSize: props.pageSize },
      active.signal,
    )
    if (active.signal.aborted) return
    data.value = response
    applyQueryKey()
  } catch {
    if (!active.signal.aborted) data.value = null
  }
}

watch(() => [props.studentId, props.curriculumVolumeId, props.onlyDeducted, props.pageSize], load)
watch(queryKey, applyQueryKey)
onMounted(load)
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section v-if="data" class="key-step-card" data-testid="key-step-repeats-card">
    <header class="key-step-card__head">
      <strong>反复卡点</strong>
      <small>同一关键步骤在 ≥2 道小问独立失分（本学期）</small>
    </header>
    <p v-if="jumpMessage" class="key-step-card__jump-message" role="status">{{ jumpMessage }}</p>
    <template v-if="repeatedRows.length">
      <div
        v-for="entry in repeatedRows"
        :key="entry.key"
        class="key-step-card__row"
        :class="{ 'is-highlighted': highlightKey === entry.key }"
      >
        <span class="key-step-card__name">{{ entry.name }}</span>
        <span class="key-step-card__stat">独立失分 {{ entry.fails }} 次 · 做到 {{ entry.mets }} 次</span>
        <span class="key-step-card__items">
          <button
            v-for="item in entry.items"
            :key="`${item.session_id}|${item.question_id}|${item.step_id}`"
            type="button"
            class="key-step-card__tag"
            :title="`${item.goal}${item.reason ? '：' + item.reason : ''}`"
            @click="jumpToItem(entry, item)"
          >{{ itemLabel(item) }}</button>
        </span>
      </div>
    </template>
    <p v-else class="key-step-card__empty">暂无反复卡点（需同一步骤在至少 2 道小问独立失分）</p>
    <div v-if="singleRows.length" class="key-step-card__singles">
      <button type="button" class="key-step-card__toggle" @click="singlesOpen = !singlesOpen">
        只失分 1 次的关键步骤（{{ singleRows.length }}）证据不足 {{ singlesOpen ? '▾' : '▸' }}
      </button>
      <template v-if="singlesOpen">
        <div
          v-for="entry in singleRows"
          :key="entry.key"
          class="key-step-card__row is-single"
          :class="{ 'is-highlighted': highlightKey === entry.key }"
        >
          <span class="key-step-card__name">{{ entry.name }}</span>
          <span class="key-step-card__stat">独立失分 1 次</span>
          <span class="key-step-card__items">
            <button
              v-for="item in entry.items"
              :key="`${item.session_id}|${item.question_id}|${item.step_id}`"
              type="button"
              class="key-step-card__tag"
              :title="`${item.goal}${item.reason ? '：' + item.reason : ''}`"
              @click="jumpToItem(entry, item)"
            >{{ itemLabel(item) }}</button>
          </span>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.key-step-card {
  margin-top: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}
.key-step-card__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.key-step-card__head small,
.key-step-card__empty {
  color: var(--color-text-muted);
  font-size: var(--font-size-dense);
}
.key-step-card__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-3);
  padding: var(--space-2);
  border-radius: var(--radius-sm);
}
.key-step-card__row + .key-step-card__row { border-top: 1px dashed var(--color-border-subtle); }
.key-step-card__row.is-highlighted {
  background: var(--color-warning-subtle);
  outline: 1px solid var(--color-warning);
}
.key-step-card__row.is-single .key-step-card__name {
  color: var(--color-text-secondary);
  font-weight: normal;
}
.key-step-card__name { font-weight: var(--font-weight-semibold); }
.key-step-card__stat {
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}
.key-step-card__items {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}
.key-step-card__tag {
  padding: 0 var(--space-1);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-sm);
  background: var(--color-bg-subtle);
  color: var(--color-text-primary);
  cursor: pointer;
}
.key-step-card__tag:hover { border-color: var(--color-focus); }
.key-step-card__toggle {
  margin-top: var(--space-1);
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: var(--font-size-dense);
}
.key-step-card__empty { margin: var(--space-1) 0; }
.key-step-card__jump-message {
  margin: 0 0 var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}
</style>
