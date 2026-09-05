<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  aiAssemblyApi,
  type AiAssemblyEssaySubtype,
  type AiAssemblyGap,
  type AiAssemblyRelaxation,
} from '../../api/ai-assembly'
import {
  questionBankApi,
  knowledgeLeafLabel,
  questionTypeWithSubtype,
  ESSAY_SUBTYPE_TAGS,
  type QuestionBankFacets,
  type QuestionBankPaper,
} from '../../api/question-bank'
import type { AssemblyQuestion } from '../../api/assembly'
import { useAiAssemblyStore } from '../../stores/ai-assembly'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import AppButton from '../design-system/AppButton.vue'
import TypewriterText from '../ui/typewriter/TypewriterText.vue'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import AiAssemblyPreflightBar from './AiAssemblyPreflightBar.vue'
import AssemblyScopeColumns from './AssemblyScopeColumns.vue'
import AssemblySpecTable from './AssemblySpecTable.vue'

const emit = defineEmits<{
  settled: []
}>()

const store = useAiAssemblyStore()
const curriculumScope = useCurriculumScopeStore()
// 部分既有单测在无 router 的环境直接挂载本组件；此时跳过模板 query 应用与页面跳转。
const route = useRoute() as ReturnType<typeof useRoute> | undefined
const router = useRouter() as ReturnType<typeof useRouter> | undefined

const papers = ref<QuestionBankPaper[]>([])
const facets = ref<QuestionBankFacets | null>(null)
const templateTypeCounts = ref<Record<string, number>>({})
const newInstruction = ref('')
const activeGap = ref<AiAssemblyGap | null>(null)
const replacing = ref<{ rowIndex: number; questionId: number } | null>(null)
const similarItems = ref<AssemblyQuestion[]>([])
const addedChapterIds = ref<string[]>([])
const similarLoading = ref(false)
const expandedAnswers = ref(new Set<number>())

const examTypeOptions = computed(() => facets.value?.exam_types.map((item) => item.value) ?? [])
const yearOptions = computed(() => (
  (facets.value?.years ?? [])
    .map((item) => Number(item.value))
    .filter((year) => Number.isSafeInteger(year))
))

const scopeVolumeLabel = computed(() => curriculumScope.selectedVolume?.label ?? '未选择学期')

// —— 题型 chips 双态兼容 ——
// 迁移前题库 facets 的题型仍是旧六值：选择题/填空题/解答题（画图）/解答题（计算）/
// 解答题/解答题（证明）。主 chips 把 4 个"解答题*"合并显示为"解答题"（题量求和），
// 子类数字从旧值括号拆出，裸"解答题"记为"未标注"。迁移后题型自然归为三类，
// 子类行改由后端 special_type facet 提供，"未标注"= 解答题总数 − 各子类之和。
const ESSAY_TYPE_NAME = '解答题'
const ESSAY_SUBTYPE_ORDER = ['画图', '计算', '证明'] as const
const ESSAY_UNLABELED = '未标注'

interface MainTypeFacet {
  value: string
  count: number
}

interface EssaySubtypeFacet {
  value: typeof ESSAY_SUBTYPE_ORDER[number] | typeof ESSAY_UNLABELED
  count: number
}

const questionTypeFacets = computed(() => facets.value?.question_types ?? [])

function isEssayFacet(value: string): boolean {
  return value === ESSAY_TYPE_NAME || value.startsWith(`${ESSAY_TYPE_NAME}（`)
}

const legacyEssayFacets = computed(() => (
  questionTypeFacets.value.some((item) => item.value.startsWith(`${ESSAY_TYPE_NAME}（`))
))

const essayTypeTotal = computed(() => (
  questionTypeFacets.value
    .filter((item) => isEssayFacet(item.value))
    .reduce((sum, item) => sum + item.count, 0)
))

// 按 facets 原顺序展开，遇到第一个"解答题*"时插入合并后的"解答题"主 chip。
const mainTypeFacets = computed<MainTypeFacet[]>(() => {
  const result: MainTypeFacet[] = []
  let essayInserted = false
  for (const item of questionTypeFacets.value) {
    if (isEssayFacet(item.value)) {
      if (!essayInserted) {
        essayInserted = true
        result.push({ value: ESSAY_TYPE_NAME, count: essayTypeTotal.value })
      }
      continue
    }
    result.push({ value: item.value, count: item.count })
  }
  return result
})

const essaySubtypeFacets = computed<EssaySubtypeFacet[]>(() => {
  type SubtypeValue = EssaySubtypeFacet['value']
  const counts = new Map<SubtypeValue, number>()
  if (legacyEssayFacets.value) {
    for (const item of questionTypeFacets.value) {
      if (!isEssayFacet(item.value)) continue
      const label = item.value.match(/（(.+)）$/)?.[1]
      const subtype: SubtypeValue = (ESSAY_SUBTYPE_ORDER as readonly string[]).includes(label ?? '')
        ? label as SubtypeValue
        : ESSAY_UNLABELED
      counts.set(subtype, (counts.get(subtype) ?? 0) + item.count)
    }
  } else {
    for (const subtype of ESSAY_SUBTYPE_ORDER) {
      const count = facets.value?.special_types.find((item) => item.value === subtype)?.count ?? 0
      if (count > 0) counts.set(subtype, count)
    }
    const labeled = [...counts.values()].reduce((sum, count) => sum + count, 0)
    const unlabeled = essayTypeTotal.value - labeled
    if (unlabeled > 0) counts.set(ESSAY_UNLABELED, unlabeled)
  }
  const order: SubtypeValue[] = [...ESSAY_SUBTYPE_ORDER, ESSAY_UNLABELED]
  return order
    .filter((subtype) => (counts.get(subtype) ?? 0) > 0)
    .map((subtype) => ({ value: subtype, count: counts.get(subtype) ?? 0 }))
})

const essaySelected = computed(() => ESSAY_TYPE_NAME in store.params.typeCounts)
const typeTotal = computed(() => (
  Object.values(store.params.typeCounts)
    .reduce((sum, count) => sum + (Number.isSafeInteger(count) ? count : 0), 0)
))

function toggleType(type: string): void {
  const next = { ...store.params.typeCounts }
  if (type in next) {
    delete next[type]
    // 取消"解答题"时子类回到"全部"，下次选中从干净状态开始。
    if (type === ESSAY_TYPE_NAME) store.params.essaySubtype = null
  } else {
    next[type] = 1
  }
  store.params.typeCounts = next
}

function setTypeCount(type: string, raw: string): void {
  const parsed = Number(raw)
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > 200) return
  store.params.typeCounts = { ...store.params.typeCounts, [type]: parsed }
}

function setEssaySubtype(subtype: 'all' | AiAssemblyEssaySubtype): void {
  store.params.essaySubtype = subtype === 'all' ? null : subtype
}

// —— 模板卡片 ——
const templatePaper = computed(() => (
  papers.value.find((paper) => paper.id === store.params.templatePaperId) ?? null
))

const templatePaperLabel = computed(() => (
  templatePaper.value?.title ?? `试卷 ${store.params.templatePaperId ?? ''}`
))

const templatePaperMeta = computed(() => {
  const paper = templatePaper.value
  if (!paper) return ''
  return [paper.year, paper.exam_type, `${paper.question_count} 题`].filter(Boolean).join(' · ')
})

const templateStructureSummary = computed(() => (
  Object.entries(templateTypeCounts.value)
    .map(([type, count]) => `${type} × ${count}`)
    .join('，')
))

function goPaperLibrary(): void {
  if (router) void router.push({ name: 'question-bank' })
}

// 题库试卷卡"用作 AI 组卷模板"跳转进来时套用模板，随后清掉 query 避免重复应用。
function applyTemplateQuery(): void {
  if (!route || !router) return
  const raw = route.query.template
  const value = Array.isArray(raw) ? raw[0] : raw
  if (!value) return
  const paperId = Number(value)
  if (Number.isSafeInteger(paperId) && paperId > 0) store.setTemplatePaper(paperId)
  const query = { ...route.query }
  delete query.template
  void router.replace({ query })
}

const stageText = computed(() => {
  const parts = [store.jobStage, store.jobDetail].filter((part) => part.trim())
  return parts.length ? parts.join('：') : '正在提交生成任务…'
})

const selectionGroups = computed(() => {
  const groups: Array<{ type: string; key: number; count: number; entries: Array<{ id: number; rowIndex: number; question: AssemblyQuestion | undefined; number: number }> }> = []
  let number = 0
  store.spec?.rows.forEach((row, rowIndex) => {
    // 只合并连续同题型区段，题序与试卷篮共用原行顺序。
    let group = groups[groups.length - 1]
    if (!group || group.type !== row.question_type) {
      group = { type: row.question_type, key: rowIndex, count: 0, entries: [] }
      groups.push(group)
    }
    group.count += row.count
    group.entries.push(...(store.selections[rowIndex] ?? []).map((id) => ({ id, rowIndex, question: store.detailsById[id], number: ++number })))
  })
  return groups
})

const hasMissing = computed(() => store.spec?.rows.some((row, index) => (store.selections[index]?.length ?? 0) < row.count) ?? false)
const scopeChapters = computed(() => (curriculumScope.catalog?.volumes ?? []).flatMap((volume) => volume.chapters.map((chapter) => ({
  id: chapter.id,
  label: `${volume.label} · ${chapter.title}`,
  points: chapter.sections.flatMap((section) => section.knowledge_points.map((point) => point.display_name)),
}))))
const currentScopeSummary = computed(() => {
  const scope = store.spec?.scope_knowledge_points ?? []
  const names = scopeChapters.value.filter((chapter) => chapter.points.some((point) => scope.includes(point))).map((chapter) => chapter.label)
  return names.length ? names.join('；') : `已选 ${scope.length} 个知识点`
})
const additionalChapters = computed(() => scopeChapters.value.filter((chapter) => chapter.points.some((point) => !store.spec?.scope_knowledge_points.includes(point))))
const activeSuggestions = computed(() => activeGap.value?.suggestions.filter((suggestion) => ['disable_dedupe', 'relax_difficulty'].includes(suggestion.step)) ?? [])

async function expandScope(): Promise<void> {
  const chapters = additionalChapters.value.filter((chapter) => addedChapterIds.value.includes(chapter.id))
  if (!chapters.length) return
  if (await store.runSelect({ preserveExisting: true, additionalKnowledgePoints: chapters.flatMap((chapter) => chapter.points), additionalScopeKeys: chapters.map((chapter) => chapter.id) })) {
    addedChapterIds.value = []
    activeGap.value = null
  }
}

function knowledgeTags(question: AssemblyQuestion): string[] {
  return question.tags.filter((tag) => tag.tag_type === 'knowledge_point').map((tag) => tag.tag_value)
}

function hasUnlabelledSubtype(question: AssemblyQuestion): boolean {
  return question.question_type === '解答题' && !question.tags.some((tag) => tag.tag_type === 'special_type' && (ESSAY_SUBTYPE_TAGS as readonly string[]).includes(tag.tag_value))
}

function isScopeFill(question: AssemblyQuestion, rowIndex: number): boolean {
  const preferred = store.spec?.rows[rowIndex]?.knowledge_points ?? []
  return preferred.length > 0 && !knowledgeTags(question).some((point) => preferred.includes(point))
}

const actualKnowledgeCount = computed(() => {
  const selectedIds = Object.values(store.selections).flat()
  if (!selectedIds.length || selectedIds.some((id) => !store.detailsById[id])) return undefined
  const scope = store.spec?.scope_knowledge_points ?? []
  return new Set(selectedIds.flatMap((id) => knowledgeTags(store.detailsById[id]!)).filter((point) => !scope.length || scope.includes(point))).size
})

function toggleArrayValue(list: string[] | number[], value: string | number): void {
  const index = list.indexOf(value as never)
  if (index >= 0) list.splice(index, 1)
  else list.push(value as never)
}

async function loadTemplateStructure(paperId: number | null): Promise<void> {
  templateTypeCounts.value = {}
  if (paperId === null) return
  try {
    const structure = await aiAssemblyApi.getTemplateStructure(paperId)
    const counts: Record<string, number> = {}
    for (const entry of structure.entries) {
      // entries 已按连续同类同难度合并行，题数必须按 count 汇总。
      counts[entry.question_type] = (counts[entry.question_type] ?? 0) + entry.count
    }
    templateTypeCounts.value = counts
  } catch {
    templateTypeCounts.value = {}
  }
}

function setDifficultyRatio(key: 'easy' | 'medium' | 'hard', raw: string): void {
  const parsed = raw.trim() === '' ? null : Number(raw)
  store.params.difficultyRatio = {
    ...store.params.difficultyRatio,
    [key]: parsed !== null && Number.isFinite(parsed) ? parsed : null,
  }
}

async function submitSpec(): Promise<void> {
  const instruction = newInstruction.value.trim()
  // 已有细目表时空指令重生成仍消耗 1 次模型调用，先向用户确认。
  if (store.spec !== null && !instruction) {
    const confirmed = globalThis.confirm(
      '未填写补充指令，将按当前表格原样重新生成细目表，仍消耗 1 次模型调用，是否继续？',
    )
    if (!confirmed) return
  }
  const ok = await store.confirmAndSubmitSpec(instruction)
  if (ok) newInstruction.value = ''
}

async function openReplace(rowIndex: number, questionId: number): Promise<void> {
  if (store.selecting || store.settling) return
  replacing.value = { rowIndex, questionId }
  similarItems.value = []
  similarLoading.value = true
  const result = await store.listReplacements(rowIndex, questionId)
  if (replacing.value?.questionId !== questionId) return
  similarLoading.value = false
  similarItems.value = result
}

async function applyReplacement(candidateId: number): Promise<void> {
  if (!replacing.value) return
  if (!await store.replaceQuestion(replacing.value.rowIndex, replacing.value.questionId, candidateId)) return
  replacing.value = null
  similarItems.value = []
}

function toggleAnswer(questionId: number): void {
  const next = new Set(expandedAnswers.value)
  if (next.has(questionId)) next.delete(questionId)
  else next.add(questionId)
  expandedAnswers.value = next
}

async function applySuggestion(gap: AiAssemblyGap, suggestion: AiAssemblyRelaxation): Promise<void> {
  if (!['disable_dedupe', 'relax_difficulty'].includes(suggestion.step)) return
  const ok = await store.runSelect({ preserveExisting: true, rowIndex: gap.row_index,
    relaxation: suggestion.step === 'disable_dedupe' ? 'dedupe' : 'difficulty' })
  if (ok) activeGap.value = store.gapByRow.get(gap.row_index) ?? null
}

async function settle(replaceExisting = false): Promise<void> {
  if (await store.settle(replaceExisting)) emit('settled')
}

watch(() => store.params.templatePaperId, (paperId) => {
  // 模板自带题型结构与子类约束：进入模板模式清掉自由组卷的子类选择，避免隐藏约束。
  if (paperId !== null) store.params.essaySubtype = null
  void loadTemplateStructure(paperId)
})

// 面板保持挂载时从题库再次跳入（换了另一张模板卷）也要套用。
watch(() => route?.query.template, () => applyTemplateQuery())

onMounted(() => {
  void curriculumScope.initialize()
  // 刷新/重进时恢复上次会话（含仍在运行的细目表 job）；
  // 会话恢复完成后再套用 URL 里的模板，保证显式跳转覆盖恢复的参数。
  void store.restoreSession().then(applyTemplateQuery)
  void store.loadPreflight()
  void questionBankApi.listPapers().then((result) => {
    papers.value = result.items
  }).catch(() => {
    papers.value = []
  })
  void questionBankApi.listFacets().then((result) => {
    facets.value = result
  }).catch(() => {
    facets.value = null
  })
})
</script>

<template>
  <div class="ai-assembly" data-testid="ai-assembly-panel">
    <p v-if="store.error" class="ai-assembly__error" role="alert">{{ store.error }}</p>

    <section v-if="store.phase === 'params'" class="ai-assembly__params">
      <h2>组卷需求</h2>

      <div class="ai-assembly__grid">
        <div class="ai-assembly__main">
          <section class="ai-assembly__card">
            <h3>
              考察范围
              <small class="ai-assembly__term">{{ scopeVolumeLabel }} · 在页面顶部切换学期</small>
            </h3>
            <AssemblyScopeColumns />
          </section>

          <section class="ai-assembly__card">
            <h3>题型与数量</h3>
            <template v-if="store.params.templatePaperId !== null">
              <p class="ai-assembly__hint">已选模板，题型与数量以模板为准。</p>
              <div v-if="Object.keys(templateTypeCounts).length" class="ai-assembly__chips">
                <span
                  v-for="[type, count] in Object.entries(templateTypeCounts)"
                  :key="type"
                  class="ai-assembly__chip is-static"
                >{{ type }} <small>{{ count }}</small></span>
              </div>
            </template>
            <template v-else>
              <p class="ai-assembly__hint">点击题型即选，再点取消；选中的填数量。</p>
              <p v-if="!mainTypeFacets.length" class="ai-assembly__hint">正在读取题库题型…</p>
              <template v-else>
                <div class="ai-assembly__type-list">
                  <span v-for="item in mainTypeFacets" :key="item.value" class="ai-assembly__type-item">
                    <button
                      type="button"
                      class="ai-assembly__chip"
                      :class="{ 'is-active': item.value in store.params.typeCounts }"
                      :aria-pressed="item.value in store.params.typeCounts"
                      @click="toggleType(item.value)"
                    >{{ item.value }} <small>{{ item.count }}</small></button>
                    <input
                      v-if="item.value in store.params.typeCounts"
                      class="ai-assembly__type-count"
                      type="number"
                      min="1"
                      max="200"
                      :value="store.params.typeCounts[item.value]"
                      :aria-label="`${item.value} 数量`"
                      @change="setTypeCount(item.value, ($event.target as HTMLInputElement).value)"
                    >
                  </span>
                </div>

                <div
                  v-if="essaySelected && essaySubtypeFacets.length"
                  class="ai-assembly__subtypes"
                  aria-label="解答题子类"
                >
                  <span class="ai-assembly__subtypes-label">解答题子类</span>
                  <button
                    type="button"
                    class="ai-assembly__chip ai-assembly__chip--sub"
                    :class="{ 'is-active': store.params.essaySubtype === null }"
                    :aria-pressed="store.params.essaySubtype === null"
                    @click="setEssaySubtype('all')"
                  >全部</button>
                  <template v-for="item in essaySubtypeFacets" :key="item.value">
                    <span
                      v-if="item.value === '未标注'"
                      class="ai-assembly__chip ai-assembly__chip--sub is-static"
                      title="未标注子类的解答题暂不支持单独限定"
                    >未标注 <small>{{ item.count }}</small></span>
                    <button
                      v-else
                      type="button"
                      class="ai-assembly__chip ai-assembly__chip--sub"
                      :class="{ 'is-active': store.params.essaySubtype === item.value }"
                      :aria-pressed="store.params.essaySubtype === item.value"
                      @click="setEssaySubtype(item.value)"
                    >{{ item.value }} <small>{{ item.count }}</small></button>
                  </template>
                </div>
                <p v-if="typeTotal" class="ai-assembly__hint">共 {{ typeTotal }} 题</p>
              </template>
            </template>
          </section>

          <section class="ai-assembly__card">
            <h3>补充说明（口语描述即可）</h3>
            <textarea
              v-model="store.params.freeText"
              rows="4"
              maxlength="2000"
              placeholder="例如：出一份 45 分钟的小测，重点考勾股定理的应用。"
            />
          </section>
        </div>

        <aside class="ai-assembly__side">
          <section class="ai-assembly__card ai-assembly__template" data-testid="ai-template-card">
            <template v-if="store.params.templatePaperId !== null">
              <div class="ai-assembly__template-head">
                <strong>{{ templatePaperLabel }}</strong>
                <button
                  type="button"
                  class="ai-assembly__template-remove"
                  @click="store.setTemplatePaper(null)"
                >移除</button>
              </div>
              <p v-if="templatePaperMeta" class="ai-assembly__hint">{{ templatePaperMeta }}</p>
              <p class="ai-assembly__hint">
                <template v-if="templateStructureSummary">
                  题型结构：{{ templateStructureSummary }}
                </template>
                <template v-else>题型结构读取中或暂不可用。</template>
              </p>
            </template>
            <template v-else>
              <div class="ai-assembly__template-head">
                <strong>未使用模板</strong>
              </div>
              <p class="ai-assembly__hint">
                选一张真卷当模板，AI 会照它的题型结构与难度组卷；不选则自由组卷。
              </p>
              <AppButton variant="ghost" @click="goPaperLibrary">去题库选真卷</AppButton>
            </template>
          </section>

          <fieldset class="ai-assembly__card ai-assembly__field-row">
            <legend>难度比例（%）</legend>
            <p v-if="store.params.templatePaperId !== null" class="ai-assembly__hint">
              已选模板，留空沿用模板难度；填入后按比例重排。
            </p>
            <div class="ai-assembly__ratio">
              <label>易
                <input
                  type="number"
                  min="0"
                  max="100"
                  aria-label="简单题比例"
                  :value="store.params.difficultyRatio.easy ?? ''"
                  :placeholder="store.params.templatePaperId !== null ? '留空沿用模板难度' : ''"
                  @change="setDifficultyRatio('easy', ($event.target as HTMLInputElement).value)"
                >
              </label>
              <label>中
                <input
                  type="number"
                  min="0"
                  max="100"
                  aria-label="中等题比例"
                  :value="store.params.difficultyRatio.medium ?? ''"
                  :placeholder="store.params.templatePaperId !== null ? '留空沿用模板难度' : ''"
                  @change="setDifficultyRatio('medium', ($event.target as HTMLInputElement).value)"
                >
              </label>
              <label>难
                <input
                  type="number"
                  min="0"
                  max="100"
                  aria-label="难题比例"
                  :value="store.params.difficultyRatio.hard ?? ''"
                  :placeholder="store.params.templatePaperId !== null ? '留空沿用模板难度' : ''"
                  @change="setDifficultyRatio('hard', ($event.target as HTMLInputElement).value)"
                >
              </label>
            </div>
          </fieldset>

          <fieldset class="ai-assembly__card ai-assembly__field-row">
            <legend>题目来源（可选）</legend>
            <p class="ai-assembly__hint">限定从哪类真卷选题，不勾用全部来源。</p>
            <div v-if="examTypeOptions.length" class="ai-assembly__source-group">
              <span>考试类型</span>
              <div class="ai-assembly__chips">
                <button
                  v-for="option in examTypeOptions"
                  :key="option"
                  type="button"
                  class="ai-assembly__chip"
                  :class="{ 'is-active': store.params.examTypes.includes(option) }"
                  @click="toggleArrayValue(store.params.examTypes, option)"
                >
                  {{ option }}
                </button>
              </div>
            </div>
            <div v-if="yearOptions.length" class="ai-assembly__source-group">
              <span>年份</span>
              <div class="ai-assembly__chips">
                <button
                  v-for="year in yearOptions"
                  :key="year"
                  type="button"
                  class="ai-assembly__chip"
                  :class="{ 'is-active': store.params.years.includes(year) }"
                  @click="toggleArrayValue(store.params.years, year)"
                >
                  {{ year }}
                </button>
              </div>
            </div>
          </fieldset>
        </aside>
      </div>

      <AiAssemblyPreflightBar
        :preflight="store.preflight"
        :loading="store.preflightState === 'loading'"
        @confirm="submitSpec"
      />
    </section>

    <section v-else-if="store.phase === 'generating'" class="ai-assembly__generating" aria-live="polite">
      <h2>正在生成细目表</h2>
      <TypewriterText :text="stageText" tag="p" />
    </section>

    <section v-else-if="store.spec" class="ai-assembly__spec">
      <label class="ai-assembly__field">
        <span>试卷标题</span>
        <input
          type="text"
          :value="store.spec.title"
          maxlength="120"
          aria-label="试卷标题"
          @change="store.setSpecTitle(($event.target as HTMLInputElement).value)"
        >
      </label>
      <p class="ai-assembly__hint">细目表由 {{ store.specModelName }} 生成，可直接改表后再选题。</p>
      <p class="ai-assembly__hint">{{ store.params.templatePaperId === null ? '自由组卷：按当前配置顺序落卷' : '模板组卷：保留模板题型、题量和顺序' }} · 考察范围：{{ currentScopeSummary }}</p>

      <fieldset class="ai-assembly__editing" :disabled="store.selecting || store.settling">
      <AssemblySpecTable
        :spec="store.spec"
        :selections="store.selections"
        :locked-question-ids="store.lockedQuestionIds"
        :gap-by-row="store.gapByRow"
        :actual-knowledge-count="actualKnowledgeCount"
        :template-mode="store.params.templatePaperId !== null"
        @edit-row="(rowIndex, patch) => store.applySpecEdit(rowIndex, patch)"
        @show-gap="(gap) => activeGap = gap"
      />
      </fieldset>

      <div v-if="activeGap" class="ai-assembly__gap" role="dialog" aria-label="缺口放宽建议">
        <h3>第 {{ activeGap.row_index + 1 }} 行缺 {{ activeGap.missing }} 题</h3>
        <p>
          范围内候选 {{ activeGap.candidates }} 题<template v-if="activeGap.excluded_by_dedupe">
            ，其中 {{ activeGap.excluded_by_dedupe }} 题因去重被排除</template>。
        </p>
        <ul>
          <li v-for="suggestion in activeSuggestions" :key="suggestion.step">
            <strong>{{ suggestion.title }}</strong>
            <span>{{ suggestion.sacrifice }}</span>
            <AppButton variant="secondary" :disabled="store.selecting || store.settling" @click="applySuggestion(activeGap, suggestion)">
              采用并补齐本行
            </AppButton>
          </li>
        </ul>
        <p v-if="!activeSuggestions.length">当前没有能直接增加候选的放宽建议。可以在下方明确增加考察章节后补题。</p>
        <p>仅补齐缺口，已有题目保留。增加候选不代表一定能补满。</p>
        <AppButton variant="ghost" @click="activeGap = null">关闭</AppButton>
      </div>

      <div class="ai-assembly__actions">
        <AppButton v-if="store.hasSelection && hasMissing" variant="primary" :loading="store.selecting" :disabled="store.settling" loading-label="正在补题" @click="store.runSelect({ preserveExisting: true })">补齐缺题</AppButton>
        <AppButton :variant="store.hasSelection && hasMissing ? 'secondary' : 'primary'" :disabled="store.selecting || store.settling" @click="store.runSelect()">
          {{ store.hasSelection ? '重新选题' : '选题' }}
        </AppButton>
        <label class="ai-assembly__dedupe">
          <input v-model="store.dedupeEnabled" type="checkbox" :disabled="store.selecting || store.settling">
          排除最近组卷已用题目
        </label>
      </div>

      <details v-if="store.gaps.length" class="ai-assembly__scope-add">
        <summary>增加考察章节后补题</summary>
        <p>保留原范围和已选题。勾选章节将加入整卷考察范围，本次只补空位。</p>
        <fieldset class="ai-assembly__editing" :disabled="store.selecting || store.settling">
          <label v-for="chapter in additionalChapters" :key="chapter.id"><input v-model="addedChapterIds" type="checkbox" :value="chapter.id">{{ chapter.label }}</label>
        </fieldset>
        <p v-if="!additionalChapters.length">暂无可增加的章节；请检查教材目录是否已加载。</p>
        <AppButton variant="secondary" :disabled="!addedChapterIds.length || store.selecting || store.settling" @click="expandScope">保留原范围，增加所选章节并补题</AppButton>
      </details>

      <div v-if="store.hasSelection" class="ai-assembly__selection">
        <section v-for="group in selectionGroups" :key="group.key" class="ai-assembly__question-group" :aria-label="`${group.type}选题结果`">
          <h3>{{ group.type }} <span>已选 {{ group.entries.length }}/{{ group.count }} 题</span></h3>
          <p v-if="!group.entries.length" class="ai-assembly__hint">此题型暂未选到题目，请查看缺口说明。</p>
          <div class="ai-assembly__questions">
            <article v-for="entry in group.entries" :key="entry.id" class="ai-assembly__question-card" :class="{ 'is-locked': store.lockedQuestionIds.includes(entry.id) }" :aria-label="`第 ${entry.number} 题`">
              <header>
                <strong>第 {{ entry.number }} 题<template v-if="entry.question?.score_value != null"> · {{ entry.question.score_value }} 分</template></strong>
                <span>{{ entry.question?.paper_title || '来源待补充' }}</span>
              </header>
              <template v-if="entry.question">
                <div class="ai-assembly__question-content">
                  <QuestionContentRenderer
                    :blocks="entry.question.rich_content?.question_blocks"
                    :fallback="entry.question.question_text"
                    image-alt="题目配图"
                    media-mode="list"
                    paper-media-flow
                    dense
                  />
                </div>
                <div class="ai-assembly__question-tags">
                  <span>{{ questionTypeWithSubtype(entry.question.question_type, entry.question.tags) }}</span>
                  <span>难度 {{ entry.question.difficulty || '待定' }}</span>
                  <span v-for="point in knowledgeTags(entry.question)" :key="point" :title="point">{{ knowledgeLeafLabel(point) }}</span>
                  <span v-if="hasUnlabelledSubtype(entry.question)" class="ai-assembly__candidate-note">子类未标注</span>
                  <span v-if="isScopeFill(entry.question, entry.rowIndex)" class="ai-assembly__candidate-note">范围内补位</span>
                </div>
                <div v-if="expandedAnswers.has(entry.id)" class="ai-assembly__answer">
                  <strong>答案与解析</strong>
                  <QuestionContentRenderer
                    :blocks="entry.question.rich_content?.answer_blocks"
                    :fallback="entry.question.answer_text"
                    empty-label="暂未录入答案或解析"
                    image-alt="答案配图"
                    media-mode="detail"
                    compact
                  />
                </div>
              </template>
              <p v-else class="ai-assembly__hint" role="status">题目内容暂未读取，请重新选题后查看。</p>
              <footer class="ai-assembly__question-actions">
                <AppButton
                  variant="ghost"
                  :aria-label="store.lockedQuestionIds.includes(entry.id) ? '解锁本题' : '锁定本题'"
                  @click="store.toggleLock(entry.id)"
                >
                  {{ store.lockedQuestionIds.includes(entry.id) ? '🔒 已锁定' : '锁定' }}
                </AppButton>
                <AppButton variant="ghost" @click="openReplace(entry.rowIndex, entry.id)">换一题</AppButton>
                <AppButton variant="ghost" @click="toggleAnswer(entry.id)">
                  {{ expandedAnswers.has(entry.id) ? '收起解析' : '查看解析' }}
                </AppButton>
              </footer>
              <div
                v-if="replacing?.questionId === entry.id"
                class="ai-assembly__similar"
                role="dialog"
                aria-label="可替换题目"
              >
                <p v-if="similarLoading">正在查找符合本行条件的题目…</p>
                <p v-else-if="!similarItems.length">没有找到符合当前范围、题型和难度的可替换题目。</p>
                <ul v-else>
                  <li v-for="item in similarItems" :key="item.id">
                    <QuestionContentRenderer :blocks="item.rich_content?.question_blocks" :fallback="item.question_text" image-alt="相似题配图" media-mode="list" compact />
                    <span>{{ item.paper_title || '来源待补充' }} · 难度 {{ item.difficulty || '待定' }}</span>
                    <AppButton variant="secondary" @click="applyReplacement(item.id)">用这题</AppButton>
                  </li>
                </ul>
                <AppButton variant="ghost" @click="replacing = null">取消</AppButton>
              </div>
            </article>
          </div>
        </section>
      </div>

      <div class="ai-assembly__regen">
        <label class="ai-assembly__field">
          <span>补充指令（可选，锁定题会保留）</span>
          <textarea
            v-model="newInstruction"
            rows="2"
            maxlength="2000"
            placeholder="例如：选择题再加一道几何题。"
          />
        </label>
        <p class="ai-assembly__hint">
          直接改表后点"选题"即可，无需重新生成；只有想用新指令调整时才点这里。
        </p>
        <AiAssemblyPreflightBar
          :preflight="store.preflight"
          :loading="store.preflightState === 'loading'"
          confirm-label="确认并重新生成细目表"
          @confirm="submitSpec"
        />
      </div>

      <div class="ai-assembly__actions">
        <AppButton
          variant="primary"
          :disabled="!store.hasSelection || store.selecting"
          :loading="store.settling"
          loading-label="正在加入试卷篮"
          @click="settle()"
        >
          加入试卷篮
        </AppButton>
        <AppButton variant="ghost" @click="store.reset()">重新开始</AppButton>
      </div>
      <div v-if="store.basketConflict" class="ai-assembly__gap" role="group" aria-label="确认替换试卷篮草稿">
        <p>试卷篮已有不同内容。替换后，草稿将与当前预览的题目和顺序一致，已导出的试卷保持不变。</p>
        <AppButton variant="secondary" :disabled="store.selecting || store.settling" @click="settle(true)">用当前组卷替换草稿</AppButton>
        <AppButton variant="ghost" @click="store.basketConflict = false">保留原草稿</AppButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ai-assembly__editing { border: 0; padding: 0; margin: 0; min-width: 0; }
.ai-assembly__scope-add { border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; }
.ai-assembly__scope-add summary { cursor: pointer; }
.ai-assembly__scope-add fieldset { display: grid; gap: 8px; max-height: 240px; overflow-y: auto; margin-bottom: 12px; }
.ai-assembly__scope-add label { display: flex; align-items: center; gap: 8px; }
.ai-assembly {
  display: grid;
  gap: 16px;
}

.ai-assembly h2 {
  font-size: 17px;
  margin: 0;
}

.ai-assembly h3 {
  font-size: 14px;
  margin: 12px 0 6px;
}

.ai-assembly__error {
  background: color-mix(in srgb, var(--color-danger) 10%, transparent);
  border-radius: 8px;
  color: var(--color-danger);
  font-size: 13px;
  margin: 0;
  padding: 8px 12px;
}

.ai-assembly__params,
.ai-assembly__spec,
.ai-assembly__generating {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel, 10px);
  display: grid;
  gap: 14px;
  padding: 16px;
}

.ai-assembly__grid {
  align-items: start;
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr);
}

@media (max-width: 1180px) {
  .ai-assembly__grid {
    grid-template-columns: 1fr;
  }
}

.ai-assembly__main,
.ai-assembly__side {
  display: grid;
  gap: 14px;
}

.ai-assembly__card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel, 10px);
  display: grid;
  gap: 10px;
  padding: 14px 16px;
}

.ai-assembly__card h3 {
  font-size: 14px;
  margin: 0;
}

.ai-assembly__term {
  color: var(--color-text-secondary);
  font-size: 11px;
  font-weight: 400;
  margin-left: 8px;
}

.ai-assembly__card textarea {
  background: var(--background);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--color-text-primary);
  font-size: 13px;
  padding: 8px 10px;
}

.ai-assembly__field {
  display: grid;
  font-size: 13px;
  gap: 6px;
}

.ai-assembly__field > span,
.ai-assembly__field legend {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.ai-assembly__field input,
.ai-assembly__field select,
.ai-assembly__field textarea {
  background: var(--background);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--color-text-primary);
  font-size: 13px;
  padding: 6px 8px;
}

.ai-assembly__field-row {
  margin: 0;
}

.ai-assembly__field-row legend {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
  padding: 0 4px;
}

.ai-assembly__ratio {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.ai-assembly__ratio label {
  align-items: center;
  color: var(--color-text-secondary);
  display: inline-flex;
  font-size: 12px;
  gap: 6px;
}

.ai-assembly__ratio input {
  background: var(--background);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--color-text-primary);
  font-size: 12px;
  padding: 4px 6px;
  width: 64px;
}

.ai-assembly__hint {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin: 0;
}

.ai-assembly__template {
  background: color-mix(in srgb, var(--primary) 5%, var(--card));
  border-color: color-mix(in srgb, var(--primary) 30%, var(--border));
}

.ai-assembly__template-head {
  align-items: center;
  display: flex;
  font-size: 13px;
  justify-content: space-between;
}

.ai-assembly__template-remove {
  background: none;
  border: 0;
  color: var(--color-danger);
  cursor: pointer;
  font-size: 12px;
  padding: 2px 4px;
}

.ai-assembly__type-list {
  align-items: start;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.ai-assembly__type-item {
  align-items: center;
  display: inline-flex;
  gap: 4px;
}

.ai-assembly__type-count {
  background: var(--background);
  border: 1px solid var(--primary);
  border-radius: 6px;
  color: var(--color-text-primary);
  font-size: 12px;
  padding: 2px 4px;
  width: 52px;
}

.ai-assembly__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ai-assembly__chip {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 12px;
  padding: 3px 10px;
}

.ai-assembly__chip:hover {
  border-color: var(--primary);
  color: var(--primary);
}

.ai-assembly__chip small {
  font-size: 11px;
  opacity: 0.75;
}

.ai-assembly__chip.is-active {
  background: var(--primary);
  border-color: var(--primary);
  color: var(--primary-foreground);
}

.ai-assembly__chip.is-static {
  cursor: default;
}

.ai-assembly__chip.is-static:hover {
  border-color: var(--border);
  color: var(--color-text-secondary);
}

.ai-assembly__chip--sub {
  font-size: 11px;
  padding: 2px 9px;
}

.ai-assembly__subtypes {
  align-items: center;
  background: var(--muted);
  border: 1px solid var(--border);
  border-radius: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 6px 10px;
}

.ai-assembly__subtypes-label {
  color: var(--color-text-secondary);
  font-size: 11px;
  font-weight: 650;
}

.ai-assembly__source-group {
  align-items: baseline;
  display: flex;
  gap: 8px;
}

.ai-assembly__source-group > span {
  color: var(--color-text-secondary);
  flex-shrink: 0;
  font-size: 12px;
}

.ai-assembly__actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.ai-assembly__dedupe {
  align-items: center;
  color: var(--color-text-secondary);
  display: inline-flex;
  font-size: 12px;
  gap: 6px;
}

.ai-assembly__questions {
  display: grid;
  gap: 8px;
  list-style: none;
  margin: 0;
  padding: 0;
}

.ai-assembly__questions > li {
  border: 1px solid var(--border);
  border-radius: 8px;
  display: grid;
  gap: 6px;
  padding: 10px 12px;
}

.ai-assembly__question-main {
  display: grid;
  gap: 2px;
}

.ai-assembly__question-text {
  font-size: 13px;
}

.ai-assembly__question-meta {
  color: var(--color-text-secondary);
  font-size: 11px;
}

.ai-assembly__question-actions {
  display: flex;
  gap: 8px;
}

.ai-assembly__answer {
  background: var(--muted);
  border-radius: 6px;
  font-size: 12px;
  margin: 0;
  padding: 8px;
  white-space: pre-wrap;
}

.ai-assembly__similar,
.ai-assembly__gap {
  border: 1px dashed var(--border);
  border-radius: 8px;
  display: grid;
  font-size: 13px;
  gap: 8px;
  padding: 10px 12px;
}

.ai-assembly__similar ul,
.ai-assembly__gap ul {
  display: grid;
  gap: 6px;
  list-style: none;
  margin: 0;
  padding: 0;
}

.ai-assembly__similar li,
.ai-assembly__gap li {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.ai-assembly__gap h3 {
  color: var(--color-danger);
  margin: 0;
}

.ai-assembly__gap li span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.ai-assembly__question-group { min-width: 0; }
.ai-assembly__question-group h3 { display: flex; align-items: baseline; gap: 12px; margin: 20px 0 10px; }
.ai-assembly__question-group h3 span { color: var(--color-text-secondary); font-size: 12px; font-weight: 400; }
.ai-assembly__question-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; min-width: 0; overflow: hidden; }
.ai-assembly__question-card.is-locked { border-color: var(--primary); }
.ai-assembly__question-card > header { align-items: baseline; background: var(--muted); display: flex; flex-wrap: wrap; gap: 8px 20px; padding: 12px 16px; font-size: 12px; }
.ai-assembly__question-card > header > span { color: var(--color-text-secondary); overflow-wrap: anywhere; }
.ai-assembly__question-content { padding: 20px; }
.ai-assembly__question-tags { color: var(--color-text-secondary); display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; padding: 0 20px 12px; }
.ai-assembly__question-tags > span { overflow-wrap: anywhere; }
.ai-assembly__candidate-note { color: var(--primary); }
.ai-assembly__question-card > .ai-assembly__answer { background: var(--muted); border-top: 1px solid var(--border); margin: 0; padding: 16px 20px; white-space: normal; }
.ai-assembly__answer > strong { display: block; margin-bottom: 8px; font-size: 13px; }
.ai-assembly__question-card > footer { border-top: 1px solid var(--border); padding: 8px 12px; }
.ai-assembly__question-card > .ai-assembly__similar { margin: 0 16px 16px; }
.ai-assembly__similar li { border-bottom: 1px solid var(--border); display: grid; padding: 12px 0; }
</style>
