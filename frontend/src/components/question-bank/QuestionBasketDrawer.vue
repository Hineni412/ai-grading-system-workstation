<script setup lang="ts">
import { computed, watch } from 'vue'
import { DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle, DialogClose } from 'reka-ui'
import { useAssemblyStore } from '../../stores/assembly'
import { defaultPaperRules, paperRulesSummary } from '../../api/assembly'
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [open: boolean] }>()
const assembly = useAssemblyStore()
const ruleSummary = computed(() => paperRulesSummary(typeof assembly.draft.practice_rules === 'object' && assembly.draft.practice_rules ? assembly.draft.practice_rules : defaultPaperRules()))
const counts = computed(() => assembly.orderedQuestions.reduce((counts, question) => { if (/选择/.test(question.question_type || '')) counts.choice++; else if (question.question_type === '填空题') counts.fill++; else counts.essay++; return counts }, { choice: 0, fill: 0, essay: 0 }))
watch(() => props.open, open => { if (open && assembly.loadState === 'idle') void assembly.load() })
async function clear() {
  if (!window.confirm(`清空试卷篮中的 ${assembly.selectedQuestionCount} 道题吗？题库题目仍保留。`)) return
  await assembly.save({ ...assembly.draft, basket_ids: [], order_ids: [], sections: assembly.draft.sections.map(section => ({ ...section, question_ids: [] })), practice_rules: false })
}
</script>
<template>
  <DialogRoot :open="open" @update:open="emit('update:open', $event)"><DialogPortal><DialogOverlay class="qb-basket-mask" /><DialogContent class="qb-basket-drawer" :aria-describedby="undefined"><header><DialogTitle>试卷篮 · {{ assembly.selectedQuestionCount }}</DialogTitle><DialogClose class="qb-link" aria-label="关闭试卷篮">关闭 ×</DialogClose></header><p class="qb-basket-summary">{{ assembly.selectedQuestionCount }} 题 · 选择 {{ counts.choice }} · 填空 {{ counts.fill }} · 解答 {{ counts.essay }}</p><p v-if="assembly.draft.practice_rules" class="qb-feedback is-warning">出卷设置：{{ ruleSummary }}；导出前核对全部限制。</p><p v-if="assembly.loadState === 'loading'" role="status">正在读取试卷篮…</p><p v-if="assembly.message" class="qb-feedback" role="status">{{ assembly.message }}</p><p v-if="assembly.missingQuestionIds.length" class="qb-feedback is-warning">部分篮中题目已不可用，请到编辑页核对。</p><ol><li v-for="(question, index) in assembly.orderedQuestions" :key="question.id"><span>{{ index + 1 }}</span><div><strong>第 {{ question.question_number }} 题 · {{ question.paper_title }}</strong><small>{{ question.question_type }}</small></div><button class="qb-link" :disabled="assembly.saveState === 'saving'" @click="assembly.removeQuestion(question.id)">移出</button></li></ol><p v-if="!assembly.selectedQuestionCount">试卷篮为空，可从题卡加入。</p><footer><button class="qb-link" :disabled="!assembly.selectedQuestionCount || assembly.saveState === 'saving'" @click="clear">清空</button><RouterLink class="qb-button" :to="{ path: '/question-assembly', query: { mode: 'edit' } }" @click="emit('update:open', false)">编辑与导出 →</RouterLink></footer></DialogContent></DialogPortal></DialogRoot>
</template>
