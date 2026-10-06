<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  questionBankApi,
  type QuestionStandardSummary,
  type SkillCandidateSummary,
} from '../../api/question-bank'

const props = defineProps<{ volumeId: string }>()
const emit = defineEmits<{ candidate: [summary: SkillCandidateSummary | null] }>()

const standard = ref<QuestionStandardSummary | null>(null)
const candidate = ref<SkillCandidateSummary | null>(null)
const loading = ref(false)
const failed = ref(false)
let controller: AbortController | null = null

function datePart(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : ''
}

const hint = computed(() => {
  const summary = candidate.value
  if (!summary) return ''
  if (summary.approved_unpublished_skill_count > 0) {
    return `有 ${summary.approved_unpublished_skill_count} 项已批准的新技能等待发布。新技能在学期标准修订时统一发布，发布前会单独征得你的同意并先备份。`
  }
  if (summary.counts.ready > 0) {
    return `有 ${summary.counts.ready} 个技能缺口判定点尚未整理，可在下方“技能缺口”中整理成技能候选。`
  }
  return ''
})

async function refresh() {
  controller?.abort()
  if (!props.volumeId) {
    standard.value = null
    candidate.value = null
    emit('candidate', null)
    return
  }
  const request = new AbortController()
  controller = request
  loading.value = true
  failed.value = false
  const [standardResult, candidateResult] = await Promise.allSettled([
    questionBankApi.standardSummary(props.volumeId, request.signal),
    questionBankApi.skillCandidateSummary(props.volumeId, request.signal),
  ])
  if (request.signal.aborted) return
  loading.value = false
  standard.value = standardResult.status === 'fulfilled' ? standardResult.value : null
  candidate.value = candidateResult.status === 'fulfilled' ? candidateResult.value : null
  failed.value = standardResult.status === 'rejected' && candidateResult.status === 'rejected'
  emit('candidate', candidate.value)
}

watch(() => props.volumeId, () => void refresh(), { immediate: true })
onBeforeUnmount(() => controller?.abort())
defineExpose({ refresh })
</script>

<template>
  <section class="qb-standard" aria-label="学期标准">
    <p v-if="loading" role="status">正在读取学期标准…</p>
    <template v-else-if="standard">
      <p v-if="standard.active_release" class="qb-standard-line">
        当前技能标准 <strong>{{ standard.active_release.label }}</strong>
        <template v-if="standard.active_release.activated_at">
          · {{ datePart(standard.active_release.activated_at) }} 启用
        </template>
        <template v-if="standard.active_release.reason">
          · {{ standard.active_release.reason }}
        </template>
      </p>
      <p v-else class="qb-standard-line">当前没有可用的技能标准。</p>
      <details v-if="standard.versions.length" class="qb-standard-history">
        <summary>历次版本（{{ standard.versions.length }}）</summary>
        <ul>
          <li v-for="version in standard.versions" :key="version.release_id">
            <strong>{{ version.label }}</strong>
            <span v-if="version.activated_at">· {{ datePart(version.activated_at) }}</span>
            <span v-if="version.reason">· {{ version.reason }}</span>
            <em :class="version.status === 'active' ? 'is-current' : 'is-retired'">
              {{ version.status === 'active' ? '当前' : '已停用' }}
            </em>
          </li>
        </ul>
      </details>
      <p class="qb-standard-counts">
        <span>本学期技能 <strong>{{ standard.skill_count }}</strong></span>
        <span>未挂技能题 <strong>{{ standard.unlinked_question_count }}</strong></span>
        <span>技能缺口判定点 <strong>{{ standard.gap_point_count }}</strong></span>
      </p>
      <p v-if="candidate" class="qb-standard-breakdown">
        待整理 {{ candidate.counts.ready }} · 待审核 {{ candidate.counts.pending_review }}
        · 已批准待发布 {{ candidate.approved_unpublished_skill_count }} 项技能
        · 保持只归小节 {{ candidate.counts.dismissed }} · 未定位小节 {{ candidate.counts.unlocated }}
      </p>
      <p v-if="hint" class="qb-standard-hint">{{ hint }}</p>
    </template>
    <p v-if="failed" role="alert" class="qb-standard-error">
      学期标准暂时无法读取，请重试。
      <button class="qb-link" type="button" @click="refresh">重试</button>
    </p>
    <p class="qb-standard-note">查看这些信息不调用模型。</p>
  </section>
</template>
