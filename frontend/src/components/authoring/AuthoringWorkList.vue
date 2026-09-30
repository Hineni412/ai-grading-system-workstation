<script setup lang="ts">
import {
  AUTHORING_KIND_LABELS,
  type AuthoringWorkSummary,
} from '../../api/authoring'
import AppButton from '../design-system/AppButton.vue'

defineProps<{
  works: AuthoringWorkSummary[]
  listState: 'loading' | 'ready' | 'error'
  listError: string
  activeWorkId: string | null
}>()
const emit = defineEmits<{
  open: [workId: string]
  remove: [work: AuthoringWorkSummary]
  retry: []
}>()
</script>

<template>
  <aside class="authoring__list" aria-label="练习列表">
    <h2>我的练习</h2>
    <p v-if="listState === 'loading'" role="status">正在读取练习列表…</p>
    <p v-else-if="listState === 'error'" class="authoring__error" role="alert">
      {{ listError }}
      <AppButton variant="ghost" @click="emit('retry')">重试</AppButton>
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
            :class="{ 'is-active': activeWorkId === work.work_id }"
            @click="emit('open', work.work_id)"
          >
            <span class="authoring__work-kind">{{ AUTHORING_KIND_LABELS[work.kind] }}</span>
            <strong>{{ work.title || `第 ${work.source_question_number || '?'} 题` }}</strong>
            <span class="authoring__work-snippet">{{ work.source_question_snippet }}</span>
            <span class="authoring__work-meta">
              已存 {{ work.current_version }} 版 · {{ work.updated_at }}
            </span>
          </button>
          <AppButton
            variant="ghost"
            class="authoring__work-delete"
            :aria-label="`删除练习 ${work.title || work.work_id}`"
            @click="emit('remove', work)"
          >
            删除
          </AppButton>
        </li>
      </ul>
    </template>
  </aside>
</template>
