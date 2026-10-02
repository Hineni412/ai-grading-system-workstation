<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { questionBankApi, type QuestionBankFilters, type QuestionBankListItem, type QuestionSkillIndex } from '../../api/question-bank'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import AppButton from '../design-system/AppButton.vue'
const props = defineProps<{ index: QuestionSkillIndex | null; pendingCount: number }>()
const emit = defineEmits<{ question: [question: QuestionBankListItem]; skill: [key: string]; criteria: []; taxonomy: []; repair: [kind: 'skills' | 'analysis'] }>()
const scope = useCurriculumScopeStore()
const groups = ref<{ key: string; label: string; total: number; items: QuestionBankListItem[]; page: number; filter: QuestionBankFilters }[]>([])
const loading = ref(false)
const error = ref('')
let controller: AbortController | null = null
const underused = computed(() => [...new Map(props.index?.chapters.flatMap(chapter => [...chapter.cross_section_skills, ...chapter.sections.flatMap(section => section.skills)]).filter(skill => skill.question_count < 5).map(skill => [skill.stable_key, skill])).values()])
const underusedLimit = ref(20)
const descriptions: Record<string, string> = { review: '判定点需要人工确认后才计入技能证据。', unlinked: '这些题目尚未关联到可用技能，无法用于按技能选题。', incomplete: '题目拆分或分析尚未完成。' }
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
    <div class="qb-todo-list"><section v-for="group in groups" :key="group.key" class="qb-todo-group"><header><h2>{{ group.label }}</h2><button v-if="group.key === 'review'" class="qb-link" @click="emit('criteria')">判定点批量审核</button><button v-if="group.total && (group.key === 'unlinked' || group.key === 'incomplete')" class="qb-link" @click="emit('repair', group.key === 'unlinked' ? 'skills' : 'analysis')">{{ group.key === 'unlinked' ? 'AI 补挂技能' : 'AI 补齐分析' }}</button><span class="qb-todo-count">{{ group.total }}</span></header><p>{{ group.total ? descriptions[group.key] : '当前学期没有这类待处理题目。' }}</p><article v-for="question in group.items" :key="question.id"><strong>第{{ question.question_number }}题</strong><span>{{ question.paper_title }}</span><small>{{ group.key === 'unlinked' && !question.evidence_point_count ? '暂无可用判定点' : group.label }}</small><AppButton variant="secondary" @click="emit('question', question)">去处理 →</AppButton></article><button v-if="group.items.length < group.total" class="qb-link" @click="more(group)">查看更多</button></section>
    <section class="qb-todo-group"><header><h2>新词例外</h2><button class="qb-link" @click="emit('taxonomy')">去处理 →</button><span class="qb-todo-count">{{ pendingCount }}</span></header><p>分析中遇到的未收录表述，确认后归入知识点或细条目。</p></section>
    <section class="qb-todo-group"><header><h2>题量少技能</h2><span class="qb-todo-count">{{ underused.length }}</span></header><p>当前题量少于 5 道的技能。</p><article v-for="skill in underused.slice(0, underusedLimit)" :key="skill.stable_key"><span>{{ skill.display_name }} · {{ skill.question_count }} 题</span><AppButton variant="secondary" @click="emit('skill', skill.stable_key)">去处理 →</AppButton></article><button v-if="underusedLimit < underused.length" class="qb-link" @click="underusedLimit += 20">查看更多</button></section>
    </div><p class="qb-todo-note">查看清单和人工审核不产生模型费用；AI 补齐会先说明题目和缺失部分，确认后开始。</p>
  </section>
</template>
