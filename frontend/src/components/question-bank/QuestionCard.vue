<script setup lang="ts">
import { ChevronDown } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { knowledgeLeafLabel, questionTypeWithSubtype, type QuestionBankListItem } from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import { useConfirm } from '../../composables/useConfirm'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppButton from '../design-system/AppButton.vue'
import QuestionContentRenderer from './QuestionContentRenderer.vue'
import QuestionAnnotationPanel from './QuestionAnnotationPanel.vue'
import QuestionInsights from './QuestionInsights.vue'

const props = defineProps<{ question: QuestionBankListItem; paperMode?: boolean; badgeLabel?: string }>()
const emit = defineEmits<{ similar: [question: QuestionBankListItem] }>()
const bank = useQuestionBankStore()
const assembly = useAssemblyStore()
const { confirm } = useConfirm()
const heading = ref<HTMLButtonElement | null>(null)
const expanded = computed(() => bank.selectedQuestionId === props.question.id)
const inBasket = computed(() => assembly.draft.basket_ids.includes(props.question.id))
const knowledgePoints = computed(() => props.question.labels
  ? props.question.labels.knowledge_points
  : props.question.tags
    .filter(tag => tag.tag_type === 'knowledge_point')
    .map(tag => ({ key: tag.tag_value, label: knowledgeLeafLabel(tag.tag_value) }))
    .filter(point => !point.label.startsWith('sk_') && !/_t\d{2}$/.test(point.label)))
function typeLabel(label: string): string {
  return knowledgeLeafLabel(label).replace(/^题型[·：:]\s*/, '')
}
const preview = computed(() => bank.detail?.previews.find(item => item.preview_type === 'question' && item.url))
async function toggle() { await bank.selectQuestion(expanded.value ? null : props.question.id) }
function clickHeading(event: MouseEvent) {
  if ((event.target as HTMLElement).closest('button, a, input, label, details')) return
  void toggle()
}
async function basket() {
  if (assembly.loadState === 'idle') await assembly.load()
  if (inBasket.value) await assembly.removeQuestion(props.question.id)
  else await assembly.addQuestions([props.question.id])
}
async function remove() {
  if (await confirm({
    title: `把第 ${props.question.question_number || props.question.id} 题移出当前题库？`,
    message: '删除后可立即恢复。',
    confirmLabel: '移出',
    danger: true,
  })) await bank.deleteCurrent()
}
watch(expanded, async (value, wasExpanded) => {
  if (!value && wasExpanded && !bank.selectedQuestionId) {
    await nextTick()
    heading.value?.scrollIntoView?.({ block: 'nearest' })
    heading.value?.focus({ preventScroll: true })
  }
})
</script>

<template>
  <article :id="`qb-question-${question.id}`" class="qb-question-card qb-inline-card" :class="{ 'is-expanded': expanded, 'is-current': expanded, 'is-in-basket': inBasket }">
    <header class="qb-inline-card__heading" @click="clickHeading">
      <button ref="heading" class="qb-inline-card__title" type="button" :aria-expanded="expanded" :aria-controls="`qb-annotation-${question.id}`" @click="toggle">第 {{ question.question_number || question.id }} 题</button>
      <span v-if="!paperMode" class="qb-inline-card__source">{{ question.paper_title || '未命名试卷' }}</span>
      <span v-if="badgeLabel" class="qb-badge-label">{{ badgeLabel }}</span>
      <label v-if="!expanded" class="qb-inline-card__select"><input type="checkbox" :aria-label="`选择第 ${question.question_number || question.id} 题`" :checked="bank.selectedQuestionIds.includes(question.id)" :disabled="bank.selectionIsFull && !bank.selectedQuestionIds.includes(question.id)" @change="bank.toggleQuestionSelection(question.id, ($event.target as HTMLInputElement).checked)">选入</label>
      <button v-if="!expanded" type="button" class="qb-inline-card__chevron" aria-label="展开题目详情" @click="toggle"><ChevronDown class="qb-caret" aria-hidden="true" /></button>
      <div v-if="expanded" class="qb-inline-card__heading-actions">
      <template v-if="expanded">
        <AppButton variant="secondary" @click="basket">{{ inBasket ? '已在试卷篮' : '加入试卷篮' }}</AppButton>
        <details class="qb-card-menu"><summary aria-label="题目更多操作">⋯</summary><div>
          <button type="button" @click="emit('similar', question)">相似题</button>
          <a v-if="preview?.url" :href="preview.url" target="_blank" rel="noopener">打开原卷</a>
          <button type="button" @click="remove">移出题库</button>
        </div></details>
        <AppButton variant="ghost" aria-label="收起题目详情" @click="toggle">收起 ▲</AppButton>
      </template>
      </div>
    </header>
    <div v-if="!expanded" class="qb-inline-card__collapsed">
      <div class="qb-inline-card__stem"><QuestionContentRenderer :blocks="question.rich_content?.question_blocks" :fallback="question.question_text" image-alt="题目配图" media-mode="list" compact /></div>
      <div class="qb-inline-card__tags">
        <span class="qb-dim-tag is-form">{{ questionTypeWithSubtype(question.question_type, question.tags) }}</span>
        <span class="qb-dim-tag is-diff">难度 {{ question.difficulty || '待定' }}</span>
        <span v-if="question.labels?.primary_type" class="qb-dim-tag is-main" :title="`主题型：${question.labels.primary_type.label}`">{{ typeLabel(question.labels.primary_type.label) }}</span>
        <span v-for="type in question.labels?.secondary_types ?? []" :key="type.key" class="qb-dim-tag is-sub" :title="`次题型：${type.label}`">{{ typeLabel(type.label) }}</span>
        <span v-for="point in knowledgePoints.slice(0, 2)" :key="point.key" class="qb-dim-tag is-kp" :title="point.label">{{ knowledgeLeafLabel(point.label) }}</span>
        <span v-if="knowledgePoints.length > 2" class="qb-dim-tag is-kp" :title="`另有 ${knowledgePoints.length - 2} 个知识点`">+{{ knowledgePoints.length - 2 }}</span>
      </div>
      <QuestionInsights :question="question" />
      <details v-if="question.duplicate_members?.length" class="qb-duplicate-members"><summary>另有 {{ question.duplicate_members.length }} 道相同题 ▸</summary><p v-for="member in question.duplicate_members" :key="member.id"><AppButton :to="{ path: '/question-bank', query: { tab: 'paper', paper: member.paper_id, question: member.id } }" variant="ghost" size="small" :as="RouterLink">第 {{ member.question_number }} 题 · {{ member.paper_title }}</AppButton></p></details>
      <footer><small>判定点 {{ question.evidence_point_count ?? '待核对' }}<template v-if="question.criteria_needs_review"> · 判定点待审核</template></small><div class="qb-inline-card__footer-actions"><AppButton type="button" variant="ghost" size="small" @click="toggle">展开标注</AppButton><AppButton type="button" variant="ghost" size="small" @click="emit('similar', question)">相似题</AppButton><AppButton variant="secondary" @click="basket">{{ inBasket ? '已在试卷篮' : '加入试卷篮' }}</AppButton></div></footer>
    </div>
    <QuestionAnnotationPanel v-else :id="`qb-annotation-${question.id}`" :question="question"><template #similar><AppButton type="button" variant="ghost" size="small" @click="emit('similar', question)">相似题</AppButton></template></QuestionAnnotationPanel>
  </article>
</template>
