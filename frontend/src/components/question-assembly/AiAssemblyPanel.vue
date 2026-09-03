<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { aiAssemblyApi, type AiAssemblyGap, type AiAssemblyRelaxation } from '../../api/ai-assembly'
import {
  questionBankApi,
  type CurriculumCatalog,
  type QuestionBankFacets,
  type QuestionBankPaper,
  type SimilarQuestionItem,
} from '../../api/question-bank'
import { useAiAssemblyStore } from '../../stores/ai-assembly'
import AppButton from '../design-system/AppButton.vue'
import TypewriterText from '../ui/typewriter/TypewriterText.vue'
import AiAssemblyPreflightBar from './AiAssemblyPreflightBar.vue'
import AssemblyScopePicker from './AssemblyScopePicker.vue'
import AssemblySpecTable from './AssemblySpecTable.vue'

const emit = defineEmits<{
  settled: []
}>()

const store = useAiAssemblyStore()

const papers = ref<QuestionBankPaper[]>([])
const facets = ref<QuestionBankFacets | null>(null)
const catalog = ref<CurriculumCatalog | null>(null)
const scopePickerOpen = ref(false)
const catalogRequested = ref(false)
const templateTypeCounts = ref<Record<string, number>>({})
const newInstruction = ref('')
const activeGap = ref<AiAssemblyGap | null>(null)
const replacing = ref<{ rowIndex: number; questionId: number } | null>(null)
const similarItems = ref<SimilarQuestionItem[]>([])
const similarLoading = ref(false)
const expandedAnswers = ref(new Set<number>())

const examTypeOptions = computed(() => facets.value?.exam_types.map((item) => item.value) ?? [])
const yearOptions = computed(() => (
  (facets.value?.years ?? [])
    .map((item) => Number(item.value))
    .filter((year) => Number.isSafeInteger(year))
))
const questionTypeOptions = computed(() => facets.value?.question_types.map((item) => item.value) ?? [])
const typeCountEntries = computed(() => (
  store.params.templatePaperId !== null
    ? Object.entries(templateTypeCounts.value)
    : Object.entries(store.params.typeCounts)
))

const stageText = computed(() => {
  const parts = [store.jobStage, store.jobDetail].filter((part) => part.trim())
  return parts.length ? parts.join('：') : '正在提交生成任务…'
})

function summarize(text: string): string {
  const clean = text.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim()
  return clean.length > 80 ? `${clean.slice(0, 80)}…` : clean
}

function toggleArrayValue(list: string[] | number[], value: string | number): void {
  const index = list.indexOf(value as never)
  if (index >= 0) list.splice(index, 1)
  else list.push(value as never)
}

async function openScopePicker(): Promise<void> {
  scopePickerOpen.value = !scopePickerOpen.value
  if (scopePickerOpen.value && !catalogRequested.value) {
    catalogRequested.value = true
    try {
      catalog.value = await questionBankApi.getCurriculum()
    } catch {
      catalog.value = null
    }
  }
}

async function loadTemplateStructure(paperId: number | null): Promise<void> {
  templateTypeCounts.value = {}
  if (paperId === null) return
  try {
    const structure = await aiAssemblyApi.getTemplateStructure(paperId)
    const counts: Record<string, number> = {}
    for (const entry of structure.entries) {
      counts[entry.question_type] = (counts[entry.question_type] ?? 0) + 1
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

function addTypeCount(): void {
  const candidate = questionTypeOptions.value.find((type) => !(type in store.params.typeCounts))
  const type = candidate ?? `题型${Object.keys(store.params.typeCounts).length + 1}`
  store.params.typeCounts = { ...store.params.typeCounts, [type]: 1 }
}

function removeTypeCount(type: string): void {
  const next = { ...store.params.typeCounts }
  delete next[type]
  store.params.typeCounts = next
}

async function submitSpec(): Promise<void> {
  const instruction = newInstruction.value.trim()
  const ok = await store.confirmAndSubmitSpec(instruction)
  if (ok) newInstruction.value = ''
}

async function openReplace(rowIndex: number, questionId: number): Promise<void> {
  replacing.value = { rowIndex, questionId }
  similarItems.value = []
  similarLoading.value = true
  const result = await store.listSimilar(questionId)
  similarLoading.value = false
  similarItems.value = result?.items ?? []
}

async function applyReplacement(candidateId: number): Promise<void> {
  if (!replacing.value) return
  await store.replaceQuestion(replacing.value.rowIndex, replacing.value.questionId, candidateId)
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
  const row = store.spec?.rows[gap.row_index]
  if (!row) return
  if (suggestion.step === 'disable_dedupe') {
    store.dedupeEnabled = false
  } else if (suggestion.step === 'relax_difficulty') {
    store.applySpecEdit(gap.row_index, { difficulty: null })
  } else if (suggestion.step === 'neighbor_knowledge') {
    store.applySpecEdit(gap.row_index, {
      knowledge_points: [...new Set([...row.knowledge_points, ...suggestion.knowledge_points])],
    })
  } else if (suggestion.step === 'relax_scope' && store.spec) {
    store.spec.scope_knowledge_points = []
    store.applySpecEdit(gap.row_index, {})
  }
  activeGap.value = null
  await store.runSelect()
}

async function settle(): Promise<void> {
  if (await store.settle()) emit('settled')
}

watch(() => store.params.templatePaperId, (paperId) => {
  void loadTemplateStructure(paperId)
})

onMounted(() => {
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

      <label class="ai-assembly__field">
        <span>真卷模板（可选）</span>
        <select
          :value="store.params.templatePaperId ?? ''"
          aria-label="真卷模板"
          @change="store.setTemplatePaper(($event.target as HTMLSelectElement).value === '' ? null : Number(($event.target as HTMLSelectElement).value))"
        >
          <option value="">自由组卷（不使用模板）</option>
          <option v-for="paper in papers" :key="paper.id" :value="paper.id">
            {{ paper.title ?? `试卷 ${paper.id}` }}
          </option>
        </select>
      </label>

      <div class="ai-assembly__field">
        <span>考察范围</span>
        <AppButton variant="secondary" @click="openScopePicker">
          {{ store.params.scopeKeys.length ? `已选 ${store.params.scopeKeys.length} 项` : '选择章节与知识点' }}
        </AppButton>
        <AssemblyScopePicker
          v-if="scopePickerOpen"
          :catalog="catalog"
          :selected-keys="store.params.scopeKeys"
          @toggle="(key, checked, descendants) => store.setScopeChecked(key, checked, descendants)"
        />
      </div>

      <fieldset class="ai-assembly__field">
        <legend>难度比例（%）</legend>
        <p v-if="store.params.templatePaperId !== null" class="ai-assembly__hint">
          已选模板，留空沿用模板难度；填入后按比例重排。
        </p>
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
      </fieldset>

      <fieldset class="ai-assembly__field">
        <legend>题型与数量</legend>
        <p v-if="store.params.templatePaperId !== null" class="ai-assembly__hint">已选模板，题型与数量以模板为准。</p>
        <ul class="ai-assembly__type-counts">
          <li v-for="[type, count] in typeCountEntries" :key="type">
            <span>{{ type }}</span>
            <input
              type="number"
              min="1"
              max="200"
              :value="count"
              :disabled="store.params.templatePaperId !== null"
              :aria-label="`${type} 数量`"
              @change="store.params.typeCounts = { ...store.params.typeCounts, [type]: Number(($event.target as HTMLInputElement).value) }"
            >
            <AppButton
              v-if="store.params.templatePaperId === null"
              variant="ghost"
              :aria-label="`删除 ${type}`"
              @click="removeTypeCount(type)"
            >
              删除
            </AppButton>
          </li>
        </ul>
        <AppButton
          v-if="store.params.templatePaperId === null"
          variant="secondary"
          @click="addTypeCount"
        >
          添加题型
        </AppButton>
      </fieldset>

      <div v-if="examTypeOptions.length" class="ai-assembly__field">
        <span>考试类型（可多选）</span>
        <div class="ai-assembly__chips">
          <button
            v-for="option in examTypeOptions"
            :key="option"
            type="button"
            :class="{ 'is-active': store.params.examTypes.includes(option) }"
            @click="toggleArrayValue(store.params.examTypes, option)"
          >
            {{ option }}
          </button>
        </div>
      </div>

      <div v-if="yearOptions.length" class="ai-assembly__field">
        <span>年份（可多选）</span>
        <div class="ai-assembly__chips">
          <button
            v-for="year in yearOptions"
            :key="year"
            type="button"
            :class="{ 'is-active': store.params.years.includes(year) }"
            @click="toggleArrayValue(store.params.years, year)"
          >
            {{ year }}
          </button>
        </div>
      </div>

      <label class="ai-assembly__field">
        <span>补充说明（口语描述即可）</span>
        <textarea
          v-model="store.params.freeText"
          rows="3"
          maxlength="2000"
          placeholder="例如：出一份 45 分钟的小测，重点考勾股定理的应用。"
        />
      </label>

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

      <AssemblySpecTable
        :spec="store.spec"
        :selections="store.selections"
        :locked-question-ids="store.lockedQuestionIds"
        :gap-by-row="store.gapByRow"
        @edit-row="(rowIndex, patch) => store.applySpecEdit(rowIndex, patch)"
        @show-gap="(gap) => activeGap = gap"
      />

      <div v-if="activeGap" class="ai-assembly__gap" role="dialog" aria-label="缺口放宽建议">
        <h3>第 {{ activeGap.row_index + 1 }} 行缺 {{ activeGap.missing }} 题</h3>
        <p>
          范围内候选 {{ activeGap.candidates }} 题<template v-if="activeGap.excluded_by_dedupe">
            ，其中 {{ activeGap.excluded_by_dedupe }} 题因去重被排除</template>。
        </p>
        <ul>
          <li v-for="suggestion in activeGap.suggestions" :key="suggestion.step">
            <strong>{{ suggestion.title }}</strong>
            <span>{{ suggestion.sacrifice }}</span>
            <AppButton variant="secondary" @click="applySuggestion(activeGap, suggestion)">
              采用并重选
            </AppButton>
          </li>
        </ul>
        <AppButton variant="ghost" @click="activeGap = null">关闭</AppButton>
      </div>

      <div class="ai-assembly__actions">
        <AppButton variant="primary" :loading="store.selecting" loading-label="正在选题" @click="store.runSelect">
          {{ store.hasSelection ? '重新选题' : '选题' }}
        </AppButton>
        <label class="ai-assembly__dedupe">
          <input v-model="store.dedupeEnabled" type="checkbox">
          排除最近组卷已用题目
        </label>
      </div>

      <div v-if="store.hasSelection" class="ai-assembly__selection">
        <template v-for="(row, rowIndex) in store.spec.rows" :key="rowIndex">
          <h3>{{ row.question_type }}（{{ (store.selections[rowIndex] ?? []).length }}/{{ row.count }}）</h3>
          <ul class="ai-assembly__questions">
            <li v-for="questionId in store.selections[rowIndex] ?? []" :key="questionId">
              <div class="ai-assembly__question-main">
                <span class="ai-assembly__question-text">
                  {{ summarize(store.detailsById[questionId]?.question_text ?? `题目 ${questionId}`) }}
                </span>
                <span class="ai-assembly__question-meta">
                  {{ store.detailsById[questionId]?.paper_title ?? '' }}
                </span>
              </div>
              <div class="ai-assembly__question-actions">
                <AppButton
                  variant="ghost"
                  :aria-label="store.lockedQuestionIds.includes(questionId) ? '解锁本题' : '锁定本题'"
                  @click="store.toggleLock(questionId)"
                >
                  {{ store.lockedQuestionIds.includes(questionId) ? '🔒 已锁定' : '🔓 锁定' }}
                </AppButton>
                <AppButton variant="ghost" @click="openReplace(rowIndex, questionId)">换一题</AppButton>
                <AppButton variant="ghost" @click="toggleAnswer(questionId)">
                  {{ expandedAnswers.has(questionId) ? '收起解析' : '查看解析' }}
                </AppButton>
              </div>
              <p v-if="expandedAnswers.has(questionId)" class="ai-assembly__answer">
                {{ store.detailsById[questionId]?.answer_text ?? '暂无解析' }}
              </p>
              <div
                v-if="replacing?.questionId === questionId"
                class="ai-assembly__similar"
                role="dialog"
                aria-label="相似题候选"
              >
                <p v-if="similarLoading">正在查找相似题…</p>
                <p v-else-if="!similarItems.length">没有找到可替换的相似题。</p>
                <ul v-else>
                  <li v-for="item in similarItems" :key="item.id">
                    <span>{{ summarize(item.question_text) }}</span>
                    <AppButton variant="secondary" @click="applyReplacement(item.id)">用这题</AppButton>
                  </li>
                </ul>
                <AppButton variant="ghost" @click="replacing = null">取消</AppButton>
              </div>
            </li>
          </ul>
        </template>
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
        <AiAssemblyPreflightBar
          :preflight="store.preflight"
          :loading="store.preflightState === 'loading'"
          @confirm="submitSpec"
        />
      </div>

      <div class="ai-assembly__actions">
        <AppButton
          variant="primary"
          :disabled="!store.hasSelection"
          :loading="store.settling"
          loading-label="正在加入试卷篮"
          @click="settle"
        >
          加入试卷篮
        </AppButton>
        <AppButton variant="ghost" @click="store.reset()">重新开始</AppButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
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
  background: color-mix(in srgb, var(--color-danger, #dc2626) 10%, transparent);
  border-radius: 8px;
  color: var(--color-danger, #dc2626);
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
  background: var(--background, #fff);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--color-text-primary);
  font-size: 13px;
  padding: 6px 8px;
}

.ai-assembly__field fieldset,
fieldset.ai-assembly__field {
  border: 1px solid var(--border);
  border-radius: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 10px 12px;
}

.ai-assembly__hint {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin: 0;
}

.ai-assembly__type-counts {
  display: grid;
  gap: 6px;
  list-style: none;
  margin: 0;
  padding: 0;
}

.ai-assembly__type-counts li {
  align-items: center;
  display: flex;
  gap: 8px;
}

.ai-assembly__type-counts input {
  max-width: 72px;
}

.ai-assembly__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ai-assembly__chips button {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 12px;
  padding: 3px 10px;
}

.ai-assembly__chips button.is-active {
  background: var(--primary);
  border-color: var(--primary);
  color: var(--primary-foreground, #fff);
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
  background: var(--muted, #f1f5f9);
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
  color: var(--color-danger, #dc2626);
  margin: 0;
}

.ai-assembly__gap li span {
  color: var(--color-text-secondary);
  font-size: 12px;
}
</style>
