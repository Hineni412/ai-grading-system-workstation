<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { knowledgeLeafLabel } from '../../api/question-bank'
import { trainingApi, type TrainingDiagnosis, type TrainingGroup, type TrainingGrouping, type TrainingGroupingRequest,
  type TrainingStudentScopeRequest, type TrainingExamScopeRequest } from '../../api/training'
import type { ChapterGroupEditor, AdoptedChapterGroup, ChapterGroupSort } from '../../features/training/paper-selection-session'
import AppButton from '../design-system/AppButton.vue'
import { ApiError } from '../../api/errors'
import { useTrainingStore } from '../../stores/training'

const props = defineProps<{
  diagnosis: TrainingDiagnosis
  scope: TrainingStudentScopeRequest
  examScope: TrainingExamScopeRequest
  settings: TrainingGroupingRequest
  editor: ChapterGroupEditor | null
  adopted: AdoptedChapterGroup | null
  arrangements: AdoptedChapterGroup[]
  disabled?: boolean
}>()
const emit = defineEmits<{
  edit: [editor: ChapterGroupEditor | null]
  adopt: [group: TrainingGroup, diagnosis: TrainingDiagnosis]
}>()
const training = useTrainingStore()
const sortMode = defineModel<ChapterGroupSort>('sortMode', { default: 'size' })
const result = ref<TrainingGrouping | null>(null)
const checked = ref<TrainingGroup | null>(null)
const latestDiagnosis = ref<TrainingDiagnosis | null>(null)
const editing = ref(false)
const memberIds = ref<string[]>([])
const targetKeys = ref<string[]>([])
const original = ref<ChapterGroupEditor | null>(null)
const busy = ref(false)
const stale = ref(false)
const message = ref('')
const showAll = ref(false)
const showAdd = ref(false)
const search = ref('')
const overlapConfirmed = ref(false)
let controller: AbortController | null = null
let revision = 0
let timer: ReturnType<typeof setTimeout> | null = null

const sourceKey = computed(() => JSON.stringify({ scope: props.scope, exams: props.examScope, settings: props.settings }))
const sortExplanation = computed(() => ({
  size: '先看人数；人数相同时，依次看训练目标掌握度更低、组内差距更小。',
  weakness: '先看训练目标掌握度更低；相同时，依次看人数更多、组内差距更小。',
  similarity: '先看组内差距更小；相同时，依次看人数更多、训练目标掌握度更低。',
})[sortMode.value])
const chapterName = computed(() => knowledgeLeafLabel(props.diagnosis.knowledge_catalog?.find(
  node => node.knowledge_key === props.settings.scope_keys[0])?.knowledge_point ?? '当前章节'))
const effectiveDiagnosis = computed(() => latestDiagnosis.value ?? props.diagnosis)
const selectedMembers = computed(() => effectiveDiagnosis.value.students.filter(student => memberIds.value.includes(student.student_id)))
const otherMembers = computed(() => effectiveDiagnosis.value.students.filter(student => !memberIds.value.includes(student.student_id)
  && `${student.student_name} ${student.student_code} ${student.class_id}`.includes(search.value.trim())))
const targetOptions = computed(() => {
  const keys = new Set([...targetKeys.value, ...(checked.value?.targets.map(target => target.knowledge_key) ?? []),
    ...(original.value?.targetKeys ?? [])])
  return [...keys].map(key => ({ key, label: knowledgeLeafLabel(props.diagnosis.knowledge_catalog?.find(
    node => node.knowledge_key === key)?.knowledge_point ?? key) }))
})
const overlaps = computed(() => props.arrangements.filter(group => !(group.groupId === props.adopted?.groupId
  && JSON.stringify([...group.memberIds].sort()) === JSON.stringify([...memberIds.value].sort())
  && JSON.stringify([...group.targetKeys].sort()) === JSON.stringify([...targetKeys.value].sort()))
  && group.memberIds.some(id => memberIds.value.includes(id)) && group.targetKeys.some(key => targetKeys.value.includes(key))))
const ready = computed(() => editing.value && checked.value?.ready && !busy.value && !stale.value
  && !props.disabled && (!overlaps.value.length || overlapConfirmed.value) && targetKeys.value.length > 0 && memberIds.value.length >= 2)

function percent(value: number | null): string { return value === null ? '—' : `${Math.round(value * 100)}%` }
function classLabel(value: string): string {
  const name = value.trim()
  if (!name) return '未分班'
  return name === '未分班' || /班$/.test(name) ? name : `${name}班`
}
function classSummary(group: TrainingGroup): string {
  const counts = new Map<string, number>()
  group.members.forEach(member => counts.set(member.class_id || '未分班', (counts.get(member.class_id || '未分班') ?? 0) + 1))
  return [...counts].sort(([left], [right]) => left.localeCompare(right, 'zh-CN', { numeric: true }))
    .map(([name, count]) => `${classLabel(name)} ${count}人`).join(' · ')
}
const checkedMembers = computed(() => new Map(checked.value?.members.map(member => [member.student_id, member]) ?? []))
function studentMastery(id: string): number | null {
  const member = checkedMembers.value.get(id)
  return member ? memberMastery(member) : null
}
const studentProfiles = computed(() => new Map((latestDiagnosis.value ?? props.diagnosis).students
  .map(student => [student.student_id, student])))
function mean(values: number[]): number | null {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
}
function memberMastery(member: TrainingGroup['members'][number]): number | null {
  return mean(member.targets.map(target => target.mastery).filter(Number.isFinite))
}
function groupStats(group: TrainingGroup) {
  const mastery = group.members.map(memberMastery).filter((value): value is number => value !== null)
  const profiles = group.members.map(member => studentProfiles.value.get(member.student_id))
  const rates = profiles.map(student => student?.score_rate).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  return {
    mastery: mean(mastery), minimum: mastery.length ? Math.min(...mastery) : null,
    maximum: mastery.length ? Math.max(...mastery) : null,
    span: mastery.length ? Math.round((Math.max(...mastery) - Math.min(...mastery)) * 100) : null,
    scoreRate: mean(rates), scored: rates.length,
    historical: profiles.filter(student => student?.score_rate_source === 'historical_fallback').length,
  }
}
const sortedCards = computed(() => (result.value?.groups ?? []).map(group => ({ group, stats: groupStats(group) }))
  .sort((left, right) => {
    const size = right.group.members.length - left.group.members.length
    const weakness = (left.stats.mastery ?? 2) - (right.stats.mastery ?? 2)
    const similarity = (left.stats.span ?? 101) - (right.stats.span ?? 101)
    if (sortMode.value === 'weakness') return weakness || size || similarity
    if (sortMode.value === 'similarity') return similarity || size || weakness
    return size || weakness || similarity
  }))
const cards = computed(() => showAll.value ? sortedCards.value : sortedCards.value.slice(0, 6))
const selectionStats = computed(() => checked.value ? groupStats(checked.value) : null)
function sameSelection(group: TrainingGroup): boolean {
  return JSON.stringify(group.members.map(member => member.student_id).sort()) === JSON.stringify([...memberIds.value].sort())
    && JSON.stringify(group.targets.map(target => target.knowledge_key).sort()) === JSON.stringify([...targetKeys.value].sort())
}
function remember(): void {
  emit('edit', editing.value ? { memberIds: [...memberIds.value], targetKeys: [...targetKeys.value],
    scopeKeys: [...props.settings.scope_keys], original: original.value ? {
      memberIds: [...original.value.memberIds], targetKeys: [...original.value.targetKeys],
    } : undefined } : null)
}
async function refresh(force = false): Promise<void> {
  controller?.abort()
  const current = ++revision
  if (props.disabled || !props.settings.scope_keys[0]) { busy.value = false; stale.value = true; return }
  const next = new AbortController()
  controller = next
  busy.value = true
  message.value = ''
  try {
    const base = { scope: props.scope, exam_scope: props.examScope, grouping: { ...props.settings } }
    const body = { ...base,
      grouping: { ...props.settings, ...(editing.value && memberIds.value.length ? {
        member_ids: [...memberIds.value], target_keys: [...targetKeys.value],
      } : {}) } }
    const key = JSON.stringify(body)
    let cached = force ? null : training.cachedGroupDiagnosis(key, props.diagnosis)
    if (!cached && !force && editing.value) {
      const originalResult = training.cachedGroupDiagnosis(JSON.stringify(base), props.diagnosis)
      const selection = originalResult?.grouping?.groups.find(sameSelection)
      if (selection && originalResult?.grouping) cached = {
        ...originalResult, grouping: { ...originalResult.grouping, selection },
      }
    }
    const diagnosis = cached ?? await trainingApi.diagnose(body, next.signal)
    if (current !== revision) return
    if (!diagnosis.grouping) throw new Error('分组结果不可用')
    if (!cached) training.rememberGroupDiagnosis(key, props.diagnosis, diagnosis)
    result.value = diagnosis.grouping
    latestDiagnosis.value = diagnosis
    checked.value = diagnosis.grouping.selection
    stale.value = false
  } catch (error) {
    if (current !== revision || next.signal.aborted) return
    stale.value = true
    message.value = error instanceof ApiError && error.kind === 'timeout'
      ? '小组核对超过30秒未完成，当前名单和目标已保留。请刷新建议重试。'
      : '小组建议暂时无法更新，当前名单和目标已保留。请重试。'
  } finally {
    if (current === revision) busy.value = false
  }
}
function scheduleCheck(): void {
  overlapConfirmed.value = false
  stale.value = true
  busy.value = true
  checked.value = null
  controller?.abort()
  revision += 1
  remember()
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => { void refresh() }, 420)
}
function inspect(group: TrainingGroup): void {
  memberIds.value = group.members.map(member => member.student_id)
  targetKeys.value = group.targets.map(target => target.knowledge_key)
  original.value = { memberIds: [...memberIds.value], targetKeys: [...targetKeys.value], scopeKeys: [...props.settings.scope_keys] }
  editing.value = true
  showAdd.value = false
  checked.value = group
  overlapConfirmed.value = false
  message.value = ''
  remember()
}
function toggleMember(id: string): void {
  memberIds.value = memberIds.value.includes(id) ? memberIds.value.filter(value => value !== id) : [...memberIds.value, id]
  scheduleCheck()
}
function toggleTarget(key: string): void {
  targetKeys.value = targetKeys.value.includes(key) ? targetKeys.value.filter(value => value !== key) : [...targetKeys.value, key]
  scheduleCheck()
}
function restore(): void {
  if (!original.value) return
  memberIds.value = [...original.value.memberIds]
  targetKeys.value = [...original.value.targetKeys]
  scheduleCheck()
}
async function adopt(): Promise<void> {
  if (!ready.value) return
  // Re-read once at adoption so saved candidates never count as fresh evidence.
  await refresh(true)
  if (!ready.value || !checked.value) return
  emit('adopt', checked.value, latestDiagnosis.value ?? props.diagnosis)
}
function closeEditor(): void {
  if (timer) clearTimeout(timer)
  controller?.abort(); revision += 1; busy.value = false
  editing.value = false
  checked.value = null
  stale.value = false
  message.value = ''
  emit('edit', null)
}

watch([sourceKey, () => props.diagnosis, () => props.disabled], (_value, previous) => {
  overlapConfirmed.value = false
  if (previous?.length && (previous[0] !== sourceKey.value || previous[1] !== props.diagnosis)) latestDiagnosis.value = null
  if (props.editor && JSON.stringify(props.editor.scopeKeys) === JSON.stringify(props.settings.scope_keys) && !editing.value) {
    editing.value = true
    memberIds.value = [...props.editor.memberIds]
    targetKeys.value = [...props.editor.targetKeys]
    original.value = { ...props.editor, ...(props.editor.original ?? {}) }
  } else if (previous?.length && original.value && JSON.stringify(original.value.scopeKeys) !== JSON.stringify(props.settings.scope_keys)) {
    closeEditor()
  }
  if (editing.value) {
    const allowed = new Set(props.diagnosis.students.map(student => student.student_id))
    // Keep an explicit explanation when the parent scope no longer contains an edited member.
    if (memberIds.value.some(id => !allowed.has(id))) {
      stale.value = true
      message.value = '来源范围已变化，编辑名单含范围外学生。请恢复原范围或重新选择候选小组。'
      controller?.abort(); revision += 1; busy.value = false
      return
    }
  }
  stale.value = true
  void refresh()
}, { immediate: true })
onBeforeUnmount(() => { controller?.abort(); revision += 1; if (timer) clearTimeout(timer) })
</script>

<template>
  <section class="training-groups" aria-label="跨班训练小组">
    <header class="training-groups__heading">
      <div><h2>{{ chapterName }}</h2><span>{{ effectiveDiagnosis.students.length }} 人参与推荐<span v-if="result">，{{ result.groups.length }} 个候选组</span></span></div>
      <AppButton :disabled="busy || disabled" @click="refresh(true)">{{ busy ? '正在核对…' : '刷新建议' }}</AppButton>
    </header>
    <p v-if="scope.score_rate_min != null || scope.score_rate_max != null" class="training-groups__note">已启用辅助条件：在得分率 {{ Math.round((scope.score_rate_min ?? 0) * 100) }}%–{{ Math.round((scope.score_rate_max ?? 1) * 100) }}% 范围内推荐。</p>
    <p v-if="message" class="training-groups__alert" role="alert">{{ message }}</p>
    <p v-if="!result && busy" class="training-groups__empty">正在核对训练目标、证据和可用题目……</p>
    <template v-if="!editing">
      <div v-if="result?.groups.length" class="training-groups__sorting">
        <label>卡片排序
          <select v-model="sortMode" aria-label="小组卡片排序" aria-describedby="training-group-sort-explanation">
            <option value="size">人数多优先</option>
            <option value="weakness">训练目标更薄弱优先</option>
            <option value="similarity">组内水平更接近优先</option>
          </select>
        </label>
        <p id="training-group-sort-explanation">{{ sortExplanation }}</p>
      </div>
      <div class="training-groups__grid">
        <article v-for="{ group, stats } in cards" :key="group.group_id" class="training-groups__candidate">
          <header class="training-groups__card-heading">
            <h3>{{ group.targets.map(target => knowledgeLeafLabel(target.knowledge_point)).join('、') }}</h3>
            <strong class="training-groups__size">{{ group.members.length }}<span>人</span></strong>
          </header>
          <dl class="training-groups__stats">
            <div><dt>平均掌握度</dt><dd>{{ percent(stats.mastery) }}</dd></div>
            <div><dt>组内差距</dt><dd>{{ stats.span ?? '—' }}<small v-if="stats.span !== null">个百分点</small></dd></div>
            <div><dt>考试平均得分率</dt><dd>{{ percent(stats.scoreRate) }}</dd></div>
          </dl>
          <div class="training-groups__range" v-if="stats.minimum !== null && stats.maximum !== null">
            <div class="training-groups__range-track" aria-hidden="true"><i :style="{ left: `${stats.minimum * 100}%`, width: `${Math.max(1, (stats.maximum - stats.minimum) * 100)}%` }" /></div>
            <span>训练目标掌握度 {{ percent(stats.minimum) }}–{{ percent(stats.maximum) }}</span>
          </div>
          <p class="training-groups__classes">{{ classSummary(group) }}</p>
          <p v-if="stats.scored < group.members.length || stats.historical" class="training-groups__note">{{ stats.scored }} 人有考试成绩<span v-if="stats.historical">，其中 {{ stats.historical }} 人参考历史</span></p>
          <p v-for="issue in group.issues" :key="issue" class="training-groups__alert">{{ issue }}</p>
          <footer class="training-groups__card-footer">
            <div><strong>{{ group.available_question_count }} 道训练目标题</strong><span>{{ group.targets.some(target => target.sparse_member_count > 0) ? '含单次作答依据' : '已有多次作答依据' }}</span></div>
            <AppButton :disabled="busy || stale || disabled" @click="inspect(group)">查看小组</AppButton>
          </footer>
        </article>
      </div>
      <p v-if="result && !result.groups.length && !busy" class="training-groups__empty">暂未形成可靠的公共训练组。可查看下方原因，或切换“学生明细”手动核对。</p>
      <AppButton v-if="(result?.groups.length ?? 0) > 6" class="training-groups__more" @click="showAll = !showAll">{{ showAll ? '收起更多小组' : `查看其余 ${result!.groups.length - 6} 个小组` }}</AppButton>
    </template>
    <section v-else class="training-groups__editor" aria-label="调整训练小组">
      <header><h3>核对名单与训练目标</h3><AppButton @click="closeEditor">返回候选</AppButton></header>
      <dl v-if="selectionStats" class="training-groups__stats training-groups__stats--selection">
        <div><dt>训练目标平均掌握度</dt><dd>{{ percent(selectionStats.mastery) }}</dd><span>{{ percent(selectionStats.minimum) }}–{{ percent(selectionStats.maximum) }}</span></div>
        <div><dt>组内掌握度差距</dt><dd>{{ selectionStats.span ?? '—' }}<small v-if="selectionStats.span !== null">个百分点</small></dd></div>
        <div><dt>考试平均得分率</dt><dd>{{ percent(selectionStats.scoreRate) }}</dd><span>{{ selectionStats.scored }} 人有成绩<span v-if="selectionStats.historical">，{{ selectionStats.historical }} 人参考历史</span></span></div>
      </dl>
      <fieldset><legend>训练目标</legend><label v-for="target in targetOptions" :key="target.key"><input type="checkbox" :checked="targetKeys.includes(target.key)" @change="toggleTarget(target.key)">{{ target.label }}</label></fieldset>
      <div class="training-groups__targets" v-if="checked">
        <p v-for="target in checked.targets" :key="target.knowledge_key">{{ knowledgeLeafLabel(target.knowledge_point) }}：掌握度 {{ percent(target.min_mastery) }}–{{ percent(target.max_mastery) }} · 目标难度 {{ target.target_difficulty ?? '证据不足' }} · 涉及 {{ target.affected_student_count ?? checked?.members.length }} 人 · {{ target.available_question_count }} 道适用题</p>
      </div>
      <div class="training-groups__members-heading"><h4>小组成员 <span>{{ memberIds.length }} 人</span></h4><p>展开姓名查看作答依据；调整名单或目标后会自动核对。</p></div>
      <div class="training-groups__members">
        <details v-for="student in selectedMembers" :key="student.student_id">
          <summary><div class="training-groups__member-name"><label @click.stop><input type="checkbox" checked :aria-label="`移除 ${classLabel(student.class_id)} ${student.student_name} ${student.student_code}`" @change="toggleMember(student.student_id)">{{ student.student_name }}</label><span>{{ classLabel(student.class_id) }} · {{ student.student_code }}</span></div><div class="training-groups__member-scores"><span>掌握度 <b>{{ percent(studentMastery(student.student_id)) }}</b></span><span>考试得分率 <b>{{ percent(studentProfiles.get(student.student_id)?.score_rate ?? null) }}</b><small v-if="studentProfiles.get(student.student_id)?.score_rate_source === 'historical_fallback'">（历史）</small></span></div></summary>
          <div v-for="target in checkedMembers.get(student.student_id)?.targets ?? []" :key="target.knowledge_key">
            <p>{{ knowledgeLeafLabel(target.knowledge_point) }} · {{ percent(target.mastery) }} · {{ target.evidence_count }} 条直接依据</p>
            <p v-for="ref in target.source_question_refs" :key="`${ref.session_id}:${ref.question_id}`">{{ ref.session_name }} · {{ ref.question_id }} · {{ ref.score_awarded }}/{{ ref.full_score }} 分</p>
          </div>
        </details>
      </div>
      <div class="training-groups__editor-tools"><AppButton @click="showAdd = !showAdd">{{ showAdd ? '收起添加成员' : '从当前范围添加成员' }}</AppButton><AppButton @click="restore">恢复原名单与目标</AppButton></div>
      <div v-if="showAdd" class="training-groups__add"><input v-model="search" type="search" placeholder="搜索姓名、班级或学号" aria-label="搜索可添加成员"><label v-for="student in otherMembers" :key="student.student_id"><input type="checkbox" :aria-label="`添加 ${classLabel(student.class_id)} ${student.student_name} ${student.student_code}`" @change="toggleMember(student.student_id)">{{ student.student_name }} · {{ classLabel(student.class_id) }} · {{ student.student_code }}</label></div>
      <p v-if="memberIds.length < 2" class="training-groups__alert">请至少保留两名成员。</p>
      <p v-if="!targetKeys.length" class="training-groups__alert">请至少保留一个训练目标。</p>
      <p v-for="issue in checked?.issues ?? []" :key="issue" class="training-groups__alert">{{ issue }}</p>
      <p v-for="warning in checked?.warnings ?? []" :key="warning" class="training-groups__note">{{ warning }}</p>
      <div v-if="overlaps.length" class="training-groups__alert"><p>与本页此前生成草稿的小组有成员及目标重合。请调整名单或目标；确需重复训练时，请先核对安排。</p><label><input v-model="overlapConfirmed" type="checkbox">已核对重合，仍采用本组</label></div>
      <footer><span>{{ memberIds.length }} 人 · {{ targetKeys.length }} 个训练目标</span><AppButton variant="primary" :disabled="!ready" @click="adopt">采用小组并核对出卷设置</AppButton></footer>
    </section>
    <details v-if="result?.unassigned.length" class="training-groups__unassigned"><summary>暂未推荐 {{ result.unassigned.length }} 人 · 查看原因</summary><p v-for="student in result.unassigned" :key="student.student_id"><strong>{{ student.student_name }} · {{ classLabel(student.class_id) }}</strong>：{{ student.reason }}</p></details>
    <details class="training-groups__explanation"><summary>分组与统计说明</summary><p>按实际失分需求和接近的整体考试水平分组；各成员目标可以不同。汇总成员适用题目，按整张卷覆盖选择，不要求每道题适合所有成员。平均掌握度按每名成员在训练目标上的平均值汇总；组内差距是这些平均值的最高与最低之差。考试平均得分率按有成绩成员计算，缺失成绩不按零分处理。</p><p>无证据不推断薄弱；明显不同需求可安排个人训练。采用小组时会自动核对最新依据。</p></details>
  </section>
</template>

<style scoped>
.training-groups{display:grid;gap:1rem;min-width:0}
.training-groups__sorting{display:flex;align-items:center;flex-wrap:wrap;gap:.65rem 1rem}
.training-groups__sorting label{display:flex;align-items:center;gap:.65rem;font-size:.8rem;white-space:nowrap}
.training-groups__sorting select{max-width:100%;min-height:var(--control-height-default);padding:.5rem .65rem;border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);color:var(--color-text-primary);font:inherit}
.training-groups__sorting p{flex:1 1 260px;font-size:.76rem;line-height:1.6;color:var(--color-text-secondary)}
.training-groups__heading,.training-groups__editor>header,.training-groups__editor>footer{display:flex;align-items:center;justify-content:space-between;gap:1rem}
.training-groups h2,.training-groups h3,.training-groups h4,.training-groups p,.training-groups dl,.training-groups dd{margin:0}
.training-groups h2{font-size:1.25rem}.training-groups h3{font-size:.98rem;line-height:1.6}.training-groups h4{font-size:.9rem}
.training-groups__heading>div{display:grid;gap:.3rem}
.training-groups__heading span,.training-groups__note{color:var(--color-text-secondary);font-size:.8rem;line-height:1.6}
.training-groups__grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,310px),1fr));gap:1rem;align-items:stretch}
.training-groups__candidate{display:flex;flex-direction:column;gap:.8rem;min-width:0;padding:1rem;background:var(--color-bg-surface);border:1px solid var(--color-border-default);border-radius:10px}
.training-groups__card-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:.75rem;min-height:3.15rem}
.training-groups__card-heading h3{font-weight:650;overflow-wrap:anywhere}
.training-groups__size{display:flex;align-items:baseline;gap:.2rem;flex-shrink:0;font-size:1.5rem;font-variant-numeric:tabular-nums;font-weight:600;color:var(--color-accent)}
.training-groups__size span{font-size:.75rem;font-weight:400;color:var(--color-text-secondary)}
.training-groups__stats{display:grid;grid-template-columns:1fr 1fr 1.15fr;gap:.55rem;padding:.85rem 0;border-top:1px solid var(--color-border-subtle);border-bottom:1px solid var(--color-border-subtle)}
.training-groups__stats>div{display:grid;align-content:start;gap:.3rem;min-width:0}
.training-groups__stats dt{color:var(--color-text-secondary);font-size:.72rem;line-height:1.4}
.training-groups__stats dd{font-size:1.25rem;line-height:1.3;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.training-groups__stats small{font-size:.65rem;font-weight:400;margin-left:.2rem;color:var(--color-text-secondary)}
.training-groups__stats span{font-size:.76rem;color:var(--color-text-secondary)}
.training-groups__range{display:grid;gap:.4rem;font-size:.74rem;color:var(--color-text-secondary)}
.training-groups__range-track{position:relative;height:5px;border-radius:3px;background:var(--color-bg-selected);overflow:hidden}
.training-groups__range-track i{display:block;position:absolute;height:100%;border-radius:3px;background:var(--color-accent)}
.training-groups__classes{font-size:.8rem;line-height:1.6;color:var(--color-text-primary)}
.training-groups__card-footer{display:flex;justify-content:space-between;align-items:center;gap:.7rem;margin-top:auto;padding-top:.2rem}
.training-groups__card-footer>div{display:grid;gap:.2rem;font-size:.76rem;line-height:1.5}
.training-groups__card-footer strong{font-weight:500}.training-groups__card-footer span{color:var(--color-text-secondary);font-size:.72rem}
.training-groups__card-footer button{flex-shrink:0}.training-groups__more{justify-self:center}
.training-groups__alert{font-size:.8rem;line-height:1.6;color:var(--color-warning);background:var(--color-warning-subtle);padding:.6rem .8rem;border-radius:var(--radius-control)}
.training-groups__empty{padding:2rem 1rem;line-height:1.8;color:var(--color-text-secondary);background:var(--color-bg-subtle)}
.training-groups__editor{display:grid;gap:1rem}
.training-groups__stats--selection{max-width:760px;gap:1.5rem}.training-groups__stats--selection dd{font-size:1.45rem}
.training-groups fieldset{display:flex;flex-wrap:wrap;gap:.6rem 1rem;padding:.8rem;border:1px solid var(--color-border-default);border-radius:var(--radius-control)}
.training-groups legend{font-size:.85rem;font-weight:600;padding:0 .35rem}
.training-groups fieldset label{display:flex;gap:.4rem;align-items:center;font-size:.85rem}
.training-groups__targets{display:grid;gap:.35rem;font-size:.8rem;color:var(--color-text-secondary)}
.training-groups__members-heading{display:flex;align-items:baseline;justify-content:space-between;gap:.75rem;flex-wrap:wrap}
.training-groups__members-heading h4 span{font-size:.8rem;font-weight:400;margin-left:.3rem;color:var(--color-text-secondary)}
.training-groups__members-heading p{font-size:.78rem;color:var(--color-text-secondary)}
.training-groups__members{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,245px),1fr));gap:.6rem;align-items:start}
.training-groups__members details{padding:.8rem;background:var(--color-bg-subtle);border:1px solid var(--color-border-subtle);border-radius:7px}
.training-groups__members summary{cursor:pointer;font-size:.85rem;list-style:none}
.training-groups__member-name{display:flex;justify-content:space-between;align-items:center;gap:.4rem;flex-wrap:wrap}
.training-groups__member-name label{display:flex;align-items:center;gap:.25rem;font-weight:500}.training-groups__member-name>span{font-size:.72rem;color:var(--color-text-secondary)}
.training-groups__member-scores{display:flex;flex-wrap:wrap;justify-content:space-between;gap:.3rem;margin-top:.65rem;font-size:.72rem;color:var(--color-text-secondary)}
.training-groups__member-scores b{font-weight:500;color:var(--color-text-primary)}.training-groups__member-scores small{font-size:.68rem}
.training-groups__members summary::after{content:'作答依据 ▾';display:block;margin-top:.65rem;font-size:.7rem;color:var(--color-accent)}
.training-groups__members details[open]>summary::after{content:'收起依据 ▴'}
.training-groups__members details>div{margin-top:.7rem;padding-top:.7rem;border-top:1px solid var(--color-border-default);font-size:.78rem;line-height:1.7}
.training-groups__editor-tools{display:flex;gap:.5rem;flex-wrap:wrap}
.training-groups__add{display:grid;gap:.5rem;max-height:260px;overflow:auto;font-size:.85rem}.training-groups__add>input{padding:.6rem;border:1px solid var(--color-border-default);border-radius:var(--radius-control)}
.training-groups__editor>footer{padding-top:1rem;border-top:1px solid var(--color-border-default)}
.training-groups__unassigned,.training-groups__explanation{font-size:.8rem;line-height:1.8}
.training-groups__unassigned summary,.training-groups__explanation summary{cursor:pointer;color:var(--color-text-secondary)}
.training-groups__unassigned p,.training-groups__explanation p{padding:.4rem 0;max-width:80ch;color:var(--color-text-secondary)}
@media(max-width:760px){.training-groups__heading,.training-groups__editor>footer{align-items:flex-start;flex-wrap:wrap}.training-groups__stats--selection{gap:.5rem}.training-groups__stats--selection dd{font-size:1.2rem}.training-groups__editor>footer button{width:100%}}
</style>
