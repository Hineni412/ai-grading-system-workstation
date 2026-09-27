<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'

import { ApiError } from '../api/errors'
import {
  AUTHORING_KIND_LABELS,
  AUTHORING_QUESTION_TYPES,
  AUTHORING_SOLO_LABELS,
  AUTHORING_SOLO_LEVELS,
  authoringApi,
  authoringOperationToken,
  type AuthoringContent,
  type AuthoringKind,
  type AuthoringSoloLevel,
  type AuthoringTaskCard,
  type AuthoringWorkDetail,
  type AuthoringWorkSummary,
} from '../api/authoring'
import { questionBankApi, type QuestionBankDetail } from '../api/question-bank'
import AppButton from '../components/design-system/AppButton.vue'
import QuestionContentRenderer from '../components/question-bank/QuestionContentRenderer.vue'

const route = useRoute() as ReturnType<typeof useRoute> | undefined

// ------------------------------------------------------------ list / storage

const works = ref<AuthoringWorkSummary[]>([])
const listState = ref<'loading' | 'ready' | 'error'>('loading')
const listError = ref('')
const storageUnavailable = ref(false)

const KIND_ORDER: AuthoringKind[] = ['decompose', 'adapt']

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : '操作失败，请重试'
}

function isStorageUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'authoring_storage_unavailable'
}

async function loadWorks(): Promise<void> {
  listState.value = 'loading'
  listError.value = ''
  try {
    const result = await authoringApi.listWorks()
    works.value = result.items
    listState.value = 'ready'
  } catch (error) {
    if (isStorageUnavailable(error)) {
      storageUnavailable.value = true
      listState.value = 'ready'
      return
    }
    listError.value = errorText(error)
    listState.value = 'error'
  }
}

// ------------------------------------------------------------- create panel

const sourceId = computed(() => {
  const raw = route?.query.source
  const value = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isSafeInteger(value) && value > 0 ? value : null
})

const showCreate = ref(false)
const sourcePreview = ref<QuestionBankDetail | null>(null)
const sourceState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const sourceError = ref('')
const newKind = ref<AuthoringKind>('decompose')
const newTitle = ref('')
const taskCards = ref<AuthoringTaskCard[]>([])
const newCardId = ref('')
const cardKeepKnowledge = ref(true)
const cardDifficulty = ref('')
const cardSolo = ref<'' | AuthoringSoloLevel>('')
const cardNote = ref('')
const creating = ref(false)
const createError = ref('')
// 一次创建提交使用同一个幂等令牌，网络失败重试不会产生重复作品。
let createToken = ''

async function openCreate(): Promise<void> {
  if (sourceId.value === null) return
  showCreate.value = true
  detail.value = null
  createError.value = ''
  if (!createToken) createToken = authoringOperationToken()
  if (sourceState.value === 'idle') {
    sourceState.value = 'loading'
    try {
      sourcePreview.value = await questionBankApi.getQuestion(sourceId.value)
      sourceState.value = 'ready'
    } catch (error) {
      sourceError.value = errorText(error)
      sourceState.value = 'error'
    }
  }
  if (!taskCards.value.length) {
    try {
      taskCards.value = (await authoringApi.listTaskCards()).items
      if (!newCardId.value) newCardId.value = taskCards.value[0]?.id ?? ''
    } catch (error) {
      if (isStorageUnavailable(error)) storageUnavailable.value = true
    }
  }
}

async function submitCreate(): Promise<void> {
  if (sourceId.value === null || creating.value) return
  if (newKind.value === 'adapt' && !newCardId.value) {
    createError.value = '改编练习需要先选择一张任务卡'
    return
  }
  creating.value = true
  createError.value = ''
  try {
    const work = await authoringApi.createWork({
      kind: newKind.value,
      source_question_id: sourceId.value,
      title: newTitle.value,
      task_card: newKind.value === 'adapt'
        ? {
            method_id: newCardId.value,
            targets: {
              keep_knowledge: cardKeepKnowledge.value,
              target_difficulty: cardDifficulty.value === '' ? null : Number(cardDifficulty.value),
              target_solo: cardSolo.value || null,
              note: cardNote.value,
            },
          }
        : undefined,
      operation_token: createToken,
    })
    showCreate.value = false
    applyDetail(work)
    void loadWorks()
  } catch (error) {
    if (isStorageUnavailable(error)) {
      storageUnavailable.value = true
      showCreate.value = false
      return
    }
    createError.value = errorText(error)
  } finally {
    creating.value = false
  }
}

// ----------------------------------------------------------------- workspace

const detail = ref<AuthoringWorkDetail | null>(null)
const detailState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const detailError = ref('')
const saving = ref(false)
const saveMessage = ref('')
const saveError = ref('')
const versionConflict = ref(false)
let saveToken = ''
let saveTokenPayload = ''

interface PartRow {
  part_label: string
  predicted_difficulty: string
  predicted_solo: '' | AuthoringSoloLevel
}

interface DraftState {
  intent: string
  knowledgeText: string
  keyStepsText: string
  expectedErrorsText: string
  predictedDifficulty: string
  predictedSolo: '' | AuthoringSoloLevel
  questionText: string
  answerText: string
  questionType: string
  targetKnowledgeText: string
  caseListText: string
  notes: string
  parts: PartRow[]
}

function emptyDraft(): DraftState {
  return {
    intent: '',
    knowledgeText: '',
    keyStepsText: '',
    expectedErrorsText: '',
    predictedDifficulty: '',
    predictedSolo: '',
    questionText: '',
    answerText: '',
    questionType: '解答题',
    targetKnowledgeText: '',
    caseListText: '',
    notes: '',
    parts: [],
  }
}

const draft = reactive<DraftState>(emptyDraft())
const baseline = ref('')

function lines(text: string): string[] {
  return text.split('\n').map((line) => line.trim()).filter(Boolean)
}

function joinLines(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => String(item)).join('\n') : ''
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function numberText(value: unknown): string {
  return typeof value === 'number' && Number.isInteger(value) ? String(value) : ''
}

function soloValue(value: unknown): '' | AuthoringSoloLevel {
  return typeof value === 'string'
    && (AUTHORING_SOLO_LEVELS as readonly string[]).includes(value)
    ? value as AuthoringSoloLevel
    : ''
}

function partRows(value: unknown): PartRow[] {
  if (!Array.isArray(value)) return []
  return value.filter((item) => typeof item === 'object' && item !== null).map((item) => {
    const row = item as Record<string, unknown>
    return {
      part_label: text(row.part_label),
      predicted_difficulty: numberText(row.predicted_difficulty),
      predicted_solo: soloValue(row.predicted_solo),
    }
  })
}

function fillDraft(content: AuthoringContent | null): void {
  const source = content ?? {}
  Object.assign(draft, emptyDraft())
  draft.intent = text(source.intent)
  draft.predictedDifficulty = numberText(source.predicted_difficulty)
  draft.predictedSolo = soloValue(source.predicted_solo)
  draft.parts = partRows(source.parts)
  if (detail.value?.kind === 'adapt') {
    draft.questionText = text(source.question_text)
    draft.answerText = text(source.answer_text)
    draft.questionType = text(source.question_type) || '解答题'
    draft.targetKnowledgeText = joinLines(source.target_knowledge)
    draft.caseListText = joinLines(source.case_list)
    draft.notes = text(source.notes)
  } else {
    draft.knowledgeText = joinLines(source.knowledge_points)
    draft.keyStepsText = joinLines(source.key_steps)
    draft.expectedErrorsText = joinLines(source.expected_errors)
  }
  baseline.value = JSON.stringify(serializeDraft())
}

function serializeDraft(): AuthoringContent {
  const parts = draft.parts
    .map((row) => ({
      part_label: row.part_label.trim(),
      predicted_difficulty: row.predicted_difficulty === ''
        ? null
        : Number(row.predicted_difficulty),
      predicted_solo: row.predicted_solo || null,
    }))
    .filter((row) => row.part_label)
  const difficulty = draft.predictedDifficulty === ''
    ? null
    : Number(draft.predictedDifficulty)
  if (detail.value?.kind === 'adapt') {
    return {
      question_text: draft.questionText,
      answer_text: draft.answerText,
      question_type: draft.questionType,
      intent: draft.intent,
      target_knowledge: lines(draft.targetKnowledgeText),
      parts,
      predicted_difficulty: difficulty,
      expected_errors: lines(draft.expectedErrorsText),
      case_list: lines(draft.caseListText),
      notes: draft.notes,
      figure_asset_ids: [],
    }
  }
  return {
    intent: draft.intent,
    knowledge_points: lines(draft.knowledgeText),
    key_steps: lines(draft.keyStepsText),
    expected_errors: lines(draft.expectedErrorsText),
    predicted_difficulty: difficulty,
    predicted_solo: draft.predictedSolo || null,
    parts,
  }
}

const dirty = computed(() => (
  detail.value !== null && JSON.stringify(serializeDraft()) !== baseline.value
))

function applyDetail(work: AuthoringWorkDetail): void {
  detail.value = work
  detailState.value = 'ready'
  fillDraft(work.latest_content)
  saveToken = authoringOperationToken()
  saveTokenPayload = ''
  saveMessage.value = ''
  saveError.value = ''
  versionConflict.value = false
  compareA.value = null
  compareB.value = null
  versionCache.clear()
}

async function openWork(workId: string): Promise<void> {
  if (detail.value?.work_id === workId) return
  if (dirty.value && !window.confirm('当前练习有未保存的修改，切换后将丢失。确定切换吗？')) {
    return
  }
  showCreate.value = false
  detailState.value = 'loading'
  detailError.value = ''
  try {
    applyDetail(await authoringApi.getWork(workId))
  } catch (error) {
    if (isStorageUnavailable(error)) {
      storageUnavailable.value = true
      return
    }
    detail.value = null
    detailError.value = errorText(error)
    detailState.value = 'error'
  }
}

async function saveVersion(): Promise<void> {
  const work = detail.value
  if (!work || saving.value) return
  if (work.kind === 'adapt' && !draft.questionText.trim()) {
    saveError.value = '题干不能为空'
    return
  }
  const content = serializeDraft()
  const payload = JSON.stringify(content)
  // 同一令牌只能对应同一内容：内容没变说明是上一次提交的重试，沿用令牌；
  // 内容变了说明是一次新的保存，换一个新令牌。
  if (!saveToken || saveTokenPayload !== payload) {
    saveToken = authoringOperationToken()
  }
  saveTokenPayload = payload
  saving.value = true
  saveError.value = ''
  saveMessage.value = ''
  versionConflict.value = false
  try {
    const saved = await authoringApi.saveVersion(work.work_id, {
      content,
      base_version: work.current_version,
      operation_token: saveToken,
    })
    saveMessage.value = `已保存为第 ${saved.version_no} 版`
    saveToken = authoringOperationToken()
    saveTokenPayload = ''
    baseline.value = JSON.stringify(serializeDraft())
    await openWorkReload(work.work_id)
    void loadWorks()
  } catch (error) {
    if (error instanceof ApiError && error.code === 'authoring_version_conflict') {
      versionConflict.value = true
      saveError.value = '这份练习在其他地方已经保存了新版本。请重新加载最新内容后再保存。'
    } else if (error instanceof ApiError && error.code === 'authoring_token_conflict') {
      versionConflict.value = true
      saveError.value = '这次保存与上一次未完成的保存冲突，请重新加载后再保存。'
    } else {
      saveError.value = errorText(error)
    }
  } finally {
    saving.value = false
  }
}

async function openWorkReload(workId: string): Promise<void> {
  try {
    applyDetail(await authoringApi.getWork(workId))
  } catch {
    // 保存已成功，详情刷新失败不影响结果展示。
  }
}

async function reloadAfterConflict(): Promise<void> {
  const work = detail.value
  if (!work) return
  if (!window.confirm('重新加载会丢弃当前未保存的修改，确定吗？')) return
  applyDetail(await authoringApi.getWork(work.work_id))
}

async function removeWork(work: AuthoringWorkSummary): Promise<void> {
  const label = work.title || `第 ${work.source_question_number || '?'} 题`
  if (!window.confirm(`确定删除「${label}」吗？已保存的版本会一起隐藏。`)) return
  try {
    await authoringApi.deleteWork(work.work_id)
    if (detail.value?.work_id === work.work_id) {
      detail.value = null
      detailState.value = 'idle'
    }
    await loadWorks()
  } catch (error) {
    listError.value = errorText(error)
    listState.value = 'error'
  }
}

function addPart(): void {
  draft.parts.push({
    part_label: `（${draft.parts.length + 1}）`,
    predicted_difficulty: '',
    predicted_solo: '',
  })
}

function removePart(index: number): void {
  draft.parts.splice(index, 1)
}

// ------------------------------------------------------------- version diff

const compareA = ref<number | null>(null)
const compareB = ref<number | null>(null)
const versionCache = new Map<number, AuthoringContent>()

const CONTENT_FIELD_LABELS: Record<string, string> = {
  intent: '命题意图',
  knowledge_points: '考点',
  key_steps: '关键步骤',
  expected_errors: '预期错法',
  predicted_difficulty: '预估难度',
  predicted_solo: '预估SOLO',
  parts: '小问',
  question_text: '题干',
  answer_text: '答案',
  question_type: '题型',
  target_knowledge: '目标考点',
  case_list: '分类讨论',
  notes: '备注',
}

function formatField(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === 'object' && item !== null && 'part_label' in item) {
        const part = item as Record<string, unknown>
        const bits = [String(part.part_label ?? '')]
        if (part.predicted_difficulty != null) bits.push(`难度${part.predicted_difficulty}`)
        if (part.predicted_solo) {
          bits.push(AUTHORING_SOLO_LABELS[part.predicted_solo as AuthoringSoloLevel] ?? String(part.predicted_solo))
        }
        return bits.join(' ')
      }
      return String(item)
    }).join('\n')
  }
  return JSON.stringify(value)
}

interface CompareRow {
  key: string
  label: string
  a: string
  b: string
  changed: boolean
}

const compareRows = ref<CompareRow[]>([])
const compareLoading = ref(false)

async function runCompare(): Promise<void> {
  const work = detail.value
  const a = compareA.value
  const b = compareB.value
  if (!work || a === null || b === null || a === b) {
    compareRows.value = []
    return
  }
  compareLoading.value = true
  try {
    for (const versionNo of [a, b]) {
      if (!versionCache.has(versionNo)) {
        versionCache.set(
          versionNo,
          (await authoringApi.getVersion(work.work_id, versionNo)).content,
        )
      }
    }
    const contentA = versionCache.get(a) ?? {}
    const contentB = versionCache.get(b) ?? {}
    const keys = [...new Set([...Object.keys(contentA), ...Object.keys(contentB)])]
      .filter((key) => key !== 'figure_asset_ids')
    compareRows.value = keys.map((key) => {
      const textA = formatField(contentA[key])
      const textB = formatField(contentB[key])
      return {
        key,
        label: CONTENT_FIELD_LABELS[key] ?? key,
        a: textA,
        b: textB,
        changed: textA !== textB,
      }
    })
  } finally {
    compareLoading.value = false
  }
}

// ------------------------------------------------------- decompose 对照

const tagLabels: Record<string, string> = {
  knowledge_point: '知识点',
  ability: '能力',
  method: '解题方法',
  thought: '数学思想',
  model: '模型',
  error_type: '错误类型',
  exam_scope: '教材章节/考试范围',
  special_type: '特殊题型',
}

const bankKnowledgeTags = computed(() => {
  const tags = detail.value?.source_snapshot.question.tags ?? []
  return tags
    .filter((tag) => tag.tag_type in tagLabels)
    .map((tag) => `${tagLabels[tag.tag_type]}：${tag.tag_value.split(/[|｜]/).pop()}`)
})

const bankJudgmentTargets = computed(() => (
  (detail.value?.source_snapshot.judgment_points?.points ?? [])
    .map((point) => String(point.target ?? ''))
    .filter(Boolean)
))

const bankErrorPatterns = computed(() => (
  (detail.value?.source_snapshot.error_patterns ?? [])
    .map((pattern) => (
      [pattern.category, pattern.pattern].filter(Boolean).join('：')
    ))
    .filter(Boolean)
))

const bankPartDifficulties = computed(() => (
  (detail.value?.source_snapshot.part_assessments ?? [])
    .map((part) => (
      `${part.label || part.part_id || '小问'}：公式难度 ${part.difficulty ?? '未定'}`
    ))
))

const compareDecompose = computed(() => {
  const work = detail.value
  if (!work || work.kind !== 'decompose' || work.current_version < 1) return null
  const content = work.latest_content ?? {}
  const snapshot = work.source_snapshot
  return {
    capturedAt: snapshot.captured_at,
    rows: [
      {
        label: '命题意图',
        mine: text(content.intent),
        bank: snapshot.judgment_points?.rationale || '题库暂无',
      },
      {
        label: '考点',
        mine: joinLines(content.knowledge_points).replace(/\n/g, '；'),
        bank: bankKnowledgeTags.value.join('；') || '题库暂无',
      },
      {
        label: '关键步骤/判定点',
        mine: joinLines(content.key_steps).replace(/\n/g, '；'),
        bank: bankJudgmentTargets.value.join('；') || '题库暂无',
      },
      {
        label: '预期错法/典型错法',
        mine: joinLines(content.expected_errors).replace(/\n/g, '；'),
        bank: bankErrorPatterns.value.join('；') || '题库暂无',
      },
      {
        label: '整题难度',
        mine: numberText(content.predicted_difficulty) || '未填',
        bank: snapshot.question.difficulty || '题库暂无',
      },
      {
        label: '小问难度',
        mine: formatField(content.parts),
        bank: bankPartDifficulties.value.join('；') || '题库暂无',
      },
    ],
  }
})

const selectedCard = computed(() => (
  taskCards.value.find((card) => card.id === newCardId.value) ?? null
))

const detailCardLabel = computed(() => {
  const card = detail.value?.task_card
  if (!card) return ''
  return taskCards.value.find((item) => item.id === card.method_id)?.label
    ?? card.method_id
})

// ------------------------------------------------------------------ lifecycle

onMounted(async () => {
  await loadWorks()
  if (sourceId.value !== null) await openCreate()
})

watch(sourceId, (value, previous) => {
  if (value === null || value === previous) return
  createToken = ''
  sourceState.value = 'idle'
  sourcePreview.value = null
  newCardId.value = ''
  void openCreate()
})

function confirmLeave(): boolean {
  return !dirty.value
    || window.confirm('当前练习有未保存的修改，离开后将丢失。确定离开吗？')
}

if (route) {
  onBeforeRouteLeave(() => confirmLeave())
}
</script>

<template>
  <section class="authoring">
    <header class="authoring__hero">
      <div>
        <p class="authoring__kicker">ITEM AUTHORING</p>
        <h1 tabindex="-1">命题练习</h1>
        <p>拆解好题、按任务卡改编，保存每一版命题。</p>
      </div>
    </header>

    <p v-if="storageUnavailable" class="authoring__notice" role="status">
      命题练习的数据表还没有建立，需要先完成数据库升级。
    </p>

    <template v-else>
      <div class="authoring__layout">
        <aside class="authoring__list" aria-label="练习列表">
          <h2>我的练习</h2>
          <p v-if="listState === 'loading'" role="status">正在读取练习列表…</p>
          <p v-else-if="listState === 'error'" class="authoring__error" role="alert">
            {{ listError }}
            <button type="button" class="authoring__link" @click="loadWorks">重试</button>
          </p>
          <template v-else>
            <p v-if="!works.length" class="authoring__empty">
              还没有练习。在题库中打开一道题，点“用这道题练习”开始。
            </p>
            <ul v-else class="authoring__works">
              <li v-for="work in works" :key="work.work_id">
                <button
                  type="button"
                  class="authoring__work"
                  :class="{ 'is-active': detail?.work_id === work.work_id }"
                  @click="openWork(work.work_id)"
                >
                  <span class="authoring__work-kind">{{ AUTHORING_KIND_LABELS[work.kind] }}</span>
                  <strong>{{ work.title || `第 ${work.source_question_number || '?'} 题` }}</strong>
                  <span class="authoring__work-snippet">{{ work.source_question_snippet }}</span>
                  <span class="authoring__work-meta">
                    已存 {{ work.current_version }} 版 · {{ work.updated_at }}
                  </span>
                </button>
                <button
                  type="button"
                  class="authoring__link authoring__work-delete"
                  :aria-label="`删除练习 ${work.title || work.work_id}`"
                  @click="removeWork(work)"
                >
                  删除
                </button>
              </li>
            </ul>
          </template>
        </aside>

        <div class="authoring__main">
          <!-- 新建练习 -->
          <section v-if="showCreate" class="authoring__panel" aria-labelledby="authoring-create-title">
            <h2 id="authoring-create-title">新建练习</h2>
            <p v-if="sourceState === 'loading'" role="status">正在读取母题…</p>
            <p v-else-if="sourceState === 'error'" class="authoring__error" role="alert">
              {{ sourceError }}
            </p>
            <template v-else-if="sourcePreview">
              <div class="authoring__source">
                <p class="authoring__eyebrow">
                  母题 · 第 {{ sourcePreview.question_number || sourcePreview.id }} 题
                </p>
                <QuestionContentRenderer
                  :blocks="sourcePreview.rich_content.question_blocks"
                  :fallback="sourcePreview.question_text"
                  image-alt="母题配图"
                  media-mode="detail"
                  dense
                />
              </div>

              <div class="authoring__field">
                <span class="authoring__label">练习类型</span>
                <label v-for="kind in KIND_ORDER" :key="kind" class="authoring__radio">
                  <input v-model="newKind" type="radio" :value="kind">
                  {{ AUTHORING_KIND_LABELS[kind] }}
                </label>
              </div>

              <template v-if="newKind === 'adapt'">
                <div class="authoring__field">
                  <label class="authoring__label" for="authoring-card">任务卡</label>
                  <select id="authoring-card" v-model="newCardId">
                    <option v-for="card in taskCards" :key="card.id" :value="card.id">
                      {{ card.label }}
                    </option>
                  </select>
                  <p v-if="selectedCard" class="authoring__help">{{ selectedCard.description }}</p>
                </div>
                <div class="authoring__grid">
                  <label class="authoring__checkbox">
                    <input v-model="cardKeepKnowledge" type="checkbox">
                    保持考点不变
                  </label>
                  <label class="authoring__inline-field">
                    目标难度（1–10，可留空）
                    <input v-model="cardDifficulty" type="number" min="1" max="10">
                  </label>
                  <label class="authoring__inline-field">
                    目标 SOLO 层级
                    <select v-model="cardSolo">
                      <option value="">不限</option>
                      <option v-for="level in AUTHORING_SOLO_LEVELS" :key="level" :value="level">
                        {{ AUTHORING_SOLO_LABELS[level] }}
                      </option>
                    </select>
                  </label>
                  <label class="authoring__inline-field authoring__inline-field--wide">
                    改编说明
                    <input v-model="cardNote" type="text" maxlength="200" placeholder="可留空">
                  </label>
                </div>
              </template>

              <div class="authoring__field">
                <label class="authoring__label" for="authoring-title">练习名称</label>
                <input
                  id="authoring-title"
                  v-model="newTitle"
                  type="text"
                  maxlength="200"
                  placeholder="可留空，默认使用母题题号"
                >
              </div>

              <p v-if="createError" class="authoring__error" role="alert">{{ createError }}</p>
              <div class="authoring__actions">
                <AppButton variant="primary" :disabled="creating" @click="submitCreate">
                  {{ creating ? '正在创建…' : '创建练习' }}
                </AppButton>
                <AppButton variant="ghost" :disabled="creating" @click="showCreate = false">
                  取消
                </AppButton>
              </div>
            </template>
          </section>

          <!-- 练习工作区 -->
          <section v-else-if="detail" class="authoring__panel" aria-labelledby="authoring-work-title">
            <header class="authoring__work-header">
              <div>
                <p class="authoring__eyebrow">
                  {{ AUTHORING_KIND_LABELS[detail.kind] }}
                  <template v-if="detail.kind === 'adapt' && detail.task_card">
                    · 任务卡：{{ detailCardLabel }}
                  </template>
                </p>
                <h2 id="authoring-work-title">
                  {{ detail.title || `第 ${detail.source_question_number || '?'} 题` }}
                </h2>
                <p class="authoring__meta">
                  当前已保存 {{ detail.current_version }} 版
                  <template v-if="dirty"> · 有未保存修改</template>
                </p>
              </div>
            </header>

            <div class="authoring__source">
              <p class="authoring__eyebrow">
                母题 · 第 {{ detail.source_snapshot.question.question_number || detail.source_question_id }} 题
                （创建练习时的题库资料）
              </p>
              <QuestionContentRenderer
                :blocks="detail.source_snapshot.question.rich_content.question_blocks"
                :fallback="detail.source_snapshot.question.question_text"
                image-alt="母题配图"
                media-mode="detail"
                dense
              />
            </div>

            <!-- 拆解编辑器 -->
            <div v-if="detail.kind === 'decompose'" class="authoring__editor">
              <label class="authoring__field">
                <span class="authoring__label">命题意图</span>
                <textarea v-model="draft.intent" rows="2" placeholder="这道题想考什么、为什么是好题" />
              </label>
              <label class="authoring__field">
                <span class="authoring__label">考点（每行一条）</span>
                <textarea v-model="draft.knowledgeText" rows="2" placeholder="例如：勾股定理" />
              </label>
              <label class="authoring__field">
                <span class="authoring__label">关键步骤（每行一条）</span>
                <textarea v-model="draft.keyStepsText" rows="3" placeholder="解题必须经过的步骤" />
              </label>
              <label class="authoring__field">
                <span class="authoring__label">预期错法（每行一条）</span>
                <textarea v-model="draft.expectedErrorsText" rows="2" placeholder="学生容易在哪一步出错" />
              </label>
              <div class="authoring__grid">
                <label class="authoring__inline-field">
                  预估难度（1–10，可留空）
                  <input v-model="draft.predictedDifficulty" type="number" min="1" max="10">
                </label>
                <label class="authoring__inline-field">
                  预估 SOLO 层级
                  <select v-model="draft.predictedSolo">
                    <option value="">不填</option>
                    <option v-for="level in AUTHORING_SOLO_LEVELS" :key="level" :value="level">
                      {{ AUTHORING_SOLO_LABELS[level] }}
                    </option>
                  </select>
                </label>
              </div>
            </div>

            <!-- 改编编辑器 -->
            <div v-else class="authoring__editor">
              <label class="authoring__field">
                <span class="authoring__label">题干（必填）</span>
                <textarea v-model="draft.questionText" rows="4" placeholder="输入或粘贴改编后的题干" />
                <span class="authoring__help">公式按输入的样子显示，可直接写 x^2、√3 这类写法。</span>
              </label>
              <div v-if="draft.questionText.trim()" class="authoring__preview">
                <p class="authoring__eyebrow">预览</p>
                <QuestionContentRenderer
                  :fallback="draft.questionText"
                  typeset-text
                  image-alt="题干配图"
                  media-mode="detail"
                  dense
                />
              </div>
              <label class="authoring__field">
                <span class="authoring__label">答案与解析</span>
                <textarea v-model="draft.answerText" rows="3" />
              </label>
              <div class="authoring__grid">
                <label class="authoring__inline-field">
                  题型
                  <select v-model="draft.questionType">
                    <option v-for="type in AUTHORING_QUESTION_TYPES" :key="type" :value="type">
                      {{ type }}
                    </option>
                  </select>
                </label>
                <label class="authoring__inline-field">
                  预估难度（1–10，可留空）
                  <input v-model="draft.predictedDifficulty" type="number" min="1" max="10">
                </label>
              </div>
              <label class="authoring__field">
                <span class="authoring__label">命题意图</span>
                <textarea v-model="draft.intent" rows="2" placeholder="这次改编想达到什么效果" />
              </label>
              <label class="authoring__field">
                <span class="authoring__label">目标考点（每行一条）</span>
                <textarea v-model="draft.targetKnowledgeText" rows="2" />
              </label>
              <label class="authoring__field">
                <span class="authoring__label">预期错法（每行一条）</span>
                <textarea v-model="draft.expectedErrorsText" rows="2" />
              </label>
              <label class="authoring__field">
                <span class="authoring__label">分类讨论情况（每行一条）</span>
                <textarea v-model="draft.caseListText" rows="2" />
              </label>
              <label class="authoring__field">
                <span class="authoring__label">备注</span>
                <textarea v-model="draft.notes" rows="2" />
              </label>
            </div>

            <!-- 小问表 -->
            <div class="authoring__field">
              <span class="authoring__label">小问预估</span>
              <table v-if="draft.parts.length" class="authoring__parts">
                <thead>
                  <tr><th>小问</th><th>难度（1–10）</th><th>SOLO 层级</th><th /></tr>
                </thead>
                <tbody>
                  <tr v-for="(part, index) in draft.parts" :key="index">
                    <td><input v-model="part.part_label" type="text" aria-label="小问标号"></td>
                    <td>
                      <input
                        v-model="part.predicted_difficulty"
                        type="number" min="1" max="10"
                        :aria-label="`小问 ${part.part_label || index + 1} 难度`"
                      >
                    </td>
                    <td>
                      <select
                        v-model="part.predicted_solo"
                        :aria-label="`小问 ${part.part_label || index + 1} SOLO 层级`"
                      >
                        <option value="">不填</option>
                        <option v-for="level in AUTHORING_SOLO_LEVELS" :key="level" :value="level">
                          {{ AUTHORING_SOLO_LABELS[level] }}
                        </option>
                      </select>
                    </td>
                    <td>
                      <button
                        type="button" class="authoring__link"
                        :aria-label="`删除小问 ${part.part_label || index + 1}`"
                        @click="removePart(index)"
                      >删除</button>
                    </td>
                  </tr>
                </tbody>
              </table>
              <button type="button" class="authoring__link" @click="addPart">添加小问</button>
            </div>

            <p v-if="saveError" class="authoring__error" role="alert">
              {{ saveError }}
              <button
                v-if="versionConflict" type="button" class="authoring__link"
                @click="reloadAfterConflict"
              >重新加载最新内容</button>
            </p>
            <p v-else-if="saveMessage" class="authoring__ok" role="status">{{ saveMessage }}</p>
            <div class="authoring__actions">
              <AppButton variant="primary" :disabled="saving || !dirty" @click="saveVersion">
                {{ saving ? '正在保存…' : '保存为新版本' }}
              </AppButton>
            </div>

            <!-- 拆解对照 -->
            <section v-if="compareDecompose" class="authoring__compare" aria-label="与题库资料对照">
              <h3>与题库资料对照（创建练习时的题库资料）</h3>
              <table class="authoring__compare-table">
                <thead>
                  <tr><th>项目</th><th>我的填写</th><th>题库已有资料</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in compareDecompose.rows" :key="row.label">
                    <th scope="row">{{ row.label }}</th>
                    <td>{{ row.mine || '未填' }}</td>
                    <td>{{ row.bank }}</td>
                  </tr>
                </tbody>
              </table>
            </section>

            <!-- 版本与对比 -->
            <section v-if="detail.versions.length" class="authoring__versions" aria-label="已保存版本">
              <h3>已保存版本</h3>
              <div class="authoring__compare-controls">
                <label class="authoring__inline-field">
                  版本一
                  <select v-model.number="compareA" aria-label="对比版本一" @change="runCompare">
                    <option :value="null">选择版本</option>
                    <option v-for="v in detail.versions" :key="v.version_no" :value="v.version_no">
                      第 {{ v.version_no }} 版（{{ v.created_at }}）
                    </option>
                  </select>
                </label>
                <label class="authoring__inline-field">
                  版本二
                  <select v-model.number="compareB" aria-label="对比版本二" @change="runCompare">
                    <option :value="null">选择版本</option>
                    <option v-for="v in detail.versions" :key="v.version_no" :value="v.version_no">
                      第 {{ v.version_no }} 版（{{ v.created_at }}）
                    </option>
                  </select>
                </label>
              </div>
              <p v-if="compareLoading" role="status">正在对比…</p>
              <table v-else-if="compareRows.length" class="authoring__compare-table">
                <thead>
                  <tr><th>内容</th><th>第 {{ compareA }} 版</th><th>第 {{ compareB }} 版</th></tr>
                </thead>
                <tbody>
                  <tr
                    v-for="row in compareRows" :key="row.key"
                    :class="{ 'is-changed': row.changed }"
                  >
                    <th scope="row">{{ row.label }}</th>
                    <td>{{ row.a }}</td>
                    <td>{{ row.b }}</td>
                  </tr>
                </tbody>
              </table>
            </section>
          </section>

          <p v-else-if="detailState === 'loading'" class="authoring__notice" role="status">
            正在打开练习…
          </p>
          <p v-else-if="detailState === 'error'" class="authoring__error" role="alert">
            {{ detailError }}
          </p>
          <p v-else class="authoring__notice">
            在左侧选择一份练习；或在题库中打开一道题，点“用这道题练习”开始。
          </p>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.authoring {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding: var(--space-6);
}

.authoring__hero h1 {
  font-family: var(--font-family-display);
  font-size: 24px;
  margin: 0;
}

.authoring__hero p {
  color: var(--color-text-secondary);
  margin: var(--space-1) 0 0;
}

.authoring__kicker,
.authoring__eyebrow {
  color: var(--color-text-muted);
  font-size: 12px;
  letter-spacing: .08em;
  margin: 0 0 var(--space-1);
}

.authoring__layout {
  align-items: flex-start;
  display: grid;
  gap: var(--space-5);
  grid-template-columns: minmax(240px, 300px) minmax(0, 1fr);
}

.authoring__list {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-panel);
  padding: var(--space-4);
}

.authoring__list h2 {
  font-size: 15px;
  margin: 0 0 var(--space-3);
}

.authoring__works {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: 0;
}

.authoring__works li {
  display: flex;
  gap: var(--space-2);
  align-items: flex-start;
}

.authoring__work {
  background: none;
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-control);
  cursor: pointer;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: var(--space-2) var(--space-3);
  text-align: left;
}

.authoring__work.is-active {
  background: var(--color-bg-selected);
  border-color: var(--color-accent);
}

.authoring__work-kind {
  color: var(--color-accent);
  font-size: 12px;
}

.authoring__work-snippet {
  color: var(--color-text-secondary);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.authoring__work-meta {
  color: var(--color-text-muted);
  font-size: 12px;
}

.authoring__work-delete {
  flex-shrink: 0;
}

.authoring__panel {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-panel);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
}

.authoring__panel h2 {
  font-size: 17px;
  margin: 0;
}

.authoring__panel h3 {
  font-size: 14px;
  margin: 0 0 var(--space-2);
}

.authoring__source {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-control);
  padding: var(--space-3) var(--space-4);
}

.authoring__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.authoring__label {
  font-size: 13px;
  font-weight: 600;
}

.authoring__field textarea,
.authoring__field input[type='text'],
.authoring__inline-field input,
.authoring__inline-field select,
.authoring__field select,
.authoring__parts input,
.authoring__parts select {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  font: inherit;
  padding: 6px 10px;
}

.authoring__radio,
.authoring__checkbox {
  align-items: center;
  display: inline-flex;
  gap: var(--space-1);
  margin-right: var(--space-4);
}

.authoring__grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
}

.authoring__inline-field {
  display: flex;
  flex-direction: column;
  font-size: 13px;
  gap: var(--space-1);
  min-width: 160px;
}

.authoring__inline-field--wide {
  flex: 1;
}

.authoring__parts {
  border-collapse: collapse;
  margin-bottom: var(--space-2);
}

.authoring__parts th,
.authoring__parts td {
  border-bottom: 1px solid var(--color-border-subtle);
  font-size: 13px;
  padding: var(--space-1) var(--space-2);
  text-align: left;
}

.authoring__compare-table {
  border-collapse: collapse;
  width: 100%;
}

.authoring__compare-table th,
.authoring__compare-table td {
  border: 1px solid var(--color-border-subtle);
  font-size: 13px;
  padding: var(--space-2) var(--space-3);
  text-align: left;
  vertical-align: top;
  white-space: pre-wrap;
}

.authoring__compare-table tr.is-changed td {
  background: var(--color-warning-subtle);
}

.authoring__compare-controls {
  display: flex;
  gap: var(--space-4);
  margin-bottom: var(--space-3);
}

.authoring__actions {
  display: flex;
  gap: var(--space-3);
}

.authoring__link {
  background: none;
  border: none;
  color: var(--color-accent);
  cursor: pointer;
  font: inherit;
  padding: 0;
  text-decoration: underline;
}

.authoring__notice {
  color: var(--color-text-secondary);
  margin: 0;
}

.authoring__error {
  color: var(--color-danger);
  margin: 0;
}

.authoring__ok {
  color: var(--color-success);
  margin: 0;
}

.authoring__empty,
.authoring__help,
.authoring__meta {
  color: var(--color-text-muted);
  font-size: 13px;
  margin: 0;
}

.authoring__preview {
  border-left: 3px solid var(--color-border-default);
  padding-left: var(--space-3);
}

.authoring__editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.authoring__versions,
.authoring__compare {
  border-top: 1px solid var(--color-border-subtle);
  padding-top: var(--space-3);
}

.authoring__work-header {
  display: flex;
  justify-content: space-between;
}
</style>
