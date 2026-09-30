<script setup lang="ts">
import { computed } from 'vue'

import {
  AUTHORING_KIND_LABELS,
  AUTHORING_QUESTION_TYPES,
  AUTHORING_SOLO_LABELS,
  AUTHORING_SOLO_LEVELS,
  type AuthoringTaskCard,
  type AuthoringWorkDetail,
} from '../../api/authoring'
import AppButton from '../design-system/AppButton.vue'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import { formatField, type DraftState } from './authoring-draft'
import type { CompareRow } from './useAuthoringCompare'

const props = defineProps<{
  detail: AuthoringWorkDetail
  draft: DraftState
  dirty: boolean
  saving: boolean
  saveMessage: string
  saveError: string
  versionConflict: boolean
  compareRows: CompareRow[]
  compareLoading: boolean
  taskCards: AuthoringTaskCard[]
}>()
const emit = defineEmits<{
  save: []
  'reload-after-conflict': []
  compare: []
  'add-part': []
  'remove-part': [index: number]
}>()

// draft 由父级持有，本组件在字段编辑中原地修改它，不替换对象本身。
const draft = props.draft
const compareA = defineModel<number | null>('compareA', { required: true })
const compareB = defineModel<number | null>('compareB', { required: true })

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
  const tags = props.detail.source_snapshot.question.tags ?? []
  return tags
    .filter((tag) => tag.tag_type in tagLabels)
    .map((tag) => `${tagLabels[tag.tag_type]}：${tag.tag_value.split(/[|｜]/).pop()}`)
})

const bankJudgmentTargets = computed(() => (
  (props.detail.source_snapshot.judgment_points?.points ?? [])
    .map((point) => String(point.target ?? ''))
    .filter(Boolean)
))

const bankErrorPatterns = computed(() => (
  (props.detail.source_snapshot.error_patterns ?? [])
    .map((pattern) => (
      [pattern.category, pattern.pattern].filter(Boolean).join('：')
    ))
    .filter(Boolean)
))

const bankPartDifficulties = computed(() => (
  (props.detail.source_snapshot.part_assessments ?? [])
    .map((part) => (
      `${part.label || part.part_id || '小问'}：公式难度 ${part.difficulty ?? '未定'}`
    ))
))

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function joinLines(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => String(item)).join('\n') : ''
}

function numberText(value: unknown): string {
  return typeof value === 'number' && Number.isInteger(value) ? String(value) : ''
}

const compareDecompose = computed(() => {
  const work = props.detail
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

const detailCardLabel = computed(() => {
  const card = props.detail.task_card
  if (!card) return ''
  return props.taskCards.find((item) => item.id === card.method_id)?.label
    ?? card.method_id
})
</script>

<template>
  <section class="authoring__panel" aria-labelledby="authoring-work-title">
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
              <AppButton
                variant="ghost"
                :aria-label="`删除小问 ${part.part_label || index + 1}`"
                @click="emit('remove-part', index)"
              >删除</AppButton>
            </td>
          </tr>
        </tbody>
      </table>
      <AppButton variant="ghost" @click="emit('add-part')">添加小问</AppButton>
    </div>

    <p v-if="saveError" class="authoring__error" role="alert">
      {{ saveError }}
      <AppButton
        v-if="versionConflict" variant="ghost"
        @click="emit('reload-after-conflict')"
      >重新加载最新内容</AppButton>
    </p>
    <p v-else-if="saveMessage" class="authoring__ok" role="status">{{ saveMessage }}</p>
    <div class="authoring__actions">
      <AppButton variant="primary" :disabled="saving || !dirty" @click="emit('save')">
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
          <select v-model.number="compareA" aria-label="对比版本一" @change="emit('compare')">
            <option :value="null">选择版本</option>
            <option v-for="v in detail.versions" :key="v.version_no" :value="v.version_no">
              第 {{ v.version_no }} 版（{{ v.created_at }}）
            </option>
          </select>
        </label>
        <label class="authoring__inline-field">
          版本二
          <select v-model.number="compareB" aria-label="对比版本二" @change="emit('compare')">
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
</template>
