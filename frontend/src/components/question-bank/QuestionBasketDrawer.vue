<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../ui/sheet'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AppButton from '../design-system/AppButton.vue'
import { useAssemblyStore } from '../../stores/assembly'
import { useConfirm } from '../../composables/useConfirm'
import { defaultPaperRules, paperRulesSummary } from '../../api/assembly'
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [open: boolean] }>()
const assembly = useAssemblyStore()
const { confirm } = useConfirm()
const ruleSummary = computed(() => paperRulesSummary(typeof assembly.draft.practice_rules === 'object' && assembly.draft.practice_rules ? assembly.draft.practice_rules : defaultPaperRules(), assembly.draft.assembly_context?.target_kind))
const counts = computed(() => assembly.orderedQuestions.reduce((counts, question) => { if (/选择/.test(question.question_type || '')) counts.choice++; else if (question.question_type === '填空题') counts.fill++; else counts.essay++; return counts }, { choice: 0, fill: 0, essay: 0 }))
watch(() => props.open, open => { if (open && assembly.loadState === 'idle') void assembly.load() })
async function clear() {
  if (!await confirm({
    title: `清空试卷篮中的 ${assembly.selectedQuestionCount} 道题？`,
    message: '题库题目仍保留。',
    confirmLabel: '清空',
    danger: true,
  })) return
  await assembly.save({ ...assembly.draft, basket_ids: [], order_ids: [], sections: assembly.draft.sections.map(section => ({ ...section, question_ids: [] })), practice_rules: false })
}
</script>
<template>
  <Sheet :open="open" @update:open="emit('update:open', $event)"><SheetContent class="qb-basket-drawer gap-0" :aria-describedby="undefined"><SheetHeader><SheetTitle>试卷篮 · {{ assembly.selectedQuestionCount }}</SheetTitle></SheetHeader><p class="qb-basket-summary">{{ assembly.selectedQuestionCount }} 题 · 选择 {{ counts.choice }} · 填空 {{ counts.fill }} · 解答 {{ counts.essay }}</p><FeedbackBanner v-if="assembly.draft.practice_rules" tone="warning">出卷设置：{{ ruleSummary }}；导出前核对全部限制。</FeedbackBanner><p v-if="assembly.loadState === 'loading'" role="status">正在读取试卷篮…</p><FeedbackBanner v-if="assembly.message" role="status" tone="info" :description="assembly.message" /><FeedbackBanner v-if="assembly.missingQuestionIds.length" tone="warning" description="部分篮中题目已不可用，请到编辑页核对。" /><ol><li v-for="(question, index) in assembly.orderedQuestions" :key="question.id"><span>{{ index + 1 }}</span><div><strong>第 {{ question.question_number }} 题 · {{ question.paper_title }}</strong><small>{{ question.question_type }}</small></div><AppButton :disabled="assembly.saveState === 'saving'" variant="ghost" size="small" @click="assembly.removeQuestion(question.id)">移出</AppButton></li></ol><p v-if="!assembly.selectedQuestionCount">试卷篮为空，可从题卡加入。</p><footer><AppButton :disabled="!assembly.selectedQuestionCount || assembly.saveState === 'saving'" variant="ghost" size="small" @click="clear">清空</AppButton><AppButton :to="{ path: '/question-assembly', query: { mode: 'edit' } }" variant="secondary" :as="RouterLink" @click="emit('update:open', false)">编辑与导出 →</AppButton></footer></SheetContent></Sheet>
</template>
