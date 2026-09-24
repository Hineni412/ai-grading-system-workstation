<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'

import type {
  TrainingOverviewNode,
  TrainingOverviewStudent,
} from '../../api/training'
import { compareFocusNodes, formatPercent, shortNodeName, tierOf } from './model'
import OverviewTierBar from './OverviewTierBar.vue'

const props = defineProps<{
  topicNodes: TrainingOverviewNode[]
  skillNodes: TrainingOverviewNode[]
  sectionNames: Record<string, string>
  studentsById: Record<string, TrainingOverviewStudent>
}>()

const expandedKey = ref('')
const showAll = ref<Record<'topic' | 'skill', boolean>>({ topic: false, skill: false })

const topics = computed(() => props.topicNodes
  .filter(node => node.evidence_student_count > 0)
  .sort(compareFocusNodes))
const skills = computed(() => props.skillNodes
  .filter(node => node.evidence_student_count > 0)
  .sort(compareFocusNodes))

function visible(kind: 'topic' | 'skill'): TrainingOverviewNode[] {
  const list = kind === 'topic' ? topics.value : skills.value
  return showAll.value[kind] ? list : list.slice(0, 8)
}

function toggle(node: TrainingOverviewNode): void {
  expandedKey.value = expandedKey.value === node.knowledge_key ? '' : node.knowledge_key
}

interface TierGroup {
  tier: 'weak' | 'review' | 'stable'
  label: string
  students: Array<{ student_id: string; mastery: number; name: string }>
}

function tierGroups(node: TrainingOverviewNode): TierGroup[] {
  const groups: TierGroup[] = [
    { tier: 'weak', label: '待补强', students: [] },
    { tier: 'review', label: '需巩固', students: [] },
    { tier: 'stable', label: '较稳定', students: [] },
  ]
  for (const entry of node.students) {
    const tier = tierOf(entry.mastery)
    const group = groups.find(item => item.tier === tier)
    group?.students.push({
      student_id: entry.student_id,
      mastery: entry.mastery,
      name: props.studentsById[entry.student_id]?.student_name ?? entry.student_id,
    })
  }
  return groups.filter(group => group.students.length > 0)
}

function masteryClass(value: number | null): string {
  const tier = tierOf(value)
  return tier ? `is-${tier}` : 'is-missing'
}
</script>

<template>
  <section class="overview-card" aria-labelledby="overview-focus-title">
    <header class="overview-card__header">
      <h2 id="overview-focus-title">最需关注</h2>
    </header>
    <div class="overview-focus-columns">
      <div
        v-for="column in ([
          { kind: 'topic' as const, title: '知识点', empty: '该范围的知识点暂无掌握证据。' },
          { kind: 'skill' as const, title: '技能', empty: '该范围的技能暂无掌握证据。' },
        ])"
        :key="column.kind"
        class="overview-focus-column"
      >
        <h3>{{ column.title }}</h3>
        <p v-if="!(column.kind === 'topic' ? topics : skills).length" class="overview-empty">
          {{ column.empty }}
        </p>
        <ul v-else class="overview-focus-list">
          <li v-for="node in visible(column.kind)" :key="node.knowledge_key">
            <button
              type="button"
              class="overview-focus-item"
              :aria-expanded="expandedKey === node.knowledge_key"
              @click="toggle(node)"
            >
              <span class="overview-focus-item__heading">
                <span class="overview-focus-item__name">{{ shortNodeName(node) }}</span>
                <span class="overview-focus-item__section">{{ sectionNames[node.section_key] ?? '' }}</span>
              </span>
              <span class="overview-focus-item__facts">
                <span class="overview-focus-item__mastery" :class="masteryClass(node.group_mastery)">
                  {{ formatPercent(node.group_mastery) }}
                </span>
                <span class="overview-focus-item__counts">
                  待补强 {{ node.distribution.weak }} 人 · 需巩固 {{ node.distribution.review }} 人
                </span>
              </span>
              <OverviewTierBar :distribution="node.distribution" />
            </button>
            <div
              v-if="expandedKey === node.knowledge_key"
              class="overview-focus-detail"
            >
              <dl
                v-for="group in tierGroups(node)"
                :key="group.tier"
                class="overview-focus-detail__group"
              >
                <dt>{{ group.label }}（{{ group.students.length }} 人）</dt>
                <dd>
                  <RouterLink
                    v-for="student in group.students"
                    :key="student.student_id"
                    class="overview-focus-detail__student"
                    :to="{
                      name: 'student-evidence',
                      params: { studentId: student.student_id },
                      query: { knowledge: node.knowledge_key, klabel: node.display_name, from: 'overview' },
                    }"
                  >
                    {{ student.name }} {{ formatPercent(student.mastery) }}
                  </RouterLink>
                </dd>
              </dl>
              <p class="overview-focus-detail__actions">
                <RouterLink :to="{ name: 'training', query: { mode: 'chapter' } }">按章节训练</RouterLink>
                <RouterLink :to="{ name: 'question-assembly', query: { mode: 'assistant' } }">学情组卷</RouterLink>
              </p>
            </div>
          </li>
        </ul>
        <button
          v-if="(column.kind === 'topic' ? topics : skills).length > 8 && !showAll[column.kind]"
          type="button"
          class="overview-link-button"
          @click="showAll[column.kind] = true"
        >
          显示全部（{{ (column.kind === 'topic' ? topics : skills).length }}）
        </button>
      </div>
    </div>
  </section>
</template>
