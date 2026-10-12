<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { useAssemblyStore } from '../../stores/assembly'
import { useConfirm } from '../../composables/useConfirm'
import AppButton from '../design-system/AppButton.vue'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AssemblyPaperSheet from '../question-assembly/AssemblyPaperSheet.vue'
import '../../styles/question-assembly.css'

const props = defineProps<{ open: boolean; context: 'class' | 'bank'; covered?: number; total?: number }>()
const emit = defineEmits<{ 'update:open': [open: boolean]; replace: [id: number]; proceed: [] }>()

const assembly = useAssemblyStore()
const router = useRouter()
const { confirm } = useConfirm()

const rules = computed(() => (
  typeof assembly.draft.practice_rules === 'object' && assembly.draft.practice_rules
    ? assembly.draft.practice_rules
    : null
))
const isTraining = computed(() => rules.value?.purpose === 'training')
const minutes = computed(() => assembly.orderedQuestions.reduce(
  (sum, question) => sum + (/选择/.test(question.question_type ?? '') ? 2 : question.question_type === '填空题' ? 3 : 8),
  0,
))
const counts = computed(() => assembly.orderedQuestions.reduce(
  (result, question) => {
    if (/选择/.test(question.question_type || '')) result.choice += 1
    else if (question.question_type === '填空题') result.fill += 1
    else result.essay += 1
    return result
  },
  { choice: 0, fill: 0, essay: 0 },
))
const difficultyText = computed(() => {
  const values = assembly.orderedQuestions
    .map((question) => Number(question.difficulty))
    .filter((value) => Number.isFinite(value) && value > 0)
  if (!values.length) return '—'
  return `${Math.min(...values)}–${Math.max(...values)}`
})
const unmetRules = computed(() => {
  if (!rules.value) return 0
  const codes = new Set((assembly.draft.rule_violations ?? []).map((violation) => violation.code))
  let unmet = codes.size
  if (isTraining.value && (assembly.selectedQuestionCount < 8 || assembly.selectedQuestionCount > 12)) unmet += 1
  return unmet
})

watch(() => props.open, (open) => {
  if (open && assembly.loadState === 'idle') void assembly.load()
})

function openRules(): void {
  void router.push({ path: '/question-assembly', query: { mode: 'edit', rules: '1' } })
}

async function clear(): Promise<void> {
  if (!await confirm({
    title: `清空试卷篮中的 ${assembly.selectedQuestionCount} 道题？`,
    message: '题库题目仍保留。',
    confirmLabel: '清空',
    danger: true,
  })) return
  await assembly.save({
    ...assembly.draft,
    basket_ids: [],
    order_ids: [],
    sections: assembly.draft.sections.map((section) => ({ ...section, question_ids: [] })),
    practice_rules: false,
  })
}
</script>

<template>
  <aside v-if="open" class="assembly-basket" aria-label="试卷篮">
    <header class="assembly-basket__head">
      <h2>试卷篮</h2>
      <span class="assembly-basket__title" :title="assembly.draft.title">{{ assembly.draft.title || '未命名试卷' }}</span>
      <button type="button" class="assembly-basket__x" aria-label="收起试卷篮" @click="emit('update:open', false)">×</button>
    </header>
    <div class="assembly-basket__scroll">
      <div class="assembly-basket__sum">
        <p class="assembly-basket__line"><b>{{ assembly.selectedQuestionCount }} 题</b> · 约 {{ minutes }} 分钟<template v-if="context === 'class' && total"> · 覆盖失分题 {{ covered ?? 0 }}/{{ total }}</template></p>
        <p class="assembly-basket__comp">选 {{ counts.choice }} · 填 {{ counts.fill }} · 解 {{ counts.essay }}<template v-if="rules">/{{ rules.max_written_questions }}</template> · 难度 {{ difficultyText }}</p>
        <button
          v-if="rules"
          type="button"
          class="assembly-basket__rules"
          :class="unmetRules ? 'is-warning' : 'is-ok'"
          title="查看出卷设置"
          @click="openRules"
        >{{ unmetRules ? `有 ${unmetRules} 条未满足` : '规则全部满足 ✓' }}</button>
      </div>
      <p v-if="assembly.loadState === 'loading'" role="status" class="assembly-basket__note">正在读取试卷篮…</p>
      <FeedbackBanner v-if="assembly.message" role="status" tone="info" :description="assembly.message" />
      <FeedbackBanner v-if="assembly.missingQuestionIds.length" tone="warning" description="部分篮中题目已不可用，请到编辑页核对。" />
      <AssemblyPaperSheet variant="drawer" :swap="context === 'class'" @replace="(id) => emit('replace', id)" />
    </div>
    <footer class="assembly-basket__foot">
      <AppButton
        v-if="context === 'class'"
        variant="primary"
        @click="emit('proceed')"
      >{{ isTraining ? '去整理与生成 →' : '去整理与导出 →' }}</AppButton>
      <AppButton
        v-else
        variant="primary"
        :to="{ path: '/question-assembly', query: { mode: 'edit' } }"
        :as="RouterLink"
      >{{ isTraining ? '去整理与生成 →' : '去整理与导出 →' }}</AppButton>
      <AppButton variant="ghost" class="is-danger" :disabled="!assembly.selectedQuestionCount" @click="clear">清空</AppButton>
    </footer>
  </aside>
</template>
