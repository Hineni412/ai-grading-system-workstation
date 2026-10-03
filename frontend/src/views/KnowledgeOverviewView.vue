<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, RouterLink } from 'vue-router'
import type { TrainingOverviewNode } from '../api/training'
import AppButton from '../components/design-system/AppButton.vue'
import PageHeader from '../components/design-system/PageHeader.vue'
import OverviewScopeBar from '../components/knowledge-overview/OverviewScopeBar.vue'
import OverviewStudentTable from '../components/knowledge-overview/OverviewStudentTable.vue'
import OverviewTierBar from '../components/knowledge-overview/OverviewTierBar.vue'
import StudentTierChips from '../components/knowledge-overview/StudentTierChips.vue'
import { compareFocusNodes, defaultStudentSort, formatPercent, shortNodeName } from '../components/knowledge-overview/model'
import { chapterMetrics, heatLevel, nodeLocation, overviewMetrics, relatedNodes, studentDistribution, volumeItems } from '../components/knowledge-overview/metrics'
import KnowledgeTrainingTabs from '../components/knowledge-training/KnowledgeTrainingTabs.vue'
import { saveEvidenceScope, semesterEvidenceQuery } from '../features/evidence-scope/session'
import { presetFocusedTraining } from '../features/training/paper-selection-session'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useMasteryOverviewStore } from '../stores/mastery-overview'
import '../styles/knowledge-overview.css'
const router = useRouter()
const curriculum = useCurriculumScopeStore()
const store = useMasteryOverviewStore()
const overview = computed(() => store.overview)
const metrics = computed(() => overview.value ? overviewMetrics(overview.value) : null)
const skills = computed(() => volumeItems(overview.value?.nodes ?? []).filter(n => n.kind === 'skill' && n.distribution.weak > 0).sort(compareFocusNodes))
const priorities = computed(() => skills.value.slice(0, 5))
const attention = computed(() => defaultStudentSort(overview.value?.students ?? []).filter(s => s.topics.weak + s.skills.weak > 0).slice(0, 8))
const chapters = computed(() => chapterMetrics(overview.value?.nodes ?? []))
const examined = computed(() => chapters.value.filter(c => c.evidence > 0))
const unexamined = computed(() => chapters.value.filter(c => c.evidence === 0))
function relatedNames(key: string) {
  return overview.value ? relatedNodes(overview.value, key).slice(0, 3).map(r => shortNodeName(r.node)).join('、') : ''
}
function train(node: TrainingOverviewNode) {
  const student_ids = node.students.filter(s => s.tier === 'weak').map(s => s.student_id)
  if (!student_ids.length || node.kind !== 'skill') return
  saveEvidenceScope(semesterEvidenceQuery({ mode: 'selected', student_ids }, curriculum.selectedVolumeId))
  presetFocusedTraining({ targetKeys: [node.knowledge_key], rangeKeys: node.section_key ? [node.section_key] : [] })
  void router.push({ name: 'training', query: { mode: 'student' } })
}
function questions(node: TrainingOverviewNode) {
  void router.push({ name: 'student-evidence', params: { studentId: 'group' }, query: { mode: 'questions', knowledge: node.knowledge_key, klabel: node.display_name, from: 'overview' } })
}
</script>
<template>
  <section class="knowledge-overview-view knowledge-training-page" aria-labelledby="knowledge-overview-title">
    <PageHeader title="学情总览" title-id="knowledge-overview-title" class="knowledge-training-header">
      <template #navigation><KnowledgeTrainingTabs /></template>
    </PageHeader>
    <OverviewScopeBar />
    <p v-if="!curriculum.selectedVolumeId" class="knowledge-overview-notice" role="status">请先在顶部选择教学学期</p>
    <div v-else-if="store.loadState === 'loading' && !overview" class="knowledge-overview-skeleton" role="status" aria-busy="true">正在汇总本学期掌握度…</div>
    <div v-else-if="store.loadState === 'error'" class="knowledge-overview-error" role="alert"><p>{{ store.errorMessage }}</p><AppButton @click="store.load(curriculum.selectedVolumeId, true)">重新加载</AppButton></div>
    <template v-if="overview && metrics">
      <div v-if="store.loadState === 'stale-error'" class="knowledge-overview-error" role="alert"><p>当前显示上次成功读取的结果，最新内容暂时无法确认。</p><AppButton @click="store.load(curriculum.selectedVolumeId, true)">重新加载</AppButton></div>
      <dl class="overview-summary-strip" aria-label="学期汇总">
        <div><dt>有证据学生</dt><dd>{{ metrics.evidence_student_count }}<small> / {{ metrics.student_count }} 人</small></dd></div>
        <div><dt>本学期平均得分率</dt><dd>{{ formatPercent(metrics.exam_score_rate) }}<small>有成绩 {{ metrics.exam_student_count }} 人</small></dd></div>
        <div><dt>至少 1 项明显薄弱的学生</dt><dd>{{ metrics.weakStudents }}<small>人 / 有证据 {{ metrics.evidence_student_count }} 人</small></dd></div>
        <div><dt>有学生明显薄弱的项</dt><dd>{{ metrics.weak_skill_count }}<small>项技能 · {{ metrics.weak_topic_count }} 项知识点</small></dd></div>
      </dl>
      <div class="overview-action-layout">
        <section class="overview-card overview-priorities" aria-labelledby="overview-priorities-title">
          <header class="overview-card__header"><h2 id="overview-priorities-title">本周建议优先处理</h2><span>按明显薄弱人数排序</span></header>
          <p v-if="!priorities.length" class="overview-empty">当前范围没有明显薄弱的技能</p>
          <article v-for="(node, index) in priorities" :key="node.knowledge_key" class="overview-priority" :data-knowledge="node.knowledge_key">
            <div class="overview-priority-heading"><span class="overview-rank">{{ index + 1 }}</span><h3>{{ shortNodeName(node) }}</h3><span class="overview-weak-count">{{ node.distribution.weak }} 人明显薄弱</span></div>
            <p class="overview-location">属于 {{ nodeLocation(node, overview.nodes) }}</p>
            <p class="overview-related">相关知识点：{{ relatedNames(node.knowledge_key) || '题库暂无关联' }}</p>
            <div class="overview-distribution"><OverviewTierBar :distribution="node.distribution" show-counts /><span>有证据 {{ node.evidence_student_count }} 人</span></div>
            <StudentTierChips :node="node" :students="overview.students" weak-only :limit="8" />
            <div class="overview-priority-actions"><AppButton variant="primary" @click="train(node)">给这 {{ node.distribution.weak }} 人出训练卷</AppButton><AppButton @click="questions(node)">看错题</AppButton></div>
            <details class="overview-layer-list"><summary>展开分层名单</summary><StudentTierChips :node="node" :students="overview.students" /></details>
          </article>
          <RouterLink class="overview-all-link" :to="{ name: 'knowledge-graph', query: { filter: 'weak' } }">查看全部 {{ metrics.weak }} 项 →</RouterLink>
        </section>
        <div class="overview-action-sidebar">
          <section class="overview-card" aria-labelledby="overview-attention-title">
            <header class="overview-card__header"><h2 id="overview-attention-title">需要个别关注的学生</h2></header>
            <p v-if="!attention.length" class="overview-empty">当前范围没有明显薄弱的学生。</p>
            <button v-for="student in attention" :key="student.student_id" type="button" class="overview-attention-student" @click="router.push({ name: 'student-evidence', params: { studentId: student.student_id }, query: { from: 'overview' } })">
              <span class="overview-attention-heading"><strong>{{ student.student_name }}</strong><span>{{ formatPercent(student.score_rate) }}</span><b>明显薄弱 {{ student.topics.weak + student.skills.weak }} 项</b></span>
              <span class="overview-location">{{ student.class_id || '—' }} · {{ student.student_code || '—' }}</span><OverviewTierBar :distribution="studentDistribution(student)" />
            </button>
          </section>
          <section class="overview-card" aria-labelledby="overview-progress-title">
            <header class="overview-card__header"><h2 id="overview-progress-title">本学期考查进度</h2></header>
            <div v-for="row in examined" :key="row.chapter.knowledge_key" class="overview-chapter-progress" :data-weak-count="row.weak">
              <div><strong>{{ shortNodeName(row.chapter) }}</strong><span>群体掌握度 {{ formatPercent(row.chapter.group_mastery) }}</span></div>
              <p>有证据 {{ row.evidence }}/{{ row.items.length }} 项 · 有学生明显薄弱的 {{ row.weak }} 项</p>
              <div class="overview-heat-strip"><i v-for="node in row.items" :key="node.knowledge_key" :class="`heat-${heatLevel(node)}`" :title="`${shortNodeName(node)}：明显薄弱 ${node.distribution.weak} 人 / 有证据 ${node.evidence_student_count} 人`" /></div>
            </div>
            <p v-if="unexamined.length" class="overview-unexamined">尚未考查：{{ unexamined.map(c => shortNodeName(c.chapter)).join('、') }}（{{ unexamined.length }} 章，共 {{ unexamined.reduce((sum, c) => sum + c.items.length, 0) }} 项）</p>
          </section>
        </div>
      </div>
      <details class="overview-all-students"><summary>全部 {{ overview.students.length }} 名学生</summary><OverviewStudentTable :students="overview.students" /></details>
      <details v-if="overview.warnings.length" class="overview-data-notes"><summary>数据说明（{{ overview.warnings.length }}）</summary><ul><li v-for="warning in overview.warnings" :key="warning">{{ warning }}</li></ul></details>
    </template>
  </section>
</template>
