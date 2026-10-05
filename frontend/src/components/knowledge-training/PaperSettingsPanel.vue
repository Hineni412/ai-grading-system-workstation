<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import WrongQuestionBookExport from './WrongQuestionBookExport.vue'
defineProps<{ context: 'student' | 'group'; studentIds: string[]; volumeId: string; scopeKeys: string[]; valid: boolean; blockedReason?: string; generating?: boolean }>()
const emit = defineEmits<{ goPaper: [] }>()
const purpose = defineModel<'training' | 'handout' | 'wrong_book'>('purpose', { default: 'training' })
const paperMode = defineModel<'individual' | 'shared'>('paperMode', { default: 'individual' })
const questionCount = defineModel<number>('questionCount', { required: true })
const difficultyMax = defineModel<number>('difficultyMax', { required: true })
const maxQuestionsPerSkill = defineModel<number>('maxQuestionsPerSkill', { default: 1 })
const maxWrittenQuestions = defineModel<number>('maxWrittenQuestions', { default: 2 })
const recentActivityCount = defineModel<number>('recentActivityCount', { default: 3 })
const maxConsolidationQuestions = defineModel<number>('maxConsolidationQuestions', { default: 0 })
const maxUnmeasuredQuestions = defineModel<number>('maxUnmeasuredQuestions', { default: 4 })
const wrongBookSessionIds = defineModel<number[] | null>('wrongBookSessionIds', { default: null })
const includeSourceLabel = defineModel<boolean>('includeSourceLabel', { default: true })
const includeAnswerSpace = defineModel<boolean>('includeAnswerSpace', { default: true })
</script>
<template>
  <section class="practice-box paper-settings-panel" aria-labelledby="paper-settings-panel-title">
    <header class="practice-box-heading"><strong id="paper-settings-panel-title">{{ context === 'group' ? '给所采用小组' : '给已选学生' }} · {{ studentIds.length }} 人出</strong></header>
    <div class="paper-settings-content">
      <div class="app-segmented paper-purpose" aria-label="出卷用途"><button type="button" :class="{ 'is-active': purpose === 'training' }" :aria-pressed="purpose === 'training'" @click="purpose = 'training'">训练卷</button><button type="button" :class="{ 'is-active': purpose === 'handout' }" :aria-pressed="purpose === 'handout'" @click="purpose = 'handout'">刷题讲义</button><button type="button" :class="{ 'is-active': purpose === 'wrong_book' }" :aria-pressed="purpose === 'wrong_book'" @click="purpose = 'wrong_book'">错题本</button></div>
      <p class="paper-purpose-note">{{ purpose === 'training' ? '回收批改，更新掌握度。批改调用模型、产生费用，批改前另行确认。' : purpose === 'handout' ? '只打印，答案解析在末尾；不回收、不更新掌握度。' : '汇集所选考试的错题，每人一份 Word，答案解析在末尾。' }}</p>
      <WrongQuestionBookExport v-if="purpose === 'wrong_book'" :student-ids="studentIds" :volume-id="volumeId" :scope-keys="scopeKeys" :valid="valid" :blocked-reason="blockedReason" v-model:session-ids="wrongBookSessionIds" v-model:include-source-label="includeSourceLabel" v-model:include-answer-space="includeAnswerSpace" />
      <template v-else>
        <div v-if="context === 'student'" class="app-segmented paper-mode" aria-label="出卷方式"><button type="button" :class="{ 'is-active': paperMode === 'individual' }" :aria-pressed="paperMode === 'individual'" @click="paperMode = 'individual'">一人一卷</button><button type="button" :class="{ 'is-active': paperMode === 'shared' }" :aria-pressed="paperMode === 'shared'" @click="paperMode = 'shared'">多人同卷</button></div>
        <div class="paper-settings-grid">
          <label>{{ purpose === 'handout' && paperMode === 'individual' ? '每人题数上限' : '每卷题数' }}<input v-model.number="questionCount" class="app-input" aria-label="每卷题数" type="number" :min="purpose === 'training' ? 8 : 1" :max="purpose === 'training' ? 12 : undefined" step="1"><small>{{ purpose === 'training' ? '8–12 题' : '至少 1 题' }}</small></label>
          <label>难度上限<input v-model.number="difficultyMax" class="app-input" aria-label="难度上限" type="number" min="1" max="10" step="1"><small>1–10 级</small></label>
        </div>
        <section v-if="purpose === 'handout'" class="paper-composition"><h4>{{ paperMode === 'individual' ? '题量结构' : '选题上限' }}</h4><label>同一技能最多<input v-model.number="maxQuestionsPerSkill" class="app-input" aria-label="同一技能最多" type="number" min="1" :max="questionCount" step="1">道</label><template v-if="paperMode === 'individual'"><label>新练习最多<input v-model.number="maxUnmeasuredQuestions" class="app-input" aria-label="新练习最多" type="number" min="0" :max="questionCount" step="1">道</label><label>巩固题最多<input v-model.number="maxConsolidationQuestions" class="app-input" aria-label="巩固题最多" type="number" min="0" :max="questionCount" step="1">道</label><p>同一技能上限对三类题合计生效。</p></template></section>
        <details class="paper-settings-more"><summary>选题细则</summary><div class="paper-rule-fields"><label v-if="purpose === 'training'">同一技能最多<input v-model.number="maxQuestionsPerSkill" class="app-input" aria-label="同一技能最多" type="number" min="1" :max="questionCount" step="1">道</label><label>解答题最多<input v-model.number="maxWrittenQuestions" class="app-input" aria-label="解答题最多" type="number" min="0" :max="questionCount" step="1">道</label><label>近期原题排除次数<input v-model.number="recentActivityCount" class="app-input" aria-label="近期原题排除次数" type="number" min="0" step="1">次</label></div><p>{{ purpose === 'handout' ? '只排除考试原题，可复用历史训练题；0 = 不排除。' : '排除近期考试与训练原题；0 = 不排除。' }}</p></details>
        <p v-if="!valid" class="paper-blocked" role="status">{{ blockedReason }}</p>
        <AppButton variant="primary" class="paper-main-action" data-testid="go-paper" :disabled="!valid || generating" @click="emit('goPaper')">{{ generating ? '正在生成…' : purpose === 'handout' ? '生成讲义草稿 →' : '生成训练卷草稿 →' }}</AppButton>
      </template>
    </div>
  </section>
</template>
<style scoped>
.paper-settings-content{padding:var(--space-4)}.paper-purpose{display:flex;width:100%}.paper-purpose button{flex:1;padding-inline:var(--space-2)}.paper-purpose-note,.paper-blocked,.paper-composition p,.paper-settings-more p{font-size:var(--font-size-caption);color:var(--color-text-secondary);line-height:1.7;margin:var(--space-3) 0}.paper-mode{margin-bottom:var(--space-4)}.paper-settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-3)}.paper-settings-grid label{display:grid;gap:var(--space-2);font-size:var(--font-size-dense);color:var(--color-text-secondary)}.paper-settings-grid input{width:100%;min-width:0}.paper-settings-grid small{color:var(--color-text-muted);font-size:var(--font-size-caption)}.paper-composition,.paper-settings-more{border-top:1px solid var(--color-border-subtle);padding-top:var(--space-3);margin-top:var(--space-4)}h4{font-size:var(--font-size-dense);margin:0 0 var(--space-3)}.paper-composition label,.paper-rule-fields label{display:flex;align-items:center;gap:var(--space-2);margin:var(--space-2) 0;font-size:var(--font-size-dense)}.paper-composition input,.paper-rule-fields input{width:60px;margin-left:auto;text-align:center;min-height:var(--control-height-small)}.paper-settings-more summary{font-size:var(--font-size-dense);cursor:pointer}.paper-rule-fields{margin-top:var(--space-3)}.paper-main-action{width:100%;margin-top:var(--space-2)}.paper-blocked{color:var(--color-warning)}
</style>
<style scoped>
.paper-composition input.app-input,.paper-rule-fields input.app-input{flex:0 0 60px;width:60px}
</style>
