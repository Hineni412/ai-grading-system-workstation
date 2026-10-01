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
const purpose = defineModel<'training' | 'handout'>('purpose', { default: 'training' })
const maxQuestionsPerSkill = defineModel<number>('maxQuestionsPerSkill', { default: 1 })
const maxWrittenQuestions = defineModel<number>('maxWrittenQuestions', { default: 2 })
const recentActivityCount = defineModel<number>('recentActivityCount', { default: 3 })
const teachingProgressChapterId = defineModel<string>('teachingProgressChapterId', { default: '' })
const scopeMode = defineModel<'comprehensive' | 'focused'>('scopeMode', { default: 'comprehensive' })

const modeLabel = computed(() => (props.mode === 'shared' ? '多人同一套卷' : '一人一卷'))
const modeNote = computed(() => (props.mode === 'shared'
  ? '整卷覆盖成员各自需求，每道题可主要帮助部分成员。'
  : '每名学生在选定范围内按实际需要选题，也可安排合适的新练习。'))
</script>

<template>
  <section class="paper-settings-panel" aria-labelledby="paper-settings-panel-title">
    <header>
      <div><h3 id="paper-settings-panel-title">出卷设置</h3><span>{{ modeLabel }} · {{ studentCount }} 人 · {{ selectionText }}</span></div>
      <AppButton variant="primary" data-testid="go-paper" :disabled="!valid || generating" @click="emit('goPaper')">
        {{ generating ? '正在生成…' : purpose === 'handout' ? '审核讲义草稿' : '审核试卷草稿' }}
      </AppButton>
    </header>
    <div class="paper-settings__grid">
      <label v-if="mode === 'individual'">训练范围<select v-model="scopeMode" class="app-input" aria-label="训练范围模式"><option value="comprehensive">综合训练 · 已学章节</option><option value="focused">专项训练 · 勾选范围</option></select></label>
      <label>已学到<select v-model="teachingProgressChapterId" class="app-input" aria-label="已学到的章节"><option value="">{{ mode === 'individual' && scopeMode === 'comprehensive' ? '按已有作答及勾选范围' : '按所选目标的最晚章节' }}</option><option v-for="chapter in progressChapters ?? []" :key="chapter.id" :value="chapter.id">{{ chapter.label }}</option></select></label>
      <label>用途<select v-model="purpose" class="app-input" aria-label="出卷用途"><option value="training">训练卷 · 回收批改</option><option value="handout">讲义 · 只打印</option></select></label>
      <label>每卷题数 <small>{{ purpose === 'training' ? '8–12 题' : '至少 1 题' }}</small><input v-model.number="questionCount" class="app-input" aria-label="每卷题数" type="number" :min="purpose === 'training' ? 8 : 1" :max="purpose === 'training' ? 12 : undefined" step="1"></label>
      <label>难度上限 <small>1–10 级</small><input v-model.number="difficultyMax" class="app-input" aria-label="难度上限" type="number" min="1" max="10" step="1"></label>
    </div>
    <details class="paper-settings__more">
      <summary>选题细则 <span>同技能 ≤ {{ maxQuestionsPerSkill }} 道 · 解答题 ≤ {{ maxWrittenQuestions }} 道 · 排除最近 {{ recentActivityCount }} 次原题</span></summary>
      <div class="paper-settings__grid">
        <label>同一技能最多<input v-model.number="maxQuestionsPerSkill" class="app-input" aria-label="同一技能最多" type="number" min="1" :max="questionCount" step="1"></label>
        <label>解答题最多<input v-model.number="maxWrittenQuestions" class="app-input" aria-label="解答题最多" type="number" min="0" :max="questionCount" step="1"></label>
        <label>近期原题排除次数<input v-model.number="recentActivityCount" class="app-input" aria-label="近期原题排除次数" type="number" min="0" step="1"><small>0 = 不排除</small></label>
      </div>
      <p>{{ modeNote }} {{ recentActivityCount === 0 ? '不排除近期原题。' : purpose === 'handout' ? '排除近期考试原题，可复用历史训练题。' : '排除近期考试与训练原题，包含刚完成的训练。' }}</p>
    </details>
    <p v-if="scopeSummary" class="paper-settings-panel__hint">{{ scopeSummary }}</p>
    <p v-if="purpose === 'handout'" class="paper-settings-panel__hint">讲义不回收、不更新掌握度；题量较大时可放宽同技能上限。</p>
    <p v-if="!valid" class="paper-settings-panel__hint" role="status">请确认学生、{{ mode === 'shared' ? '训练目标' : '已学进度或专项范围' }}与出卷设置。</p>
  </section>
</template>

<style scoped>
.paper-settings-panel{margin:var(--space-4) var(--space-5);padding:var(--space-4) var(--space-5);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);box-shadow:var(--shadow-raised)}
.paper-settings-panel>header{display:flex;justify-content:space-between;align-items:center;gap:var(--space-4);margin-bottom:var(--space-4)}
.paper-settings-panel h3{margin:0 0 var(--space-1);font-size:var(--font-size-h3)}
.paper-settings-panel header span,.paper-settings-panel__hint,.paper-settings__more p{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.paper-settings__grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:var(--space-3)}
.paper-settings__grid label{display:flex;flex-wrap:wrap;gap:var(--space-1);align-content:start;color:var(--color-text-secondary);font-size:var(--font-size-dense);min-width:0}
.paper-settings__grid input,.paper-settings__grid select{width:100%;min-width:0;flex-basis:100%}
.paper-settings__grid small{margin-left:auto;color:var(--color-text-muted);font-size:var(--font-size-caption)}
.paper-settings__more{margin-top:var(--space-4);border-top:1px solid var(--color-border-subtle);padding-top:var(--space-3)}
.paper-settings__more summary{cursor:pointer;font-size:var(--font-size-dense)}
.paper-settings__more summary span{color:var(--color-text-muted);margin-left:var(--space-3)}
.paper-settings__more .paper-settings__grid{margin-top:var(--space-3);max-width:680px}
.paper-settings-panel__hint{margin:var(--space-3) 0 0}
@media(max-width:760px){.paper-settings-panel{margin:var(--space-3);padding:var(--space-4)}.paper-settings-panel>header{flex-wrap:wrap}.paper-settings__grid{grid-template-columns:repeat(2,minmax(0,1fr))}.paper-settings__more summary span{display:block;margin:var(--space-1) 0 0}}
</style>
