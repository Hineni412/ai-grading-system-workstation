<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import type { GraphQueryInput } from '../../api/graph'
import type { SessionSummary } from '../../api/sessions'
import type { StudentSummary } from '../../api/students'

const props = withDefaults(defineProps<{
  sessions: SessionSummary[]
  currentSessionId: number | null
  students: StudentSummary[]
  modelValue: GraphQueryInput | null
  applying: boolean
  applyLabel?: string
  scoreProfiles?: Record<string, Record<string, unknown>>
  curriculumVolumeId?: string | null
  evidenceFrom?: 'chapter' | 'student'
}>(), {
  applyLabel: '立即更新',
  scoreProfiles: () => ({}),
  curriculumVolumeId: null,
  evidenceFrom: 'chapter',
})

const emit = defineEmits<{
  apply: [query: GraphQueryInput]
}>()

const examMode = ref<'current' | 'manual' | 'cross_exam'>('current')
const manualSessionIds = ref<number[]>([])
const classIds = ref<string[]>([])
const scoreMin = ref<string>('')
const scoreMax = ref<string>('')
const useHistory = ref(true)
// 勾选即入队；队列是唯一统计候选，换筛选条件不影响队列内容。
const queueIds = ref<string[]>([])
const search = ref('')
const moreOpen = ref(false)
const validationMessage = ref('')
let synchronizing = false
let debounceTimer: ReturnType<typeof setTimeout> | null = null

const classes = computed(() => [...new Set(
  props.students.flatMap((student) => student.class_name ? [student.class_name] : []),
)].sort((left, right) => left.localeCompare(right, 'zh-CN')))

const visibleStudents = computed(() => {
  const query = search.value.trim().toLocaleLowerCase('zh-CN')
  return props.students.filter((student) => {
    if (classIds.value.length && (!student.class_name || !classIds.value.includes(student.class_name))) return false
    return !query || `${student.student_code} ${student.name}`
      .toLocaleLowerCase('zh-CN').includes(query)
  })
})

const currentSessionName = computed(() => props.sessions.find(
  (item) => item.id === props.currentSessionId,
)?.name ?? '尚未选择考试')
const primarySessions = computed(() => props.curriculumVolumeId
  ? props.sessions.filter(item => item.curriculum_volume_id === props.curriculumVolumeId)
  : props.sessions)
const otherSessions = computed(() => props.curriculumVolumeId
  ? props.sessions.filter(item => item.curriculum_volume_id !== props.curriculumVolumeId)
  : [])

const snapshotText = computed(() => {
  const exam = examMode.value === 'current'
    ? currentSessionName.value
    : examMode.value === 'manual'
      ? `${manualSessionIds.value.length} 场指定考试`
      : '全部可用考试'
  const roster = classIds.value.length ? `${classIds.value.length} 个班级` : '全部班级'
  const range = scoreMin.value || scoreMax.value
    ? `得分率 ${scoreMin.value || '0'}%–${scoreMax.value || '100'}%`
    : '全部得分率'
  const manual = queueIds.value.length
    ? `队列 ${queueIds.value.length} 人`
    : '未指定学生'
  return `${exam} · ${roster} · ${range} · ${manual} · ${useHistory.value ? '缺考参考历史' : '仅本次成绩'}`
})

watch(
  () => props.modelValue,
  (query) => {
    if (!query) return
    synchronizing = true
    if (query.scope.mode === 'selected') {
      // 队列模式只回同步队列本身；用户输入的班级、得分率区间与考试条件保持原样，
      // 避免 selected 查询回包把本地筛选输入重置。
      queueIds.value = [...(query.scope.student_ids ?? [])]
    } else {
      examMode.value = query.exam_scope.mode
      manualSessionIds.value = query.exam_scope.mode === 'manual'
        ? [...query.exam_scope.session_ids]
        : []
      classIds.value = query.scope.mode === 'class'
        ? [...(query.scope.class_ids ?? (query.scope.class_id ? [query.scope.class_id] : []))]
        : []
      scoreMin.value = query.scope.score_rate_min === null || query.scope.score_rate_min === undefined
        ? '' : String(Math.round(query.scope.score_rate_min * 1000) / 10)
      scoreMax.value = query.scope.score_rate_max === null || query.scope.score_rate_max === undefined
        ? '' : String(Math.round(query.scope.score_rate_max * 1000) / 10)
      queueIds.value = []
      useHistory.value = query.scope.use_historical_fallback !== false
    }
    void nextTick(() => { synchronizing = false })
  },
  { immediate: true, deep: true },
)

function rate(value: string): number | undefined {
  if (!value.trim()) return undefined
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) return Number.NaN
  return parsed / 100
}

function toggleStudent(studentId: string): void {
  queueIds.value = queueIds.value.includes(studentId)
    ? queueIds.value.filter((item) => item !== studentId)
    : [...queueIds.value, studentId]
}

function selectAllFiltered(): void {
  const merged = new Set(queueIds.value)
  for (const student of cardStudents.value) merged.add(String(student.id))
  queueIds.value = [...merged]
}

function clearQueue(): void {
  queueIds.value = []
}

function toggleClass(className: string): void {
  classIds.value = classIds.value.includes(className)
    ? classIds.value.filter((item) => item !== className)
    : [...classIds.value, className].sort((left, right) => left.localeCompare(right, 'zh-CN'))
}

function profileRate(studentId: string): number | null {
  const raw = props.scoreProfiles[studentId]?.score_rate
  return typeof raw === 'number' && Number.isFinite(raw) && raw >= 0 && raw <= 1 ? raw : null
}

function rateText(studentId: string): string {
  const value = profileRate(studentId)
  return value === null ? '—' : `${Math.round(value * 100)}%`
}

// 与后端 _rate_matches 语义一致：设定了得分率区间时，无成绩学生不进入卡片列表。
const cardStudents = computed(() => {
  const minimum = rate(scoreMin.value)
  const maximum = rate(scoreMax.value)
  const rangeSet = (minimum !== undefined && !Number.isNaN(minimum))
    || (maximum !== undefined && !Number.isNaN(maximum))
  return visibleStudents.value.filter((student) => {
    if (!rangeSet) return true
    const value = profileRate(String(student.id))
    if (value === null) return false
    if (minimum !== undefined && !Number.isNaN(minimum) && value < minimum) return false
    if (maximum !== undefined && !Number.isNaN(maximum) && value > maximum) return false
    return true
  })
})

const rosterVisible = computed(() => Boolean(classIds.value.length || moreOpen.value))

// 队列 chips 按队列顺序解析学生；勾选只改卡片勾选态，不再把卡片移到单独的分组。
const queuedStudents = computed(() => {
  const byId = new Map(props.students.map((student) => [String(student.id), student]))
  return queueIds.value
    .map((id) => byId.get(id))
    .filter((student): student is StudentSummary => Boolean(student))
})

function toggleSession(sessionId: number): void {
  manualSessionIds.value = manualSessionIds.value.includes(sessionId)
    ? manualSessionIds.value.filter((item) => item !== sessionId)
    : [...manualSessionIds.value, sessionId]
}

function apply(): void {
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
  validationMessage.value = ''
  const minimum = rate(scoreMin.value)
  const maximum = rate(scoreMax.value)
  if (Number.isNaN(minimum) || Number.isNaN(maximum)) {
    validationMessage.value = '得分率请输入 0–100 之间的数字'
    return
  }
  if (minimum !== undefined && maximum !== undefined && minimum > maximum) {
    validationMessage.value = '得分率下限不能大于上限'
    return
  }
  let examScope: GraphQueryInput['exam_scope']
  if (examMode.value === 'current') {
    if (props.currentSessionId === null) {
      validationMessage.value = '请先在顶部选择当前考试'
      return
    }
    examScope = { mode: 'current', session_ids: [props.currentSessionId] }
  } else if (examMode.value === 'manual') {
    if (!manualSessionIds.value.length) {
      validationMessage.value = '请至少选择一场考试'
      return
    }
    examScope = { mode: 'manual', session_ids: [...manualSessionIds.value] }
  } else {
    examScope = { mode: 'cross_exam' }
  }
  emit('apply', {
    exam_scope: examScope,
    scope: queueIds.value.length
      ? {
          mode: 'selected',
          student_ids: [...queueIds.value],
          include_student_ids: [],
          exclude_student_ids: [],
          use_historical_fallback: useHistory.value,
        }
      : {
          mode: classIds.value.length ? 'class' : 'all',
          ...(classIds.value.length ? { class_ids: [...classIds.value] } : {}),
          ...(minimum !== undefined ? { score_rate_min: minimum } : {}),
          ...(maximum !== undefined ? { score_rate_max: maximum } : {}),
          include_student_ids: [],
          exclude_student_ids: [],
          use_historical_fallback: useHistory.value,
        },
  })
}

watch(
  [examMode, manualSessionIds, classIds, scoreMin, scoreMax, useHistory, queueIds],
  () => {
    if (synchronizing) return
    if (debounceTimer) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(apply, 420)
  },
  { deep: true },
)

onBeforeUnmount(() => {
  if (debounceTimer) clearTimeout(debounceTimer)
})

function openMoreFilters(): void {
  moreOpen.value = true
}

defineExpose({ openMoreFilters })
</script>

<template>
  <section class="evidence-scope" aria-labelledby="evidence-scope-title">
    <div class="evidence-scope__quickbar">
      <div class="evidence-scope__snapshot-text">
        <span>当前证据范围</span>
        <strong>{{ snapshotText }}</strong>
      </div>
      <label>
        <span id="evidence-scope-title">考试</span>
        <select v-model="examMode">
          <option value="current">当前考试</option>
          <option value="manual">指定考试</option>
          <option value="cross_exam">全部可用考试</option>
        </select>
      </label>
      <details class="evidence-scope__classes">
        <summary>班级 · {{ classIds.length ? `${classIds.length} 个` : '全部' }}</summary>
        <div>
          <label v-for="name in classes" :key="name">
            <input type="checkbox" :checked="classIds.includes(name)" @change="toggleClass(name)">
            <span>{{ name }}</span>
          </label>
        </div>
      </details>
      <button type="button" class="primary-button" data-testid="apply-evidence-scope" @click="apply">
        {{ applying ? '正在更新' : applyLabel }}
      </button>
      <button
        type="button"
        class="quiet-button"
        :aria-expanded="moreOpen"
        @click="moreOpen = !moreOpen"
      >
        {{ moreOpen ? '收起筛选' : '更多筛选' }}
      </button>
    </div>

    <div class="evidence-scope__queue" aria-label="队列">
      <template v-if="queueIds.length">
        <span class="evidence-scope__queue-label">队列 {{ queueIds.length }} 人：</span>
        <ul>
          <li v-for="student in queuedStudents" :key="student.id">
            <span>{{ student.name }}</span>
            <button
              type="button"
              :aria-label="`从队列移除${student.name}`"
              @click="toggleStudent(String(student.id))"
            >×</button>
          </li>
        </ul>
        <button type="button" class="evidence-scope__queue-clear" @click="clearQueue">清空</button>
      </template>
      <p v-else class="evidence-scope__queue-empty">队列为空 · 勾选下方卡片加入</p>
    </div>

    <div v-if="examMode === 'manual'" class="evidence-scope__sessions">
      <button v-for="session in primarySessions" :key="session.id" type="button" :class="{ active: manualSessionIds.includes(session.id) }" @click="toggleSession(session.id)">
        {{ session.name }}
      </button>
      <details v-if="otherSessions.length" class="evidence-scope__other-sessions">
        <summary>其他学期或未归类考试（{{ otherSessions.length }}）</summary>
        <div>
          <button v-for="session in otherSessions" :key="session.id" type="button" :class="{ active: manualSessionIds.includes(session.id) }" @click="toggleSession(session.id)">
            {{ session.name }}
          </button>
        </div>
      </details>
    </div>

    <div v-if="moreOpen" class="evidence-scope__more">
      <div class="evidence-scope__more-row">
        <fieldset class="evidence-scope__range">
          <legend>得分率区间</legend>
          <label><span>最低</span><input v-model="scoreMin" inputmode="decimal" placeholder="0" aria-label="最低得分率"><i>%</i></label>
          <b>—</b>
          <label><span>最高</span><input v-model="scoreMax" inputmode="decimal" placeholder="100" aria-label="最高得分率"><i>%</i></label>
        </fieldset>
        <label class="evidence-scope__history">
          <input v-model="useHistory" type="checkbox">
          <span><strong>缺考时参考历史</strong><small>仅使用目标考试之前的成绩与相似知识点证据</small></span>
        </label>
      </div>
    </div>

    <div v-if="rosterVisible" class="evidence-scope__roster">
      <header>
        <div><strong>指定学生</strong><p>勾选即固定进队列；换筛选条件不影响队列。队列为空时按上方条件自动圈定。</p></div>
        <div class="evidence-scope__roster-tools">
          <input v-model="search" type="search" placeholder="搜索姓名或学号" aria-label="搜索学生">
          <button
            type="button"
            class="quiet-button"
            :disabled="!cardStudents.length"
            @click="selectAllFiltered"
          >全选筛选结果</button>
        </div>
      </header>
      <section class="evidence-scope__group" aria-label="筛选结果">
        <h3>筛选结果 · {{ cardStudents.length }} 人</h3>
        <p v-if="!cardStudents.length" class="evidence-scope__roster-empty">当前条件下没有匹配的学生。</p>
        <div v-else class="evidence-scope__cards">
          <label
            v-for="student in cardStudents"
            :key="student.id"
            class="evidence-scope__card"
            :class="{ 'is-checked': queueIds.includes(String(student.id)) }"
          >
            <input
              type="checkbox"
              :checked="queueIds.includes(String(student.id))"
              :aria-label="`选择${student.name}`"
              @change="toggleStudent(String(student.id))"
            >
            <span class="evidence-scope__card-name"><b>{{ student.name }}</b><small>{{ student.student_code }}</small></span>
            <strong>{{ rateText(String(student.id)) }}</strong>
            <RouterLink
              class="evidence-scope__card-evidence"
              :to="{ name: 'student-evidence', params: { studentId: student.id }, query: { from: evidenceFrom } }"
              @click.stop
            >证据</RouterLink>
          </label>
        </div>
      </section>
    </div>
    <p v-if="validationMessage" class="evidence-scope__error" role="alert">{{ validationMessage }}</p>
  </section>
</template>

<style scoped>
.evidence-scope { border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); }
.evidence-scope > :last-child { border-end-start-radius: var(--radius-control); border-end-end-radius: var(--radius-control); }
.evidence-scope__quickbar { display: flex; flex-wrap: wrap; align-items: end; gap: 14px; padding: 12px 16px; }
.evidence-scope__snapshot-text { display: flex; flex: 1 1 280px; align-items: baseline; gap: 12px; min-width: 0; padding: 4px 0 4px 12px; border-left: 4px solid var(--color-accent); }
.evidence-scope__snapshot-text span { color: var(--color-text-secondary); font-size: 12px; white-space: nowrap; }
.evidence-scope__snapshot-text strong { color: var(--color-text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.evidence-scope__quickbar > label { flex: 0 1 170px; min-width: 140px; }
label > span, legend { display: block; margin-bottom: 6px; color: var(--color-text-secondary); font-size: 12px; }
select, input { width: 100%; min-height: var(--control-height-large); border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-primary); padding: 8px 10px; }
.evidence-scope__more { border-top: 1px solid var(--color-border-subtle); }
.evidence-scope__more-row { display: flex; flex-wrap: wrap; align-items: end; gap: 24px; padding: 16px; }
.evidence-scope__more-row .evidence-scope__range { flex: 1 1 320px; }
.evidence-scope__more-row .evidence-scope__history { flex: 1 1 320px; }
.evidence-scope__range { display: flex; align-items: end; gap: 7px; min-width: 0; border: 0; padding: 0; margin: 0; }
.evidence-scope__range label { position: relative; flex: 1; }
.evidence-scope__range label span { position: absolute; width: 1px; height: 1px; overflow: hidden; }
.evidence-scope__range input { padding-right: 28px; }
.evidence-scope__range i { position: absolute; right: 10px; bottom: 11px; color: var(--color-text-muted); font-style: normal; }
.evidence-scope__range b { padding-bottom: 11px; color: var(--color-text-muted); }
.evidence-scope__history { display: flex; gap: 9px; align-items: center; min-height: var(--control-height-large); }
.evidence-scope__history input { width: 18px; min-height: auto; }
.evidence-scope__history span { margin: 0; }
.evidence-scope__history strong, .evidence-scope__history small { display: block; }
.evidence-scope__history small { margin-top: 2px; color: var(--color-text-muted); }
.evidence-scope__classes { position: relative; }
.evidence-scope__classes summary { min-height: var(--control-height-large); padding: 10px; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); cursor: pointer; list-style: none; }
.evidence-scope__classes > div { position: absolute; z-index: 8; top: 44px; left: 0; display: grid; min-width: 220px; max-height: 240px; overflow: auto; padding: 8px; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); box-shadow: var(--shadow-overlay); }
.evidence-scope__classes label { display: flex; align-items: center; gap: 8px; padding: 7px; }
.evidence-scope__classes input { width: 16px; min-height: auto; }
.evidence-scope__classes span { margin: 0; }
.primary-button, .quiet-button, .evidence-scope__sessions button { border-radius: var(--radius-control); min-height: var(--control-height-large); padding: 8px 14px; cursor: pointer; }
.primary-button { border: 1px solid var(--color-accent); background: var(--color-accent); color: var(--primary-foreground); font-weight: 700; }
.primary-button:hover { border-color: var(--color-accent-hover); background: var(--color-accent-hover); }
.quiet-button { border: 1px solid var(--color-border-default); background: var(--color-bg-surface); color: var(--color-text-primary); }
.evidence-scope__sessions { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 16px 16px; }
.evidence-scope__sessions button { min-height: var(--control-height-default); border: 1px solid var(--color-border-default); background: var(--color-bg-surface); }
.evidence-scope__sessions button.active { border-color: var(--color-accent); background: var(--color-bg-selected); color: var(--color-accent); }
.evidence-scope__other-sessions { flex-basis: 100%; color: var(--color-text-secondary); }
.evidence-scope__other-sessions summary { width: max-content; padding-block: 6px; cursor: pointer; }
.evidence-scope__other-sessions > div { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 6px; }
.evidence-scope__roster { border-top: 1px solid var(--color-border-subtle); padding: 16px; }
.evidence-scope__roster header { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 16px; margin-bottom: 12px; }
.evidence-scope__roster p { margin: 4px 0 0; color: var(--color-text-secondary); font-size: 13px; }
.evidence-scope__roster-tools { display: flex; align-items: center; gap: 8px; }
.evidence-scope__roster-tools input { max-width: 260px; }
.evidence-scope__roster-tools .quiet-button { min-height: var(--control-height-default); white-space: nowrap; }
.evidence-scope__queue { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 0 16px 12px; }
/* 队列行常显：占位文案与 chip 保持相同盒高（内容 18px + 上下 2px 内边距 + 1px 边框 = 24px），
 * 勾选/清空时行高不变，下方内容不再纵向跳动。注意覆盖全局 button 的 min-height: 34px。 */
.evidence-scope__queue-empty { margin: 0; padding: 3px 0; color: var(--color-text-muted); font-size: var(--font-size-caption); line-height: var(--line-height-body); }
.evidence-scope__queue-label { color: var(--color-text-secondary); font-size: var(--font-size-caption); white-space: nowrap; }
.evidence-scope__queue ul { display: flex; flex-wrap: wrap; gap: 6px; margin: 0; padding: 0; list-style: none; }
.evidence-scope__queue li { display: flex; align-items: center; gap: 4px; padding: 2px 4px 2px 10px; border: 1px solid var(--color-accent); border-radius: 999px; background: var(--color-bg-selected); color: var(--color-text-primary); font-size: var(--font-size-caption); }
.evidence-scope__queue li button { width: 18px; min-width: 18px; height: 18px; min-height: 0; padding: 0; border: 0; border-radius: 50%; background: transparent; color: var(--color-text-secondary); line-height: 1; cursor: pointer; }
.evidence-scope__queue li button:hover { background: var(--color-accent-subtle); color: var(--color-accent); }
.evidence-scope__queue-clear { min-height: 0; padding: 2px 10px; border: 1px solid var(--color-border-default); border-radius: 999px; background: var(--color-bg-surface); color: var(--color-text-secondary); font-size: var(--font-size-caption); line-height: var(--line-height-body); cursor: pointer; }
.evidence-scope__queue-clear:hover { border-color: var(--color-danger); color: var(--color-danger); }
.evidence-scope__group h3 { margin: 0 0 8px; font-size: 13px; color: var(--color-text-secondary); }
.evidence-scope__roster-empty { padding: 8px 0; }
.evidence-scope__cards { display: grid; grid-template-columns: repeat(auto-fill,minmax(150px,1fr)); gap: 8px; max-height: 220px; overflow: auto; }
.evidence-scope__card { display: grid; grid-template-columns: auto 1fr auto; gap: 4px 8px; align-items: center; padding: 8px 10px; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); cursor: pointer; }
.evidence-scope__card.is-checked { border-color: var(--color-accent); background: var(--color-bg-selected); }
.evidence-scope__card input { width: 16px; min-height: auto; }
.evidence-scope__card-name { display: grid; min-width: 0; line-height: 1.2; }
.evidence-scope__card-name small { color: var(--color-text-muted); }
.evidence-scope__card > strong { color: var(--color-text-secondary); font-size: 12px; white-space: nowrap; }
.evidence-scope__card-evidence { grid-column: 2 / -1; justify-self: start; font-size: var(--font-size-caption); color: var(--color-accent-active); text-decoration: none; }
.evidence-scope__card-evidence:hover { text-decoration: underline; }
.evidence-scope__error { margin: 0; padding: 0 16px 14px; color: var(--color-danger); }
@media (max-width: 1100px) { .evidence-scope__quickbar > label, .evidence-scope__classes { flex: 1 1 200px; } .primary-button { width: 100%; } }
</style>
