<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  questionBankApi,
  type QuestionBankFacets,
  type QuestionBankFilters,
  type QuestionBankListItem,
  type SimilarQuestionItem,
} from '../../api/question-bank'
import { useAssemblyStore } from '../../stores/assembly'
import QuestionContentRenderer from './QuestionContentRenderer.vue'

const emit = defineEmits<{
  edit: []
}>()

const assembly = useAssemblyStore()
const questions = ref<QuestionBankListItem[]>([])
const total = ref(0)
const totalPages = ref(0)
const state = ref<'loading' | 'ready' | 'empty' | 'error'>('loading')
const facetsState = ref<'loading' | 'ready' | 'error'>('loading')
const facets = ref<QuestionBankFacets>({
  exam_scopes: [],
  knowledge_points: [],
  question_types: [],
  years: [],
  exam_types: [],
  grades: [],
})
const expandedAnswers = ref(new Set<number>())
const similarSource = ref<QuestionBankListItem | null>(null)
const similarItems = ref<SimilarQuestionItem[]>([])
const similarState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const filters = reactive({
  page: 1,
  keyword: '',
  examScope: '',
  knowledgePoint: '',
  questionType: '',
  year: '',
  examType: '',
  grade: '',
  tagStatus: 'all' as 'all' | 'tagged' | 'untagged',
  sort: 'newest' as QuestionBankFilters['sort'],
})

const activeFilterCount = computed(() => [
  filters.keyword,
  filters.examScope,
  filters.knowledgePoint,
  filters.questionType,
  filters.year,
  filters.examType,
  filters.grade,
  filters.tagStatus === 'all' ? '' : filters.tagStatus,
].filter(Boolean).length)

onMounted(() => {
  void Promise.all([
    assembly.loadState === 'idle' ? assembly.load() : Promise.resolve(),
    loadFacets(),
    loadQuestions(),
  ])
})

function queryFilters(): QuestionBankFilters {
  return {
    page: filters.page,
    pageSize: 12,
    keyword: filters.keyword,
    knowledgePoint: filters.knowledgePoint,
    questionTypes: filters.questionType ? [filters.questionType] : [],
    years: filters.year ? [filters.year] : [],
    examTypes: filters.examType ? [filters.examType] : [],
    grades: filters.grade ? [filters.grade] : [],
    examScopes: filters.examScope ? [filters.examScope] : [],
    tagStatus: filters.tagStatus,
    sort: filters.sort,
  }
}

async function loadQuestions(resetPage = false): Promise<void> {
  if (resetPage) filters.page = 1
  state.value = 'loading'
  try {
    const result = await questionBankApi.listQuestions(queryFilters())
    questions.value = result.items
    total.value = result.total
    totalPages.value = result.total_pages
    state.value = result.items.length ? 'ready' : 'empty'
  } catch {
    state.value = 'error'
  }
}

async function loadFacets(): Promise<void> {
  facetsState.value = 'loading'
  try {
    facets.value = await questionBankApi.listFacets({ tagStatus: 'all' })
    facetsState.value = 'ready'
  } catch {
    facetsState.value = 'error'
  }
}

function chooseChapter(value: string): void {
  filters.examScope = filters.examScope === value ? '' : value
  void loadQuestions(true)
}

function chooseQuestionType(value: string): void {
  filters.questionType = filters.questionType === value ? '' : value
  void loadQuestions(true)
}

function changePage(page: number): void {
  if (page < 1 || page > totalPages.value || page === filters.page) return
  filters.page = page
  void loadQuestions()
}

function resetFilters(): void {
  Object.assign(filters, {
    page: 1,
    keyword: '',
    examScope: '',
    knowledgePoint: '',
    questionType: '',
    year: '',
    examType: '',
    grade: '',
    tagStatus: 'all',
    sort: 'newest',
  })
  void loadQuestions()
}

function isInBasket(questionId: number): boolean {
  return assembly.draft.basket_ids.includes(questionId)
}

async function toggleBasket(questionId: number): Promise<void> {
  if (isInBasket(questionId)) {
    await assembly.removeQuestion(questionId)
  } else {
    await assembly.addQuestions([questionId])
  }
}

async function clearBasket(): Promise<void> {
  if (!assembly.selectedQuestionCount) return
  const confirmed = window.confirm('确认清空当前试卷篮吗？试卷标题和导出记录不会被删除。')
  if (!confirmed) return
  await assembly.save({
    ...assembly.draft,
    basket_ids: [],
    order_ids: [],
    sections: [],
  })
}

function toggleAnswer(questionId: number): void {
  const next = new Set(expandedAnswers.value)
  if (next.has(questionId)) next.delete(questionId)
  else next.add(questionId)
  expandedAnswers.value = next
}

async function openSimilar(question: QuestionBankListItem): Promise<void> {
  similarSource.value = question
  similarItems.value = []
  similarState.value = 'loading'
  try {
    similarItems.value = (await questionBankApi.listSimilar(question.id)).items
    similarState.value = 'ready'
  } catch {
    similarState.value = 'error'
  }
}

function closeSimilar(): void {
  similarSource.value = null
  similarItems.value = []
  similarState.value = 'idle'
}

function tagsFor(question: QuestionBankListItem, tagType: string): string[] {
  return question.tags
    .filter((tag) => tag.tag_type === tagType)
    .map((tag) => tag.tag_value)
}
</script>

<template>
  <div class="assembly-browser">
    <aside class="assembly-chapters" aria-labelledby="assembly-chapter-title">
      <header>
        <div>
          <p class="assembly-kicker">TEXTBOOK CHAPTERS</p>
          <h2 id="assembly-chapter-title">教材章节</h2>
        </div>
        <button v-if="filters.examScope" type="button" class="assembly-link" @click="chooseChapter(filters.examScope)">清除</button>
      </header>
      <p class="assembly-chapters__hint">当前题库按已有“教材章节/范围”标签整理，不虚构教材层级。</p>
      <div v-if="facetsState === 'loading'" class="assembly-compact-state">正在读取章节…</div>
      <div v-else-if="facetsState === 'error'" class="assembly-compact-state">
        <span>章节计数暂不可用</span>
        <button type="button" class="assembly-link" @click="loadFacets">重试</button>
      </div>
      <nav v-else class="assembly-chapter-list" aria-label="教材章节筛选">
        <button
          type="button"
          :class="{ 'is-active': !filters.examScope }"
          @click="chooseChapter(filters.examScope)"
        >
          <span>全部章节</span><strong>{{ facets.exam_scopes.reduce((sum, item) => sum + item.count, 0) }}</strong>
        </button>
        <button
          v-for="chapter in facets.exam_scopes"
          :key="chapter.value"
          type="button"
          :class="{ 'is-active': filters.examScope === chapter.value }"
          @click="chooseChapter(chapter.value)"
        >
          <span>{{ chapter.value }}</span><strong>{{ chapter.count }}</strong>
        </button>
      </nav>
    </aside>

    <main class="assembly-browser__main">
      <section class="assembly-filter-panel" aria-label="试题筛选">
        <div class="assembly-filter-row">
          <span class="assembly-filter-label">题型</span>
          <div class="assembly-filter-chips">
            <button type="button" :class="{ 'is-active': !filters.questionType }" @click="chooseQuestionType(filters.questionType)">
              全部
            </button>
            <button
              v-for="item in facets.question_types.slice(0, 8)"
              :key="item.value"
              type="button"
              :class="{ 'is-active': filters.questionType === item.value }"
              @click="chooseQuestionType(item.value)"
            >
              {{ item.value }} <small>{{ item.count }}</small>
            </button>
          </div>
        </div>
        <div class="assembly-filter-row">
          <span class="assembly-filter-label">常用</span>
          <div class="assembly-filter-selects">
            <select v-model="filters.year" aria-label="年份" @change="loadQuestions(true)">
              <option value="">全部年份</option>
              <option v-for="item in facets.years" :key="item.value" :value="item.value">{{ item.value }}（{{ item.count }}）</option>
            </select>
            <select v-model="filters.examType" aria-label="试卷类型" @change="loadQuestions(true)">
              <option value="">全部试卷类型</option>
              <option v-for="item in facets.exam_types" :key="item.value" :value="item.value">{{ item.value }}（{{ item.count }}）</option>
            </select>
            <select v-model="filters.grade" aria-label="年级" @change="loadQuestions(true)">
              <option value="">全部年级</option>
              <option v-for="item in facets.grades" :key="item.value" :value="item.value">{{ item.value }}（{{ item.count }}）</option>
            </select>
            <select v-model="filters.tagStatus" aria-label="标签状态" @change="loadQuestions(true)">
              <option value="all">全部标签状态</option>
              <option value="tagged">核心标签完整</option>
              <option value="untagged">标签待完善</option>
            </select>
          </div>
        </div>
        <div class="assembly-filter-row is-search">
          <span class="assembly-filter-label">搜索</span>
          <input v-model="filters.keyword" type="search" placeholder="输入试题关键词" @keyup.enter="loadQuestions(true)">
          <select v-model="filters.sort" aria-label="排序" @change="loadQuestions(true)">
            <option value="newest">最近更新</option>
            <option value="difficulty">按难度</option>
            <option value="frequency_midterm">期中常见</option>
            <option value="frequency_final">期末常见</option>
            <option value="frequency_zhongkao">中考常见</option>
          </select>
          <button type="button" class="assembly-button is-primary" @click="loadQuestions(true)">搜索</button>
          <button v-if="activeFilterCount" type="button" class="assembly-button" @click="resetFilters">
            清除 {{ activeFilterCount }} 项
          </button>
        </div>
      </section>

      <div class="assembly-results-heading">
        <div>
          <h2>题库试题</h2>
          <p>共 {{ total }} 道；加入后会立即保存到本机试卷篮。</p>
        </div>
        <span v-if="filters.examScope">{{ filters.examScope }}</span>
      </div>

      <div v-if="state === 'loading'" class="assembly-state" role="status">正在读取试题…</div>
      <div v-else-if="state === 'error'" class="assembly-state" role="alert">
        <span>试题暂时无法读取。</span>
        <button type="button" class="assembly-link" @click="loadQuestions()">重新读取</button>
      </div>
      <div v-else-if="state === 'empty'" class="assembly-state">
        当前筛选下没有试题，可以清除筛选后再查看。
      </div>
      <div v-else class="assembly-question-results">
        <article
          v-for="question in questions"
          :key="question.id"
          class="assembly-result-card"
          :class="{ 'is-in-basket': isInBasket(question.id) }"
        >
          <header>
            <div>
              <strong>第 {{ question.question_number || question.id }} 题</strong>
              <span>{{ question.paper_title || '未命名试卷' }}</span>
            </div>
            <span>{{ [question.year, question.exam_type, question.grade].filter(Boolean).join(' · ') }}</span>
          </header>

          <div class="assembly-result-card__content">
            <QuestionContentRenderer
              :blocks="question.rich_content.question_blocks"
              :fallback="question.question_text"
              image-alt="题目配图"
            />
          </div>

          <div class="assembly-result-card__meta">
            <span>{{ question.question_type || '未分类' }}</span>
            <span>难度 {{ question.difficulty || '待定' }}</span>
            <span v-for="tag in tagsFor(question, 'knowledge_point').slice(0, 3)" :key="tag">{{ tag }}</span>
          </div>

          <div v-if="expandedAnswers.has(question.id)" class="assembly-result-card__answer">
            <strong>答案与解析</strong>
            <QuestionContentRenderer
              :blocks="question.rich_content.answer_blocks"
              :fallback="question.answer_text"
              empty-label="暂未录入答案或解析"
              image-alt="答案配图"
              compact
            />
          </div>

          <footer>
            <span>
              {{ question.tags.length }} 个标签
              <template v-if="question.has_images"> · 含图片</template>
            </span>
            <span>
              <button type="button" class="assembly-link" @click="toggleAnswer(question.id)">
                {{ expandedAnswers.has(question.id) ? '收起解析' : '查看解析' }}
              </button>
              <button type="button" class="assembly-link" @click="openSimilar(question)">相似题</button>
              <button
                type="button"
                class="assembly-button"
                :class="{ 'is-selected': isInBasket(question.id) }"
                :disabled="assembly.saveState === 'saving'"
                @click="toggleBasket(question.id)"
              >
                {{ isInBasket(question.id) ? '移出试卷篮' : '加入试卷篮' }}
              </button>
            </span>
          </footer>
        </article>
      </div>

      <footer v-if="totalPages > 1" class="assembly-pagination">
        <button type="button" class="assembly-button" :disabled="filters.page <= 1" @click="changePage(filters.page - 1)">上一页</button>
        <span>第 {{ filters.page }} / {{ totalPages }} 页</span>
        <button type="button" class="assembly-button" :disabled="filters.page >= totalPages" @click="changePage(filters.page + 1)">下一页</button>
      </footer>
    </main>

    <aside class="assembly-basket" aria-labelledby="assembly-basket-title">
      <header>
        <div>
          <p class="assembly-kicker">PAPER BASKET</p>
          <h2 id="assembly-basket-title">试卷篮</h2>
        </div>
        <strong>{{ assembly.selectedQuestionCount }}</strong>
      </header>
      <div class="assembly-basket__summary">
        <span><strong>{{ assembly.selectedQuestionCount }}</strong> 道题</span>
        <span><strong>{{ assembly.totalScore }}</strong> 已识别分值</span>
      </div>
      <ol v-if="assembly.orderedQuestions.length" class="assembly-basket__list">
        <li v-for="question in assembly.orderedQuestions.slice(0, 12)" :key="question.id">
          <span>
            <strong>第 {{ question.question_number || question.id }} 题</strong>
            <small>{{ question.question_type || '未分类' }} · {{ question.paper_title || '未命名试卷' }}</small>
          </span>
          <button type="button" aria-label="移出试卷篮" @click="assembly.removeQuestion(question.id)">×</button>
        </li>
      </ol>
      <p v-else class="assembly-basket__empty">从左侧题卡加入试题，这里会即时显示。</p>
      <p v-if="assembly.orderedQuestions.length > 12" class="assembly-basket__more">
        另有 {{ assembly.orderedQuestions.length - 12 }} 道题
      </p>
      <footer>
        <button type="button" class="assembly-button" :disabled="!assembly.selectedQuestionCount" @click="clearBasket">清空</button>
        <button
          type="button"
          class="assembly-button is-primary"
          :disabled="!assembly.selectedQuestionCount"
          @click="emit('edit')"
        >
          进入编辑与导出
        </button>
      </footer>
    </aside>
  </div>

  <Teleport to="body">
    <div v-if="similarSource" class="assembly-drawer-layer" role="presentation" @click.self="closeSimilar">
      <aside class="assembly-similar-drawer" role="dialog" aria-modal="true" aria-labelledby="assembly-similar-title">
        <header>
          <div>
            <p class="assembly-kicker">LOCAL SIMILARITY</p>
            <h2 id="assembly-similar-title">相似题推荐</h2>
            <p>保留原题浏览位置；推荐在本机完成，不会产生模型费用。</p>
          </div>
          <button type="button" class="assembly-drawer-close" aria-label="关闭相似题" @click="closeSimilar">×</button>
        </header>
        <div v-if="similarState === 'loading'" class="assembly-state">正在查找相似题…</div>
        <div v-else-if="similarState === 'error'" class="assembly-state">
          <span>相似题暂时无法读取。</span>
          <button type="button" class="assembly-link" @click="openSimilar(similarSource)">重新查找</button>
        </div>
        <div v-else-if="similarItems.length === 0" class="assembly-state">当前题库没有找到足够相似的题目。</div>
        <div v-else class="assembly-similar-list">
          <article v-for="item in similarItems" :key="item.id">
            <div class="assembly-similar-score">
              <strong>{{ Math.round(item.similarity_score * 100) }}%</strong>
              <span>{{ item.similarity_reasons.join(' · ') || '内容相近' }}</span>
            </div>
            <QuestionContentRenderer
              :blocks="item.rich_content.question_blocks"
              :fallback="item.question_text"
              image-alt="相似题配图"
            />
            <footer>
              <span>{{ item.paper_title || '未命名试卷' }}</span>
              <button
                type="button"
                class="assembly-button"
                :class="{ 'is-selected': isInBasket(item.id) }"
                @click="toggleBasket(item.id)"
              >
                {{ isInBasket(item.id) ? '移出试卷篮' : '加入试卷篮' }}
              </button>
            </footer>
          </article>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
