<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  questionBankApi,
  type QuestionStandardSummary,
  type SkillCandidateSummary,
} from '../../api/question-bank'

const props = defineProps<{ volumeId: string; skillsRequired?: boolean }>()
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
    return `有 ${summary.approved_unpublished_skill_count} 项已批准的新关键步骤等待发布。新关键步骤在学期标准修订时统一发布，发布前会单独征得你的同意并先备份。`
  }
  if (summary.counts.ready > 0) {
    return `有 ${summary.counts.ready} 个关键步骤缺口判定点尚未整理，可在下方“关键步骤缺口”中整理成关键步骤候选。`
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
    props.skillsRequired
      ? questionBankApi.skillCandidateSummary(props.volumeId, request.signal)
      : Promise.resolve(null),
  ])
  if (request.signal.aborted) return
  loading.value = false
  standard.value = standardResult.status === 'fulfilled' ? standardResult.value : null
  candidate.value = candidateResult.status === 'fulfilled' ? candidateResult.value : null
  failed.value = standardResult.status === 'rejected' || candidateResult.status === 'rejected'
  emit('candidate', candidate.value)
}

watch(() => [props.volumeId, props.skillsRequired], () => void refresh(), { immediate: true })
onBeforeUnmount(() => controller?.abort())
defineExpose({ refresh })
</script>

<template>
  <section class="qb-standard" aria-label="学期标准">
    <p v-if="loading" role="status">正在读取学期标准…</p>
    <template v-else-if="standard">
      <p v-if="standard.active_release" class="qb-standard-line">
        当前知识标准 <strong>{{ standard.active_release.label }}</strong>
        <template v-if="standard.active_release.activated_at">
          · {{ datePart(standard.active_release.activated_at) }} 启用
        </template>
        <template v-if="standard.active_release.reason">
          · {{ standard.active_release.reason }}
        </template>
      </p>
      <p v-else class="qb-standard-line">当前没有可用的知识标准。</p>
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
      <p v-if="skillsRequired" class="qb-standard-counts">
        <span>本学期关键步骤 <strong>{{ standard.skill_count }}</strong> 项</span>
        <span>未挂关键步骤题 <strong>{{ standard.unlinked_question_count }}</strong> 题</span>
        <span>关键步骤缺口 <strong>{{ standard.gap_point_count }}</strong> 个判定点</span>
      </p>
      <p v-if="candidate" class="qb-standard-breakdown">
        待整理 {{ candidate.counts.ready }} 个判定点 · 待审核 {{ candidate.counts.pending_review }} 个判定点
        · 已批准待发布 {{ candidate.approved_unpublished_skill_count }} 项关键步骤
        · 保持只归小节 {{ candidate.counts.dismissed }} 个判定点 · 未定位小节 {{ candidate.counts.unlocated }} 个判定点
      </p>
      <p v-if="hint" class="qb-standard-hint">{{ hint }}</p>
    </template>
    <p v-if="failed" role="alert" class="qb-standard-error">
      部分标准信息暂时无法读取，请重试。
      <button class="qb-link" type="button" @click="refresh">重试</button>
    </p>
    <p class="qb-standard-note">查看这些信息不调用模型。</p>
  </section>
</template>
