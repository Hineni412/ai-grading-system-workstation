<script setup lang="ts">
import { computed } from 'vue'

import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{
  mode: 'individual' | 'shared'
  studentCount: number
  selectionText: string
  valid: boolean
  generating?: boolean
}>()

const emit = defineEmits<{ goPaper: [] }>()

const questionCount = defineModel<number>('questionCount', { required: true })
const expectedMinutes = defineModel<number>('expectedMinutes', { required: true })
const difficultyMin = defineModel<number>('difficultyMin', { required: true })
const difficultyMax = defineModel<number>('difficultyMax', { required: true })
const directRatio = defineModel<number>('directRatio', { required: true })
const prerequisiteRatio = defineModel<number>('prerequisiteRatio', { required: true })
const transferRatio = defineModel<number>('transferRatio', { required: true })
const excludeCurrentOriginals = defineModel<boolean>('excludeCurrentOriginals', { required: true })

const modeLabel = computed(() => (props.mode === 'shared' ? '多人同一套卷' : '一人一卷'))
const modeNote = computed(() => (props.mode === 'shared'
  ? '所有学生使用同一套题，姓名和回收身份独立。'
  : '每名学生在选定范围内按各自细点掌握情况分别选题。'))
const stageRatioTotal = computed(() => directRatio.value + prerequisiteRatio.value + transferRatio.value)
</script>

<template>
  <section class="paper-settings-panel" aria-labelledby="paper-settings-panel-title">
    <header>
      <div>
        <p class="training-eyebrow">出卷设置 · {{ modeLabel }}</p>
        <h3 id="paper-settings-panel-title">在本页完成 {{ modeLabel }} 的出卷设置</h3>
      </div>
      <span>{{ studentCount }} 名学生 · {{ selectionText }}</span>
    </header>

    <div class="paper-settings__grid">
      <label>每卷题数<input v-model.number="questionCount" type="number" min="8" max="12"><small>8–12 题</small></label>
      <label>预计时长<input v-model.number="expectedMinutes" type="number" min="10" max="180"><small>分钟</small></label>
      <label>最低难度<input v-model.number="difficultyMin" type="number" min="1" max="10"><small>1–10</small></label>
      <label>最高难度<input v-model.number="difficultyMax" type="number" min="1" max="10"><small>1–10</small></label>
    </div>
    <div class="paper-ratios" aria-label="训练题目比例">
      <label><span>针对训练</span><input v-model.number="directRatio" type="number" min="0" max="100"><b>%</b></label>
      <label><span>基础巩固</span><input v-model.number="prerequisiteRatio" type="number" min="0" max="100"><b>%</b></label>
      <label><span>提升应用</span><input v-model.number="transferRatio" type="number" min="0" max="100"><b>%</b></label>
      <strong :class="{ 'is-error': stageRatioTotal !== 100 }">合计 {{ stageRatioTotal }}%</strong>
    </div>
    <p v-if="stageRatioTotal !== 100" class="field-error">三类训练比例合计需为 100%。</p>
    <p v-if="difficultyMin > difficultyMax" class="field-error">最低难度不能高于最高难度。</p>

    <div class="paper-settings-panel__footer">
      <label class="paper-settings-panel__exclude">
        <input v-model="excludeCurrentOriginals" type="checkbox">排除本次考试原题
      </label>
      <span class="paper-settings-panel__note">{{ modeNote }}</span>
      <AppButton
        variant="primary"
        data-testid="go-paper"
        :disabled="!valid || generating"
        @click="emit('goPaper')"
      >
        {{ generating ? '正在生成…' : '设置完成，去生成试卷' }}
      </AppButton>
    </div>
    <p v-if="!valid" class="paper-settings-panel__hint">
      请先在本页完成学生与{{ mode === 'shared' ? '细知识点' : '章/节范围' }}勾选，并确认比例合计为 100%。
    </p>
  </section>
</template>

<style scoped>
.paper-settings-panel { margin: 0 var(--space-6) var(--space-6); padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.paper-settings-panel > header { display: flex; justify-content: space-between; align-items: end; gap: var(--space-4); }
.paper-settings-panel > header > span { color: var(--color-text-muted); font-size: var(--font-size-caption); }
.paper-settings-panel h3 { margin: var(--space-1) 0 0; }
.paper-settings__grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-2); margin-top: var(--space-3); }
.paper-settings__grid label { display: grid; grid-template-columns: 1fr auto; gap: var(--space-1); color: var(--color-text-muted); font-size: var(--font-size-dense); }
.paper-settings__grid input { grid-column: 1 / -1; width: 100%; }
.paper-settings__grid small { color: var(--color-text-muted); }
.paper-ratios { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) auto; gap: var(--space-2); align-items: end; margin-top: var(--space-3); padding: var(--space-3); border-radius: var(--radius-md); background: var(--color-bg-subtle); }
.paper-ratios label { display: grid; grid-template-columns: 1fr 22px; gap: var(--space-1); align-items: center; }
.paper-ratios label span { grid-column: 1 / -1; color: var(--color-text-muted); font-size: var(--font-size-caption); }
.paper-ratios input { width: 100%; }
.paper-ratios b { font-weight: 500; }
.paper-ratios > strong { padding-bottom: var(--space-2); white-space: nowrap; color: var(--color-accent); }
.paper-ratios > strong.is-error { color: var(--destructive); }
.paper-settings-panel__footer { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); margin-top: var(--space-3); }
.paper-settings-panel__exclude { display: flex; gap: var(--space-2); align-items: center; }
.paper-settings-panel__note { flex: 1 1 16rem; color: var(--color-text-muted); font-size: var(--font-size-dense); }
.paper-settings-panel__footer button { margin-left: auto; }
.paper-settings-panel__hint { margin: var(--space-2) 0 0; color: var(--color-text-muted); font-size: var(--font-size-dense); }
.field-error { color: var(--destructive); }
</style>
