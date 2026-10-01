<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { knowledgeLeafLabel, questionTypeWithSubtype, type QuestionBankListItem } from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppButton from '../design-system/AppButton.vue'
import QuestionContentRenderer from './QuestionContentRenderer.vue'
import QuestionAnnotationPanel from './QuestionAnnotationPanel.vue'

const props = defineProps<{ question: QuestionBankListItem; paperMode?: boolean; currentSkill?: string }>()
const emit = defineEmits<{ similar: [question: QuestionBankListItem]; skill: [key: string] }>()
const bank = useQuestionBankStore()
const assembly = useAssemblyStore()
const heading = ref<HTMLButtonElement | null>(null)
const expanded = computed(() => bank.selectedQuestionId === props.question.id)
const inBasket = computed(() => assembly.draft.basket_ids.includes(props.question.id))
const tags = computed(() => props.question.tags.filter(tag => tag.tag_type === 'knowledge_point').slice(0, 2))
const preview = computed(() => bank.detail?.previews.find(item => item.preview_type === 'question' && item.url))
async function toggle() { await bank.selectQuestion(expanded.value ? null : props.question.id) }
async function basket() {
  if (assembly.loadState === 'idle') await assembly.load()
  if (inBasket.value) await assembly.removeQuestion(props.question.id)
  else await assembly.addQuestions([props.question.id])
}
async function remove() {
  if (window.confirm(`确认把第 ${props.question.question_number || props.question.id} 题移出当前题库吗？删除后可立即恢复。`)) await bank.deleteCurrent()
}
watch(expanded, async (value, wasExpanded) => {
  if (!value && wasExpanded) {
    await nextTick()
    heading.value?.scrollIntoView?.({ block: 'nearest' })
    heading.value?.focus({ preventScroll: true })
  }
})
</script>

<template>
  <article :id="`qb-question-${question.id}`" class="qb-question-card qb-inline-card" :class="{ 'is-expanded': expanded, 'is-current': expanded, 'is-in-basket': inBasket }">
    <header class="qb-inline-card__heading">
      <label><input type="checkbox" :aria-label="`选择第 ${question.question_number || question.id} 题`" :checked="bank.selectedQuestionIds.includes(question.id)" :disabled="bank.selectionIsFull && !bank.selectedQuestionIds.includes(question.id)" @change="bank.toggleQuestionSelection(question.id, ($event.target as HTMLInputElement).checked)"></label>
      <button ref="heading" class="qb-inline-card__title" type="button" :aria-expanded="expanded" :aria-controls="`qb-annotation-${question.id}`" @click="toggle">第 {{ question.question_number || question.id }} 题</button>
      <span v-if="!paperMode" class="qb-inline-card__source">{{ question.paper_title || '未命名试卷' }}</span>
      <template v-if="expanded">
        <AppButton variant="secondary" :disabled="assembly.saveState === 'saving'" @click="basket">{{ inBasket ? '已在试卷篮' : '加入试卷篮' }}</AppButton>
        <details class="qb-card-menu"><summary aria-label="题目更多操作">⋯</summary><div>
          <button type="button" @click="emit('similar', question)">相似题</button>
          <RouterLink :to="{ path: '/authoring', query: { source: question.id } }">用这道题练习</RouterLink>
          <a v-if="preview?.url" :href="preview.url" target="_blank" rel="noopener">打开原卷</a>
          <button type="button" @click="remove">移出题库</button>
        </div></details>
        <AppButton variant="ghost" aria-label="收起题目详情" @click="toggle">收起 ▲</AppButton>
      </template>
    </header>
    <div v-if="!expanded" class="qb-inline-card__collapsed">
      <div class="qb-inline-card__stem"><QuestionContentRenderer :blocks="question.rich_content?.question_blocks" :fallback="question.question_text" image-alt="题目配图" media-mode="list" compact /></div>
      <div class="qb-inline-card__skills">
        <span v-if="currentSkill && question.skill_hits?.length">命中判定点：{{ question.skill_hits.map(hit => hit.point_label.split('：')[0]).join('、') }} → 本技能 · 直接</span>
        <span v-else>技能：</span>
        <button v-for="skill in question.skills ?? []" :key="skill.stable_key" type="button" class="qb-topic-capsule" @click="emit('skill', skill.stable_key)">{{ skill.display_name }}</button>
        <span v-if="question.skills?.length === 0" class="qb-warning">未挂技能</span>
      </div>
      <div class="qb-inline-card__tags"><span>{{ questionTypeWithSubtype(question.question_type, question.tags) }}</span><span>难度 {{ question.difficulty || '待定' }}</span><span v-for="tag in tags" :key="tag.tag_value" :title="tag.tag_value">{{ knowledgeLeafLabel(tag.tag_value) }}</span></div>
      <footer><small>技能 {{ question.skills?.length ?? 0 }} · {{ question.criteria_needs_review ? '判定点待审核' : '判定点可核对' }}</small><button type="button" class="qb-link" @click="toggle">展开标注</button><button type="button" class="qb-link" @click="emit('similar', question)">相似题</button><AppButton variant="secondary" :disabled="assembly.saveState === 'saving'" @click="basket">{{ inBasket ? '已在试卷篮' : '加入试卷篮' }}</AppButton></footer>
    </div>
    <QuestionAnnotationPanel v-else :id="`qb-annotation-${question.id}`" :current-skill="currentSkill" @skill="emit('skill', $event)"><template #similar><button class="qb-link" type="button" @click="emit('similar', question)">相似题</button></template></QuestionAnnotationPanel>
  </article>
</template>
