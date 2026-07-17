<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { GradingMode, ScanDecision } from '../api/scan-grading'
import { useScanGradingStore } from '../stores/scan-grading'

const route = useRoute()
const router = useRouter()
const store = useScanGradingStore()
const sessionId = computed(() => Number(route.params.sessionId))
const confirmPending = ref(false)
const selectedStudents = ref<Record<string, number | undefined>>({})

const stage = computed(() => {
  if (store.gradingRun) return 4
  if (store.preflight) return 3
  if (store.uploadBatch?.state === 'frozen') return 2
  return 1
})
const pendingCount = computed(() => store.preflight?.pending_issue_count ?? 0)
const canStart = computed(() => Boolean(store.preflight)
  && !store.gradingRun && (pendingCount.value === 0 || confirmPending.value) && !store.busyAction)
const invalidCount = computed(() => store.preflight?.decisions
  .filter((item) => item.target_type === 'issue' && item.action === 'invalid').length ?? 0)
const missingBackCount = computed(() => store.preflight?.issues
  .filter((item) => item.issue_type === 'missing_back' || item.issue_type === 'orphan_page').length ?? 0)
const lowConfidenceGroups = computed(() => store.preflight?.groups.filter((item) => (
  item.match_method !== 'exact' || Number(item.match_score ?? 0) < 1
)) ?? [])
const confirmedDecisions = computed(() => store.preflight?.decisions
  .filter((item) => item.action !== 'pending') ?? [])
const runStateLabel = computed(() => ({
  running: '批改运行中', pause_requested: '正在安全暂停', paused: '已安全暂停',
  starting: '批改任务正在启动',
  interrupted: '上次运行已中断', cancel_requested: '正在安全取消', cancelled: '本次运行已取消',
  completed: '本次运行已完成', failed: '本次运行有失败项',
}[store.gradingRun?.state ?? ''] ?? store.gradingRun?.state ?? ''))

function loadRoute(): void {
  if (Number.isSafeInteger(sessionId.value) && sessionId.value > 0) void store.load(sessionId.value)
}
function chooseFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  if (input.files?.length) void store.addFiles([...input.files])
  input.value = ''
}
function start(mode: GradingMode): void { void store.begin(mode, confirmPending.value) }
function decisionFor(targetType: 'group' | 'issue', targetId: string): ScanDecision | undefined {
  return store.preflight?.decisions.find((item) => (
    item.target_type === targetType && item.target_id === targetId
  ))
}
function decisionStatus(decision: ScanDecision | undefined): string {
  if (!decision) return ''
  if (decision.action === 'invalid') return '已保存：标记无效'
  if (decision.action === 'pending') return '已保存：稍后处理'
  const student = store.students.find((item) => item.id === decision.student_id)
  return `已保存：匹配至 ${student?.name ?? '已选学生'}`
}
function decisionTargetLabel(decision: ScanDecision): string {
  const items = decision.target_type === 'group'
    ? store.preflight?.groups
    : store.preflight?.issues
  const item = items?.find((candidate) => String(candidate.id) === decision.target_id)
  return String(
    item?.student_name
    || item?.detected_name
    || item?.source_label
    || (decision.target_type === 'group' ? '自动匹配答卷' : '异常答卷'),
  )
}
function saveDecision(targetType: 'group' | 'issue', targetId: string, action: 'match' | 'invalid' | 'pending'): void {
  const decisions: ScanDecision[] = [...(store.preflight?.decisions ?? [])
    .filter((item) => !(item.target_type === targetType && item.target_id === targetId))]
  decisions.push({ target_type: targetType, target_id: targetId, action,
    ...(action === 'match' ? { student_id: selectedStudents.value[targetId] } : {}) })
  void store.saveDecisions(decisions)
}
function cancelRun(): void {
  if (window.confirm('取消后，本次运行会结束；已经完成的成绩会保留，未完成部分以后可重新发起。确认取消吗？')) {
    void store.cancel()
  }
}
function beginNewBatch(): void {
  if (window.confirm('开始新批次会保留既有成绩和运行记录，并建立一份空的上传队列。确认继续吗？')) {
    void store.newBatch()
  }
}

onMounted(loadRoute)
watch(sessionId, loadRoute)
watch(
  () => `${store.uploadBatch?.batch_id ?? ''}:${store.uploadBatch?.revision ?? -1}:${store.preflight?.revision ?? -1}`,
  () => { confirmPending.value = false },
)
</script>

<template>
  <article class="scan-grading">
    <header class="scan-grading__header">
      <div>
        <span class="scan-grading__eyebrow">批改执行 · 考试会话 {{ sessionId }}</span>
        <h1>整班答卷批改</h1>
        <p>从答卷入库到异常核对，再到正式批改与补批，状态都保存在本机服务中。</p>
      </div>
      <button type="button" class="secondary" @click="router.push('/sessions')">返回考试配置</button>
    </header>

    <div class="scan-grading__body">
      <ol class="scan-run-rail" aria-label="批改执行阶段">
        <li v-for="(label, index) in ['上传答卷', '扫描预检', '开始批改', '运行与补批']" :key="label"
          :data-state="stage > index + 1 ? 'complete' : stage === index + 1 ? 'current' : 'waiting'">
          <span>{{ index + 1 }}</span><strong>{{ label }}</strong>
        </li>
      </ol>
      <main class="scan-grading__main">

    <p v-if="store.errorMessage" class="scan-grading__notice" role="alert">{{ store.errorMessage }}</p>
    <p v-if="store.loadState === 'loading'" class="scan-grading__notice" role="status">正在恢复本次批改工作区…</p>

    <template v-if="store.workspace">
      <section class="scan-stage" aria-labelledby="upload-title">
        <div class="scan-stage__heading">
          <div><span>01</span><h2 id="upload-title">上传答卷</h2></div>
          <strong>{{ store.uploadBatch?.file_count ?? 0 }} 个文件 · {{ Math.ceil((store.uploadBatch?.total_bytes ?? 0) / 1024) }} KB</strong>
        </div>
        <label class="scan-drop" :data-disabled="store.uploadBatch?.state === 'frozen'">
          <input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            :disabled="store.uploadBatch?.state === 'frozen' || Boolean(store.busyAction)" @change="chooseFiles">
          <strong>{{ store.uploadBatch?.state === 'frozen' ? '上传批次已冻结' : '选择或继续添加答卷' }}</strong>
          <span>支持 PDF、JPG、PNG；重复内容会自动跳过。</span>
        </label>
        <div v-if="store.uploadBatch?.files.length" class="scan-file-list">
          <div v-for="file in store.uploadBatch.files" :key="file.id">
            <span><strong>{{ file.name }}</strong><small>{{ file.sha256_prefix }} · {{ Math.ceil(file.size_bytes / 1024) }} KB</small></span>
            <button v-if="store.uploadBatch.state === 'draft'" type="button" class="text-button" @click="store.remove(file.id)">移除</button>
          </div>
        </div>
        <div v-if="store.uploadBatch?.state === 'draft'" class="scan-stage__actions">
          <button type="button" class="secondary" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.clear">清空</button>
          <button type="button" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.analyze">冻结并开始预检</button>
        </div>
      </section>

      <section class="scan-stage" aria-labelledby="preflight-title">
        <div class="scan-stage__heading">
          <div><span>02</span><h2 id="preflight-title">扫描预检</h2></div>
          <strong v-if="store.preflight">自动匹配 {{ store.preflight.summary.auto_matched ?? 0 }} · 异常 {{ store.preflight.summary.issues ?? 0 }}</strong>
        </div>
        <div v-if="!store.preflight" class="scan-empty">
          <p>{{ store.preflightJobId ? '预检正在后台运行，完成后这里会自动更新。' : '冻结答卷后运行识别，再在这里核对学生归属和异常页。' }}</p>
          <button v-if="store.uploadBatch?.state === 'frozen' && !store.preflightJobId" data-action="retry-preflight" type="button" class="secondary" @click="store.analyze">运行或重新运行预检</button>
        </div>
        <template v-else>
          <p v-if="pendingCount" class="scan-warning"><strong>仍有 {{ pendingCount }} 份异常答卷待处理</strong>。它们可以暂时跳过，不会阻塞其余学生批改。</p>
          <div v-if="lowConfidenceGroups.length" class="scan-issue-list" aria-label="低可信自动匹配">
            <div v-for="group in lowConfidenceGroups" :key="String(group.id)" class="scan-issue-row">
              <div class="scan-evidence" :aria-label="`${group.source_label || '答卷'}正反面证据`">
                <img v-if="group.front_media_url" :src="String(group.front_media_url)"
                  :alt="`${group.source_label || '答卷'}正面`" loading="lazy" decoding="async">
                <img v-if="group.back_media_url" :src="String(group.back_media_url)"
                  :alt="`${group.source_label || '答卷'}反面`" loading="lazy" decoding="async">
              </div>
              <span class="scan-item-copy">
                <strong>{{ group.student_name || group.detected_name || '待核对姓名' }}</strong>
                <small>{{ group.source_label }} · {{ group.match_method }}</small>
                <small v-if="decisionFor('group', String(group.id))"
                  :data-saved-decision="`group:${String(group.id)}`" class="scan-decision-state">
                  {{ decisionStatus(decisionFor('group', String(group.id))) }}
                </small>
              </span>
              <select v-model="selectedStudents[String(group.id)]" aria-label="重新选择学生">
                <option :value="undefined">重新选择学生</option>
                <option v-for="student in store.students" :key="student.id" :value="student.id">{{ student.name }} · {{ student.student_code }}</option>
              </select>
              <button type="button" class="secondary" :disabled="!selectedStudents[String(group.id)]" @click="saveDecision('group', String(group.id), 'match')">确认改绑</button>
            </div>
          </div>
          <div v-if="store.preflight.issues.length" class="scan-issue-list">
            <div v-for="issue in store.preflight.issues" :key="String(issue.id)" class="scan-issue-row">
              <div class="scan-evidence" :aria-label="`${issue.source_label || '异常答卷'}正反面证据`">
                <img v-if="issue.front_media_url" :src="String(issue.front_media_url)"
                  :alt="`${issue.source_label || '异常答卷'}正面`" loading="lazy" decoding="async">
                <img v-if="issue.back_media_url" :src="String(issue.back_media_url)"
                  :alt="`${issue.source_label || '异常答卷'}反面`" loading="lazy" decoding="async">
                <span v-else class="scan-evidence__missing">无反面</span>
              </div>
              <span class="scan-item-copy">
                <strong>{{ issue.detected_name || '未识别姓名' }}</strong>
                <small>{{ issue.source_label || '异常答卷' }}</small>
                <small v-if="decisionFor('issue', String(issue.id))"
                  :data-saved-decision="`issue:${String(issue.id)}`" class="scan-decision-state">
                  {{ decisionStatus(decisionFor('issue', String(issue.id))) }}
                </small>
              </span>
              <select v-model="selectedStudents[String(issue.id)]" aria-label="选择学生">
                <option :value="undefined">选择学生</option>
                <option v-for="student in store.students" :key="student.id" :value="student.id">{{ student.name }} · {{ student.student_code }}</option>
              </select>
              <button type="button" class="secondary" :disabled="!selectedStudents[String(issue.id)]" @click="saveDecision('issue', String(issue.id), 'match')">匹配</button>
              <button type="button" class="text-button" @click="saveDecision('issue', String(issue.id), 'invalid')">标记无效</button>
              <button type="button" class="text-button" @click="saveDecision('issue', String(issue.id), 'pending')">稍后处理</button>
            </div>
          </div>
          <details v-if="confirmedDecisions.length" class="scan-confirmed-decisions">
            <summary>已确认 {{ confirmedDecisions.length }} 项</summary>
            <ul>
              <li v-for="decision in confirmedDecisions"
                :key="`${decision.target_type}:${decision.target_id}`">
                <span>{{ decisionTargetLabel(decision) }}</span>
                <strong>{{ decisionStatus(decision) }}</strong>
              </li>
            </ul>
          </details>
        </template>
      </section>

      <section class="scan-stage" aria-labelledby="start-title">
        <div class="scan-stage__heading"><div><span>03</span><h2 id="start-title">开始批改</h2></div></div>
        <label v-if="pendingCount" class="scan-confirm">
          <input v-model="confirmPending" data-confirm-pending type="checkbox">
          我已知晓：{{ pendingCount }} 份异常答卷本轮会跳过，之后可继续匹配和补批。
        </label>
        <p v-if="store.preflight" class="scan-start-summary">
          本轮可批改 {{ store.preflight.summary.ready_to_grade ?? 0 }} 份；待处理异常 {{ pendingCount }} 份；
          已标无效 {{ invalidCount }} 份；缺反面/孤页 {{ missingBackCount }} 份；缺考候选 {{ store.preflight.summary.absent_candidates ?? 0 }} 人。
        </p>
        <div class="grading-modes">
          <button type="button" data-grading-mode="full_paper" :disabled="!canStart" @click="start('full_paper')">
            <span>整卷批改</span><strong>按学生逐份完成</strong><small>适合日常整班批改，过程直观。</small>
          </button>
          <button type="button" data-grading-mode="hybrid_batch" :disabled="!canStart" @click="start('hybrid_batch')">
            <span>混合批改</span><strong>客观题批量 + 主观题并行</strong><small>适合题量较大、希望提高吞吐的班级。</small>
          </button>
        </div>
      </section>

      <section class="scan-stage" aria-labelledby="run-title">
        <div class="scan-stage__heading"><div><span>04</span><h2 id="run-title">运行与补批</h2></div><strong v-if="store.gradingRun">{{ runStateLabel }}</strong></div>
        <div v-if="store.gradingRun" class="run-console">
          <div class="run-counts">
            <span><strong>已完成 {{ store.gradingRun.counts.graded }}</strong></span>
            <span>处理中 {{ store.gradingRun.counts.grading }}</span><span>待处理 {{ store.gradingRun.counts.pending }}</span>
            <span>跳过 {{ store.gradingRun.counts.skipped }}</span><span>失败 {{ store.gradingRun.counts.failed }}</span>
          </div>
          <div class="scan-stage__actions">
            <button v-if="store.gradingRun.allowed_actions.includes('pause')" data-action="pause" type="button" @click="store.control('pause')">安全暂停</button>
            <button v-if="store.gradingRun.allowed_actions.includes('resume')" data-action="resume" type="button" @click="store.control('resume')">继续本次运行</button>
            <button v-if="store.gradingRun.allowed_actions.includes('retry_failed')" data-action="retry-failed" type="button" class="secondary" @click="store.control('retry-failed')">仅重试失败项</button>
            <button v-if="store.gradingRun.allowed_actions.includes('supplement_new_matches')" data-action="supplement" type="button" class="secondary" @click="store.supplement">补批后来匹配的答卷</button>
            <button v-if="store.gradingRun.allowed_actions.includes('cancel')" data-action="cancel" type="button" class="danger" @click="cancelRun">取消本次运行</button>
            <button v-if="['cancelled', 'completed', 'failed'].includes(store.gradingRun.state)" data-action="new-batch" type="button" class="secondary" @click="beginNewBatch">开始新批次</button>
          </div>
        </div>
        <p v-else class="scan-empty">尚未开始批改。启动后，刷新页面仍可恢复这里的运行状态。</p>
      </section>
    </template>
      </main>
    </div>
  </article>
</template>
