<script setup lang="ts">
import { computed } from 'vue'

import {
  AUTHORING_KIND_LABELS,
  AUTHORING_SOLO_LABELS,
  AUTHORING_SOLO_LEVELS,
  type AuthoringKind,
  type AuthoringSoloLevel,
  type AuthoringTaskCard,
} from '../../api/authoring'
import type { QuestionBankDetail } from '../../api/question-bank'
import AppButton from '../design-system/AppButton.vue'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'

const props = defineProps<{
  sourcePreview: QuestionBankDetail | null
  sourceState: 'idle' | 'loading' | 'ready' | 'error'
  sourceError: string
  taskCards: AuthoringTaskCard[]
  creating: boolean
  createError: string
}>()
const emit = defineEmits<{
  submit: []
  cancel: []
}>()

const KIND_ORDER: AuthoringKind[] = ['decompose', 'adapt']

const kind = defineModel<AuthoringKind>('kind', { required: true })
const title = defineModel<string>('title', { required: true })
const cardId = defineModel<string>('cardId', { required: true })
const keepKnowledge = defineModel<boolean>('keepKnowledge', { required: true })
const difficulty = defineModel<string>('difficulty', { required: true })
const solo = defineModel<'' | AuthoringSoloLevel>('solo', { required: true })
const note = defineModel<string>('note', { required: true })

const selectedCard = computed(() => (
  props.taskCards.find((card) => card.id === cardId.value) ?? null
))
</script>

<template>
  <section class="authoring__panel" aria-labelledby="authoring-create-title">
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
        <label v-for="option in KIND_ORDER" :key="option" class="authoring__radio">
          <input v-model="kind" type="radio" :value="option">
          {{ AUTHORING_KIND_LABELS[option] }}
        </label>
      </div>

      <template v-if="kind === 'adapt'">
        <div class="authoring__field">
          <label class="authoring__label" for="authoring-card">任务卡</label>
          <select class="app-input" id="authoring-card" v-model="cardId">
            <option v-for="card in taskCards" :key="card.id" :value="card.id">
              {{ card.label }}
            </option>
          </select>
          <p v-if="selectedCard" class="authoring__help">{{ selectedCard.description }}</p>
        </div>
        <div class="authoring__grid">
          <label class="authoring__checkbox">
            <input v-model="keepKnowledge" type="checkbox">
            保持考点不变
          </label>
          <label class="authoring__inline-field">
            目标难度（1–10，可留空）
            <input class="app-input" v-model="difficulty" type="number" min="1" max="10">
          </label>
          <label class="authoring__inline-field">
            目标 SOLO 层级
            <select class="app-input" v-model="solo">
              <option value="">不限</option>
              <option v-for="level in AUTHORING_SOLO_LEVELS" :key="level" :value="level">
                {{ AUTHORING_SOLO_LABELS[level] }}
              </option>
            </select>
          </label>
          <label class="authoring__inline-field authoring__inline-field--wide">
            改编说明
            <input class="app-input" v-model="note" type="text" maxlength="200" placeholder="可留空">
          </label>
        </div>
      </template>

      <div class="authoring__field">
        <label class="authoring__label" for="authoring-title">练习名称</label>
        <input class="app-input"
          id="authoring-title"
          v-model="title"
          type="text"
          maxlength="200"
          placeholder="可留空，默认使用母题题号"
        >
      </div>

      <p v-if="createError" class="authoring__error" role="alert">{{ createError }}</p>
      <div class="authoring__actions">
        <AppButton variant="primary" :disabled="creating" @click="emit('submit')">
          {{ creating ? '正在创建…' : '创建练习' }}
        </AppButton>
        <AppButton variant="ghost" :disabled="creating" @click="emit('cancel')">
          取消
        </AppButton>
      </div>
    </template>
  </section>
</template>
