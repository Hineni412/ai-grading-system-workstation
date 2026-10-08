<script setup lang="ts">
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import StudentMatchSelect from './StudentMatchSelect.vue'
import type { ScanStageId } from './scan-stage'
import {
  PREFLIGHT_TRACK,
  REVIEW_BUCKET_LABELS,
  REVIEW_FILTERS,
  formatBytes,
  type ScanPreflightController,
} from './useScanPreflight'

const props = defineProps<{ recon: ScanPreflightController }>()
const emit = defineEmits<{ 'select-stage': [id: ScanStageId] }>()

const {
  store,
  selectedStudents,
  decisionNotice,
  decisionFailed,
  savingDecisionCount,
  editableUploadBatch,
  uploadFrozen,
  inputChanged,
  supplementReady,
  preflightActive,
  preflightProgress,
  preflightProgressText,
  preflightTrackIndex,
  pendingCount,
  matchConflicts,
  invalidCount,
  preflightPageAssignment,
  reviewFilter,
  selectedRow,
  selectedRowIndex,
  filteredReviewRows,
  reviewListItems,
  reviewRows,
  reviewCounts,
  nextNonEmptyFilter,
  reviewNeedsHandling,
  selectedConflict,
  otherConflictTargets,
  assignedLabels,
  selectedRowId,
  selectedMatches,
  detailSide,
  detailImageUrl,
  selectRow,
  stepRow,
  jumpToRow,
  setReviewFilter,
  onReviewListKeydown,
  rowDisplayName,
  pendingPickName,
  rowSuggestions,
  applySuggestion,
  suggestionLabel,
  matchMethodLabel,
  itemText,
  itemSourceLabel,
  decisionFor,
  decisionStatus,
  submitDecisions,
  saveDecision,
  conflictMessages,
  classEvidence,
  transferMatch,
  keepConflictTarget,
  openViewer,
  chooseFiles,
  chooseAppendFiles,
  confirmReplacement,
  otherAssignedPapers,
} = props.recon

function selectStage(id: ScanStageId): void {
  emit('select-stage', id)
}
</script>

<template>
  <section class="scan-stage" aria-labelledby="prepare-title">
    <div class="scan-stage__heading">
      <h2 id="prepare-title">答卷与预检</h2>
      <div class="scan-stage__side">
        <strong v-if="store.preflight">自动匹配 {{ store.preflight.summary.auto_matched ?? 0 }} · 异常 {{ store.preflight.summary.issues ?? 0 }}</strong>
        <strong v-else>{{ editableUploadBatch?.file_count ?? 0 }} 个文件 · {{ formatBytes(editableUploadBatch?.total_bytes ?? 0) }}</strong>
        <AppButton
          v-if="store.gradingRun?.allowed_actions.includes('supplement_new_matches')"
          data-action="supplement"
          variant="secondary"
          :disabled="!supplementReady || Boolean(store.busyAction)"
          @click="store.supplement"
        >补批新匹配的答卷</AppButton>
        <AppButton
          v-if="store.preflight && !matchConflicts.length && !inputChanged"
          :variant="pendingCount ? 'secondary' : 'primary'"
          @click="selectStage('grade')"
        >下一步：批改</AppButton>
      </div>
    </div>
    <p v-if="store.replacementBatch" class="scan-warning">
      <strong>正在准备替换答卷。</strong>旧答卷和已有成果目前仍然保留；只有点击“确认替换并开始预检”后才会永久清除。
    </p>
    <div v-if="uploadFrozen" class="scan-upload-summary">
      <div class="scan-upload-summary__files">
        <span v-for="file in editableUploadBatch?.files ?? []" :key="file.id" class="scan-upload-summary__file">
          <strong>{{ file.name }}</strong><small>{{ formatBytes(file.size_bytes) }}</small>
          <template v-if="file.appended">
            <small>新增</small>
            <AppButton variant="ghost" :disabled="Boolean(store.busyAction)" @click="store.remove(file.id, true)">移除</AppButton>
          </template>
        </span>
      </div>
      <div class="scan-upload-summary__actions">
        <label class="scan-upload-summary__action" :data-disabled="Boolean(store.busyAction)">
          新增答卷文件
          <input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            :disabled="Boolean(store.busyAction)" @change="chooseAppendFiles">
        </label>
        <AppButton variant="secondary" :disabled="Boolean(store.busyAction)" @click="store.beginReplacement">替换全部答卷</AppButton>
      </div>
    </div>
    <label v-else class="scan-drop" :data-disabled="Boolean(store.busyAction)">
      <input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
        :disabled="Boolean(store.busyAction)" @change="chooseFiles">
      <span class="scan-drop__icon" aria-hidden="true">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 16.5V4.81m0 0L8.03 8.03M12 4.81l3.22 3.22" />
          <path d="M3.75 15.75v1.5A2.25 2.25 0 0 0 6 19.5h12a2.25 2.25 0 0 0 2.25-2.25v-1.5" />
        </svg>
      </span>
      <strong>选择或继续添加答卷</strong>
      <span>支持 PDF、JPG、PNG；重复内容会自动跳过。</span>
    </label>
    <div v-if="!uploadFrozen && editableUploadBatch?.files.length" class="scan-file-list">
      <div v-for="file in editableUploadBatch.files" :key="file.id">
        <span><strong>{{ file.name }}</strong><small>{{ file.sha256_prefix }} · {{ formatBytes(file.size_bytes) }}</small></span>
        <AppButton v-if="editableUploadBatch.state === 'draft'" variant="ghost" @click="store.remove(file.id)">移除</AppButton>
      </div>
    </div>
    <div v-if="store.replacementBatch" class="scan-stage__actions">
      <AppButton variant="secondary" :disabled="!store.replacementBatch.file_count || Boolean(store.busyAction)" @click="store.clear">清空新答卷</AppButton>
      <AppButton variant="secondary" :disabled="Boolean(store.busyAction)" @click="store.cancelReplacement">取消重新上传</AppButton>
      <AppButton variant="primary" :disabled="!store.replacementBatch.file_count || Boolean(store.busyAction)" @click="confirmReplacement">确认替换并开始预检</AppButton>
    </div>
    <div v-else-if="store.uploadBatch?.state === 'draft'" class="scan-stage__actions">
      <AppButton variant="secondary" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.clear">清空</AppButton>
      <AppButton variant="primary" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.analyze">开始预检</AppButton>
    </div>

    <div v-if="preflightActive" class="scan-progress" role="status" aria-live="polite">
      <div class="scan-progress__copy">
        <div>
          <strong>扫描预检 {{ preflightProgress }}%</strong>
          <span>{{ preflightProgressText }}</span>
        </div>
        <span>{{ store.preflightJob?.stage || '准备中' }}</span>
      </div>
      <ol class="scan-preflight-track" aria-label="预检步骤">
        <li
          v-for="(label, index) in PREFLIGHT_TRACK" :key="label"
          :class="{ 'is-current': index === preflightTrackIndex, 'is-done': index < preflightTrackIndex }"
        >{{ label }}</li>
      </ol>
      <progress :value="preflightProgress" max="100" aria-label="扫描预检进度">
        {{ preflightProgress }}%
      </progress>
      <p v-if="store.preflight">正在生成新结果，下方暂时显示上一次完整预检结果。</p>
    </div>
    <StatePanel v-if="!store.preflight" kind="empty" :title="store.preflightJobId ? '预检正在后台运行，完成后这里会自动更新。' : '上传答卷后运行识别，再在这里核对学生归属和异常页。'">
      <template #actions><AppButton v-if="store.uploadBatch?.state === 'frozen' && !store.preflightJobId" data-action="retry-preflight" variant="secondary" @click="store.analyze">运行或重新运行预检</AppButton></template>
    </StatePanel>
    <template v-else>
      <FeedbackBanner
        v-if="inputChanged"
        tone="warning"
        title="已新增答卷文件，下方结果还未包含它们"
        :description="store.preflight?.appended_file_count ? `批次中现有 ${store.preflight.appended_file_count} 个新增文件；原有匹配决定会在重新预检时保留。` : '答卷文件已变化；原有匹配决定会在重新预检时保留。'"
        action-label="重新运行预检"
        @action="store.analyze"
      />
      <p v-if="store.preflight.identity" class="scan-identity">
        本机识别，未调用 AI · 自动匹配 {{ store.preflight.identity.auto }} 份 · 需确认 {{ store.preflight.identity.needs_confirmation }} 份
      </p>
      <div class="scan-stats" data-scan-reconciliation>
        <span>扫描 <strong>{{ store.preflight.summary.scanned_papers ?? 0 }}</strong> 份</span>
        <span>已匹配 <strong>{{ store.preflight.summary.matched_papers ?? 0 }}</strong> 份</span>
        <span>对应 <strong>{{ store.preflight.summary.unique_students ?? 0 }}</strong> 名学生</span>
        <span>有效可批改 <strong>{{ store.preflight.summary.ready_to_grade ?? 0 }}</strong> 份</span>
        <span>未决 <strong>{{ pendingCount }}</strong> 份</span>
        <span>无效 <strong>{{ invalidCount }}</strong> 份</span><FeedbackBanner v-if="matchConflicts.length" role="alert" tone="error">有 {{ matchConflicts.length }} 份答卷归属冲突，处理后才能开始批改</FeedbackBanner>
        <span v-else-if="pendingCount" class="scan-stats__warn">仍有 {{ pendingCount }} 份异常答卷待处理</span>
        <span class="scan-stats__note">PDF 第 1 页为{{ preflightPageAssignment.first_page_role === 'front' ? '正面' : '反面' }}（正面在{{ preflightPageAssignment.front_page_parity === 'odd' ? '奇数页' : '偶数页' }}）</span>
      </div>
      <StatePanel v-if="!reviewRows.length" kind="empty" title="所有答卷已自动匹配。">
        <template #actions><AppButton :variant="pendingCount ? 'secondary' : 'primary'" @click="selectStage('grade')">下一步：批改</AppButton></template>
      </StatePanel>
      <div v-else class="scan-review">
        <nav class="scan-review-nav" aria-label="答卷核对列表" @keydown="onReviewListKeydown">
          <div class="scan-review-filters" role="group" aria-label="核对筛选">
            <button
              v-for="filter in REVIEW_FILTERS" :key="filter.key" type="button"
              class="scan-review-filter" :data-review-filter="filter.key"
              :aria-pressed="reviewFilter === filter.key"
              @click="reviewFilter = filter.key"
            >{{ filter.label }} {{ reviewCounts[filter.key] }}</button>
          </div>
          <div class="scan-review-list">
            <template v-for="entry in reviewListItems" :key="entry.key">
              <div v-if="entry.type === 'subheader'" class="scan-review-subheader">{{ entry.text }}</div>
              <button
                v-else-if="entry.row" type="button" class="scan-review-item"
                :data-row-key="entry.row.key"
                :aria-current="selectedRow?.key === entry.row.key ? 'true' : undefined"
                @click="selectRow(entry.row)"
              >
                <span class="scan-review-item__line">
                  <strong>{{ rowDisplayName(entry.row) }}</strong>
                  <span class="scan-review-item__tag" :data-kind="entry.row.bucket">{{ REVIEW_BUCKET_LABELS[entry.row.bucket] }}</span>
                </span>
                <small class="scan-review-item__meta">
                  {{ itemSourceLabel(entry.row.item) || (entry.row.targetType === 'group' ? '答卷' : '异常答卷') }}<template v-if="entry.row.targetType === 'group'"> · {{ matchMethodLabel(entry.row.item) }}</template>
                </small>
                <small v-if="pendingPickName(entry.row)" class="scan-review-item__pick">已选：{{ pendingPickName(entry.row) }}</small>
              </button>
            </template>
          </div>
          <div
            v-if="selectedMatches.length || store.busyAction === 'decisions' || savingDecisionCount || decisionNotice"
            class="scan-review-footer"
          >
            <div v-if="selectedMatches.length || store.busyAction === 'decisions'" class="scan-stage__actions scan-match-actions">
              <AppButton variant="secondary" data-match-selected :disabled="!selectedMatches.length || Boolean(store.busyAction)" @click="submitDecisions(selectedMatches, true)">
                {{ store.busyAction === 'decisions' ? '正在保存…' : `一键匹配（${selectedMatches.length} 项）` }}
              </AppButton>
              <span>重复归属的项会保留待处理</span>
            </div>
            <p v-if="savingDecisionCount" class="scan-decision-state" role="status">正在保存 {{ savingDecisionCount }} 项，请稍候…</p>
            <p v-else-if="decisionNotice" data-match-result :class="decisionFailed ? 'scan-match-conflict' : 'scan-decision-state'" :role="decisionFailed ? 'alert' : 'status'">{{ decisionNotice }}</p>
          </div>
        </nav>
        <section class="scan-review-detail" aria-live="polite">
          <template v-if="selectedRow">
            <header class="scan-review-detail__header">
              <div class="scan-review-detail__title">
                <h3>{{ rowDisplayName(selectedRow) }}<span class="scan-review-item__tag" :data-kind="selectedRow.bucket">{{ REVIEW_BUCKET_LABELS[selectedRow.bucket] }}</span></h3>
                <small :title="itemText(selectedRow.item, 'source_label') || undefined">
                  {{ itemSourceLabel(selectedRow.item) || (selectedRow.targetType === 'group' ? '答卷' : '异常答卷') }}<template v-if="selectedRow.targetType === 'group'"> · {{ matchMethodLabel(selectedRow.item) }}</template>
                </small>
              </div>
              <div class="scan-review-detail__pager">
                <AppButton variant="secondary" :disabled="selectedRowIndex <= 0" @click="stepRow(-1)">上一份</AppButton>
                <AppButton variant="secondary" :disabled="selectedRowIndex < 0 || selectedRowIndex >= filteredReviewRows.length - 1" @click="stepRow(1)">下一份</AppButton>
                <AppButton variant="secondary" :disabled="!selectedRow.item.front_media_url" @click="openViewer(selectedRow.targetType, selectedRow.item, 'front')">全屏查看</AppButton>
              </div>
            </header>
            <div class="scan-review-detail__decision">
              <div class="scan-review-detail__messages">
                <small v-if="selectedRow.item.detected_name && (selectedRow.targetType === 'issue' || selectedRow.item.student_name)">识别姓名：{{ selectedRow.item.detected_name }}</small>
                <small v-if="selectedRow.item.issue_type === 'ambiguous_name'">名单中有重名，请按学号和班级选择。</small>
                <small v-if="selectedRow.targetType === 'issue' && !selectedRow.item.back_media_url" class="scan-match-conflict">缺反面，不能直接归属；可标无效或稍后处理。</small>
                <small v-if="classEvidence(selectedRow.item, selectedRow.targetType)">{{ classEvidence(selectedRow.item, selectedRow.targetType) }}</small>
                <small v-if="conflictMessages(selectedRow.targetType, String(selectedRow.item.id))" class="scan-match-conflict">{{ conflictMessages(selectedRow.targetType, String(selectedRow.item.id)) }}</small>
                <small v-else-if="selectedRow.targetType === 'group' && !decisionFor('group', String(selectedRow.item.id))" class="scan-decision-state">已自动匹配，可直接批改；如有误可更正。</small>
                <small v-if="decisionFor(selectedRow.targetType, String(selectedRow.item.id))"
                  :data-saved-decision="selectedRow.key" class="scan-decision-state">
                  {{ decisionStatus(decisionFor(selectedRow.targetType, String(selectedRow.item.id))) }}
                </small>
              </div>
              <div v-if="selectedConflict" class="scan-review-detail__conflict">
                <AppButton variant="secondary" :disabled="Boolean(store.busyAction)"
                  @click="keepConflictTarget(selectedConflict.card, selectedConflict.target)">保留这份</AppButton>
                <span v-for="other in otherConflictTargets" :key="`${other.targetType}:${other.targetId}`">
                  另一份：<button type="button" class="text-button" @click="jumpToRow(`${other.targetType}:${other.targetId}`)">{{ other.label }}</button>
                </span>
              </div>
              <div v-if="rowSuggestions(selectedRow).length" class="scan-review-detail__suggest">
                <span>建议</span>
                <button
                  v-for="(suggestion, index) in rowSuggestions(selectedRow)" :key="suggestion.student_id"
                  type="button" class="scan-suggestion-chip"
                  :data-suggestion-id="suggestion.student_id"
                  :aria-pressed="selectedStudents[selectedRowId] === suggestion.student_id"
                  @click="applySuggestion(selectedRow, suggestion)"
                >{{ suggestionLabel(suggestion) }}<em v-if="index === 0" class="scan-suggestion-chip__best">最可能</em></button>
              </div>
              <div class="scan-review-detail__controls">
                <StudentMatchSelect
                  v-model="selectedStudents[String(selectedRow.item.id)]"
                  :students="store.students"
                  :assigned="assignedLabels"
                  placeholder="姓名、学号或拼音"
                  :aria-label="selectedRow.targetType === 'group' ? '重新选择学生' : '选择学生'"
                />
                <AppButton v-if="!otherAssignedPapers(String(selectedRow.item.id)).length" variant="secondary"
                  :disabled="!selectedStudents[String(selectedRow.item.id)] || !selectedRow.item.back_media_url || Boolean(store.busyAction)"
                  @click="saveDecision(selectedRow.targetType, String(selectedRow.item.id), 'match')">{{ selectedRow.targetType === 'group' ? '确认归属' : '匹配' }}</AppButton>
                <AppButton variant="ghost" :disabled="Boolean(store.busyAction)" @click="saveDecision(selectedRow.targetType, String(selectedRow.item.id), 'invalid')">标记无效</AppButton>
                <AppButton variant="ghost" :disabled="Boolean(store.busyAction)" @click="saveDecision(selectedRow.targetType, String(selectedRow.item.id), 'pending')">稍后处理</AppButton>
              </div>
              <div v-if="otherAssignedPapers(String(selectedRow.item.id)).length" class="scan-transfer" data-transfer-panel>
                <span>
                  {{ store.students.find((s) => s.id === selectedStudents[selectedRowId])?.name || '该学生' }}
                  已归属「{{ otherAssignedPapers(String(selectedRow.item.id)).map((p) => p.label).join('、') }}」。
                </span>
                <AppButton variant="secondary" :disabled="!selectedRow.item.back_media_url || Boolean(store.busyAction)"
                  @click="transferMatch(selectedRow.targetType, String(selectedRow.item.id), 'pending')">改用当前卷（原卷转待处理）</AppButton>
                <AppButton variant="ghost" :disabled="Boolean(store.busyAction)"
                  @click="transferMatch(selectedRow.targetType, String(selectedRow.item.id), 'invalid')">原卷标无效</AppButton>
              </div>
            </div>
            <div class="scan-review-detail__image">
              <div class="scan-review-detail__sides" role="group" aria-label="正反面">
                <button type="button" :aria-pressed="detailSide === 'front'" @click="detailSide = 'front'">正面</button>
                <button type="button" :disabled="!selectedRow.item.back_media_url" :aria-pressed="detailSide === 'back'" @click="detailSide = 'back'">
                  {{ selectedRow.item.back_media_url ? '反面' : '无反面' }}
                </button>
              </div>
              <img
                v-if="detailImageUrl"
                :src="detailImageUrl"
                :alt="`${itemSourceLabel(selectedRow.item) || '答卷'}${detailSide === 'front' ? '正面' : '反面'}`"
                loading="lazy" decoding="async"
              >
              <p v-else class="scan-review-detail__missing">暂无{{ detailSide === 'front' ? '正面' : '反面' }}图片</p>
            </div>
          </template>
          <div v-else class="scan-review-detail__empty">
            <template v-if="reviewNeedsHandling && nextNonEmptyFilter">
              <p>这一类已处理完</p>
              <AppButton variant="secondary" @click="setReviewFilter(nextNonEmptyFilter)">
                查看{{ REVIEW_FILTERS.find((f) => f.key === nextNonEmptyFilter)?.label }}
              </AppButton>
            </template>
            <template v-else>
              <p>可以开始批改</p>
              <AppButton :variant="pendingCount ? 'secondary' : 'primary'" @click="selectStage('grade')">下一步：批改</AppButton>
            </template>
          </div>
        </section>
      </div>

    </template>
  </section>
</template>
