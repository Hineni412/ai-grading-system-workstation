<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { questionBankApi, type QuestionBankFilters, type QuestionBankListItem, type QuestionSkillIndex } from '../../api/question-bank'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
const props = defineProps<{ index: QuestionSkillIndex | null; pendingCount: number }>()
const emit = defineEmits<{ question: [question: QuestionBankListItem]; skill: [key: string]; criteria: []; taxonomy: [] }>()
const scope = useCurriculumScopeStore()
const groups = ref<{ key: string; label: string; total: number; items: QuestionBankListItem[]; page: number; filter: QuestionBankFilters }[]>([])
const loading = ref(false)
const error = ref('')
let controller: AbortController | null = null
const underused = computed(() => [...new Map(props.index?.chapters.flatMap(chapter => [...chapter.cross_section_skills, ...chapter.sections.flatMap(section => section.skills)]).filter(skill => skill.question_count < 5).map(skill => [skill.stable_key, skill])).values()])
async function load() {
  controller?.abort()
  if (!scope.selectedVolumeId) { groups.value = []; return }
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = ''
  const base: QuestionBankFilters = { page: 1, pageSize: 20, includeSkills: true, curriculumVolumeIds: [scope.selectedVolumeId] }
  const definitions = [{ key: 'review', label: '判定点待审核', filter: { ...base, criteriaNeedsReview: true } }, { key: 'unlinked', label: '未挂技能', filter: { ...base, skillUnlinked: true } }, { key: 'incomplete', label: '分析未完成', filter: { ...base, analysisStatus: 'incomplete' as const } }]
  try {
    const results = await Promise.all(definitions.map(async group => ({ ...group, ...(await questionBankApi.listQuestions(group.filter, request.signal)) })))
    if (!request.signal.aborted) groups.value = results
  } catch { if (!request.signal.aborted) error.value = '待处理题目暂时无法读取，请重试。' }
  finally { if (!request.signal.aborted) loading.value = false }
}
async function more(group: typeof groups.value[number]) {
  try { const page = await questionBankApi.listQuestions({ ...group.filter, page: group.page + 1 }, controller?.signal); group.items.push(...page.items); group.page = page.page }
  catch { error.value = '后续题目读取失败，请重试。' }
}
watch([() => scope.selectedVolumeId, () => props.index], () => void load(), { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <section class="qb-todo">
    <p v-if="!scope.selectedVolumeId">请在侧栏选择教学学期。</p>
    <div class="qb-todo-stats"><span v-for="group in groups" :key="group.key">{{ group.label }} <strong>{{ group.total }}</strong></span><span>新词例外 <strong>{{ pendingCount }}</strong></span><span>题量少技能 <strong>{{ underused.length }}</strong></span></div>
    <p v-if="loading" role="status">正在读取待处理题目…</p><p v-if="error" role="alert">{{ error }} <button class="qb-link" @click="load">重试</button></p>
    <section v-for="group in groups" :key="group.key" class="qb-todo-group"><header><h2>{{ group.label }} · {{ group.total }}</h2><button v-if="group.key === 'review'" class="qb-link" @click="emit('criteria')">判定点批量审核</button></header><p v-if="!group.total">当前学期没有这类待处理题目。</p><article v-for="question in group.items" :key="question.id"><span>第 {{ question.question_number }} 题 · {{ question.paper_title }}</span><button class="qb-link" @click="emit('question', question)">去处理 →</button></article><button v-if="group.items.length < group.total" class="qb-link" @click="more(group)">查看更多</button></section>
    <section class="qb-todo-group"><header><h2>新词例外 · {{ pendingCount }}</h2><button class="qb-link" @click="emit('taxonomy')">去处理 →</button></header></section>
    <section class="qb-todo-group"><header><h2>题量少技能 · {{ underused.length }}</h2></header><article v-for="skill in underused" :key="skill.stable_key"><span>{{ skill.display_name }} · {{ skill.question_count }} 题</span><button class="qb-link" @click="emit('skill', skill.stable_key)">去处理 →</button></article></section>
    <p class="qb-help">以上处理不会调用模型产生费用。重新生成等操作仍需确认。</p>
  </section>
</template>
