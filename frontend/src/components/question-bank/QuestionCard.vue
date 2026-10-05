<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { knowledgeLeafLabel, questionTypeWithSubtype, type QuestionBankListItem } from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import { useConfirm } from '../../composables/useConfirm'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppButton from '../design-system/AppButton.vue'
import QuestionContentRenderer from './QuestionContentRenderer.vue'
import QuestionAnnotationPanel from './QuestionAnnotationPanel.vue'

const props = defineProps<{ question: QuestionBankListItem; paperMode?: boolean; currentSkill?: string }>()
const emit = defineEmits<{ similar: [question: QuestionBankListItem]; skill: [key: string] }>()
const bank = useQuestionBankStore()
const assembly = useAssemblyStore()
const { confirm } = useConfirm()
const heading = ref<HTMLButtonElement | null>(null)
const expanded = computed(() => bank.selectedQuestionId === props.question.id)
const inBasket = computed(() => assembly.draft.basket_ids.includes(props.question.id))
const tags = computed(() => props.question.tags.filter(tag => tag.tag_type === 'knowledge_point').slice(0, 2))
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
      <label v-if="!expanded" class="qb-inline-card__select"><input type="checkbox" :aria-label="`选择第 ${question.question_number || question.id} 题`" :checked="bank.selectedQuestionIds.includes(question.id)" :disabled="bank.selectionIsFull && !bank.selectedQuestionIds.includes(question.id)" @change="bank.toggleQuestionSelection(question.id, ($event.target as HTMLInputElement).checked)">选入</label>
      <button v-if="!expanded" type="button" class="qb-inline-card__chevron" aria-label="展开题目详情" @click="toggle">▾</button>
      <div v-if="expanded" class="qb-inline-card__heading-actions">
      <template v-if="expanded">
        <AppButton variant="secondary" :disabled="assembly.saveState === 'saving'" @click="basket">{{ inBasket ? '已在试卷篮' : '加入试卷篮' }}</AppButton>
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
      <div class="qb-inline-card__skills">
        <span v-if="currentSkill && question.skill_hits?.length">命中判定点：{{ question.skill_hits.map(hit => hit.point_label.split('：')[0]).join('、') }} → 本技能 · 直接</span>
        <span v-else>技能：</span>
        <span v-if="currentSkill && question.skills?.some(skill => skill.stable_key !== currentSkill)">｜还关联：</span>
        <button v-for="skill in (question.skills ?? []).filter(skill => skill.stable_key !== currentSkill)" :key="skill.stable_key" type="button" class="qb-topic-capsule" @click="emit('skill', skill.stable_key)">{{ skill.display_name }}</button>
        <span v-if="question.skills?.length === 0" class="qb-warning">未挂技能</span>
      </div>
      <div class="qb-inline-card__tags"><span>{{ questionTypeWithSubtype(question.question_type, question.tags) }}</span><span>难度 {{ question.difficulty || '待定' }}</span><span v-for="tag in tags" :key="tag.tag_value" :title="tag.tag_value">{{ knowledgeLeafLabel(tag.tag_value) }}</span></div>
      <details v-if="question.duplicate_members?.length" class="qb-duplicate-members"><summary>另有 {{ question.duplicate_members.length }} 道相同题 ▸</summary><p v-for="member in question.duplicate_members" :key="member.id"><AppButton :to="{ path: '/question-bank', query: { tab: 'paper', paper: member.paper_id, question: member.id } }" variant="ghost" size="small" :as="RouterLink">第 {{ member.question_number }} 题 · {{ member.paper_title }}</AppButton></p></details>
      <footer><small>判定点 {{ question.evidence_point_count ?? '待核对' }} · 技能 {{ question.skills?.length ?? 0 }}<template v-if="question.criteria_needs_review"> · 判定点待审核</template></small><div class="qb-inline-card__footer-actions"><AppButton type="button" variant="ghost" size="small" @click="toggle">展开标注</AppButton><AppButton type="button" variant="ghost" size="small" @click="emit('similar', question)">相似题</AppButton><AppButton variant="secondary" :disabled="assembly.saveState === 'saving'" @click="basket">{{ inBasket ? '已在试卷篮' : '加入试卷篮' }}</AppButton></div></footer>
    </div>
    <QuestionAnnotationPanel v-else :id="`qb-annotation-${question.id}`" :current-skill="currentSkill" @skill="emit('skill', $event)"><template #similar><AppButton type="button" variant="ghost" size="small" @click="emit('similar', question)">相似题</AppButton></template></QuestionAnnotationPanel>
  </article>
</template>
