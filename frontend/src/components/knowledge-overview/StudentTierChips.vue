<script setup lang="ts">
import StatePanel from '../design-system/StatePanel.vue'
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { TrainingOverviewNode, TrainingOverviewStudent } from '../../api/training'
import { TIER_LABELS, formatPercent, masteryDetail, tierOf, type MasteryTier } from './model'
const props = defineProps<{ node: TrainingOverviewNode; students: TrainingOverviewStudent[]; weakOnly?: boolean; limit?: number }>()
const router = useRouter()
const byId = computed(() => new Map(props.students.map(s => [s.student_id, s])))
type Entry = TrainingOverviewNode['students'][number]
// Score rate desc (missing/null last), then mastery asc, then student code.
function compareEntries(a: Entry, b: Entry) {
  const left = byId.value.get(a.student_id)?.score_rate
  const right = byId.value.get(b.student_id)?.score_rate
  return (right ?? Number.NEGATIVE_INFINITY) - (left ?? Number.NEGATIVE_INFINITY)
    || a.mastery - b.mastery
    || (byId.value.get(a.student_id)?.student_code ?? a.student_id)
      .localeCompare(byId.value.get(b.student_id)?.student_code ?? b.student_id, 'zh')
}
const groups = computed(() => (['weak', 'unsteady', 'stable', 'insufficient'] as MasteryTier[])
  .filter(tier => !props.weakOnly || tier === 'weak').map(tier => ({ tier,
    entries: props.node.students.filter(s => tierOf(s.tier) === tier).sort(compareEntries) })).filter(g => g.entries.length))
function scoreText(studentId: string) {
  const rate = byId.value.get(studentId)?.score_rate
  return rate === null || rate === undefined ? '无成绩' : formatPercent(rate)
}
function open(studentId: string) {
  void router.push({ name: 'student-evidence', params: { studentId },
    query: { knowledge: props.node.knowledge_key, klabel: props.node.display_name, from: 'overview' } })
}
</script>
<template>
  <div class="student-tier-groups">
    <div v-for="group in groups" :key="group.tier" class="student-tier-group" :class="`is-${group.tier}`">
      <h4 v-if="!weakOnly">{{ TIER_LABELS[group.tier] }}（{{ group.entries.length }} 人）</h4>
      <div class="student-tier-chips">
        <button v-for="entry in group.entries.slice(0, limit ?? group.entries.length)" :key="entry.student_id"
          type="button" class="student-tier-chip" :title="`考试得分率 ${scoreText(entry.student_id)} · ${masteryDetail(entry)}`" @click="open(entry.student_id)">
          {{ byId.get(entry.student_id)?.student_name ?? entry.student_id }} <span>{{ scoreText(entry.student_id) }}</span>
        </button>
        <span v-if="limit && group.entries.length > limit" class="student-tier-more">+{{ group.entries.length - limit }}</span>
      </div>
    </div>
    <StatePanel v-if="!groups.length" kind="empty" compact title="当前范围没有有证据学生。" />
  </div>
</template>
