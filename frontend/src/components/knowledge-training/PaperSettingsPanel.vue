<script setup lang="ts">
import { computed } from 'vue'

import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{
  mode: 'individual' | 'shared'
  studentCount: number
  selectionText: string
  valid: boolean
  generating?: boolean
  progressChapters?: Array<{ id: string; label: string }>
  scopeSummary?: string
}>()

const emit = defineEmits<{ goPaper: [] }>()

const questionCount = defineModel<number>('questionCount', { required: true })
const difficultyMax = defineModel<number>('difficultyMax', { required: true })
const teachingProgressChapterId = defineModel<string>('teachingProgressChapterId', { default: '' })
const scopeMode = defineModel<'comprehensive' | 'focused'>('scopeMode', { default: 'comprehensive' })
const excludeCurrentOriginals = defineModel<boolean>('excludeCurrentOriginals', { required: true })

const modeLabel = computed(() => (props.mode === 'shared' ? '多人同一套卷' : '一人一卷'))
const modeNote = computed(() => (props.mode === 'shared'
  ? '整卷覆盖成员各自需求，每道题可主要帮助部分成员。'
  : '每名学生在选定范围内按实际需要选题，也可安排合适的新练习。'))
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

    <div class="paper-settings__fit">
      <label v-if="mode === 'individual'">训练范围<select v-model="scopeMode" aria-label="训练范围模式">
        <option value="comprehensive">综合训练 · 覆盖已学章节</option>
        <option value="focused">专项训练 · 仅勾选的章或节</option>
      </select></label>
      <label>已学到<select v-model="teachingProgressChapterId" aria-label="已学到的章节">
        <option value="">{{ mode === 'individual' && scopeMode === 'comprehensive' ? '按已有作答及勾选范围的最晚章节' : '按所选训练目标的最晚章节' }}</option>
        <option v-for="chapter in progressChapters ?? []" :key="chapter.id" :value="chapter.id">{{ chapter.label }}</option>
      </select></label>
    </div>
    <p class="paper-settings-panel__hint">{{ scopeSummary ?? (mode === 'shared' ? '专项训练 · 按所选章节与共同目标选题' : '') }}</p>
    <p class="paper-settings-panel__hint">依据同技能多次作答（包含正确与失分）判断适合难度。允许范围内新练习；整题所有小问均检查已学范围，同技能最多1道，相似题受限，解答题最多2道。</p>
    <div class="paper-settings__grid">
      <label>每卷题数<input v-model.number="questionCount" type="number" min="8" max="12"><small>8–12 题</small></label>
      <label>难度上限<input v-model.number="difficultyMax" type="number" min="1" max="8"><small>最高 8 级</small></label>
    </div>
    <div class="paper-settings-panel__footer">
      <label class="paper-settings-panel__exclude">
        训练与考试合并，排除最近3次已有批改结果的原题
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
      请先在本页完成学生与{{ mode === 'shared' ? '细知识点勾选' : '已学进度或专项范围设置' }}，并确认题量与难度上限。
    </p>
  </section>
</template>

<style scoped>
.paper-settings-panel { margin: 0 var(--space-6) var(--space-6); padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.paper-settings-panel > header { display: flex; justify-content: space-between; align-items: end; gap: var(--space-4); }
.paper-settings-panel > header > span { color: var(--color-text-muted); font-size: var(--font-size-caption); }
.paper-settings-panel h3 { margin: var(--space-1) 0 0; }
.paper-settings__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-2); margin-top: var(--space-3); }
.paper-settings__grid label { display: grid; grid-template-columns: 1fr auto; gap: var(--space-1); color: var(--color-text-muted); font-size: var(--font-size-dense); }
.paper-settings__grid input { grid-column: 1 / -1; width: 100%; }
.paper-settings__fit { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); margin-top: var(--space-3); }
.paper-settings__fit label { display: grid; gap: var(--space-1); color: var(--color-text-muted); font-size: var(--font-size-dense); }
.paper-settings__fit select { width: 100%; min-width: 0; }
.paper-settings__grid small { color: var(--color-text-muted); }
.paper-settings-panel__footer { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); margin-top: var(--space-3); }
.paper-settings-panel__exclude { display: flex; gap: var(--space-2); align-items: center; }
.paper-settings-panel__note { flex: 1 1 16rem; color: var(--color-text-muted); font-size: var(--font-size-dense); }
.paper-settings-panel__footer button { margin-left: auto; }
.paper-settings-panel__hint { margin: var(--space-2) 0 0; color: var(--color-text-muted); font-size: var(--font-size-dense); }
.field-error { color: var(--destructive); }
@media (max-width: 600px) {
  .paper-settings-panel { margin: 0 var(--space-3) var(--space-3); }
  .paper-settings-panel > header { align-items: start; flex-direction: column; }
  .paper-settings__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .paper-settings__fit { grid-template-columns: minmax(0, 1fr); }
}
</style>
