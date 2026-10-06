<script setup lang="ts">
import type { ReportHistoryJob, ReportType } from '../../api/exports'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'
import AppButton from '../design-system/AppButton.vue'
import { useFileCenterStore } from '../../stores/file-center'
import {
  fileStatusLabel,
  formatShortTime,
  formatTime,
  isRetainedReport,
  reportDisplayStatus,
  reportFilename,
  reportLiveDetail,
  reviewNoteCount,
  statusLabel,
  statusTone,
  type ReportRow,
} from './report-format'

defineProps<{
  rows: ReportRow[]
  historyOpen: Set<ReportType>
  isPopover: boolean
  originalsAvailable?: boolean
  personalSummary?: string
}>()
const emit = defineEmits<{
  'export-personal': []
  'generate': [type: ReportType, forceRegenerate: boolean]
  'cancel-job': [jobId: number]
  'download': [job: ReportHistoryJob]
  'delete-report': [job: ReportHistoryJob]
  'open-excel-settings': []
  'open-review-notes': []
  'toggle-history': [type: ReportType]
}>()

const fileCenter = useFileCenterStore()
</script>

<template>
  <ul v-if="isPopover" class="file-center__rows">
    <li v-for="row in rows" :key="row.type" class="file-center__row">
      <div class="file-center__row-head">
        <strong>{{ row.title }}</strong>
        <span class="file-report-table__kind">{{ row.kind }}</span>
        <template v-if="row.liveJob">
          <span class="file-status file-status--progress">
            {{ statusLabel(row.liveJob.status) }}
            {{ Math.round(row.liveJob.progress * 100) }}%
          </span>
          <span v-if="reportLiveDetail(row.liveJob)" class="file-report-table__detail">
            {{ reportLiveDetail(row.liveJob) }}
          </span>
        </template>
        <span
          v-else-if="row.latest"
          class="file-status"
          :class="`file-status--${statusTone(reportDisplayStatus(row.latest))}`"
        >
          {{ fileStatusLabel(reportDisplayStatus(row.latest)) }}
        </span>
        <span v-else class="file-status file-status--muted">尚未生成</span>
        <span v-if="row.latest" class="file-center__row-time">
          {{ formatShortTime(row.latest.finished_at ?? row.latest.created_at) }}
        </span>
      </div>
      <p
        v-if="row.latest !== null && reviewNoteCount(row.latest) > 0"
        class="file-center__row-notes"
      >
        发现 {{ reviewNoteCount(row.latest) }} 条建议核对，
        <button
          type="button"
          class="file-link-button"
          data-testid="open-review-notes"
          @click="emit('open-review-notes')"
        >在成绩中心查看</button>
      </p>
      <p v-if="row.type === 'personal_analysis_html'" class="file-center__row-notes">{{ personalSummary }}</p>
      <p v-if="row.type === 'annotated_original_pdf' && originalsAvailable === false" class="file-center__row-notes">原卷已清理，不能再导出批注原卷。</p>
      <div class="file-center__row-actions">
        <AppButton variant="danger"
          v-if="row.liveJob && !TERMINAL_JOB_STATUSES.has(row.liveJob.status)"
          :data-testid="`cancel-job-${row.liveJob.id}`"
          @click="emit('cancel-job', row.liveJob.id)"
        >
          取消
        </AppButton>
        <AppButton variant="primary"
          v-if="row.latest && reportDisplayStatus(row.latest) === 'available'"
          :data-testid="`download-report-${row.latest.id}`"
          @click="emit('download', row.latest)"
        >
          下载
        </AppButton>
        <template v-if="row.type === 'personal_analysis_html'">
          <AppButton variant="primary" data-testid="export-personal-reports" @click="emit('export-personal')">导出…</AppButton>
        </template>
        <template v-else>
        <AppButton variant="secondary"
          v-if="row.latest && reportDisplayStatus(row.latest) === 'available'"
          :data-testid="`regenerate-${row.type}`"
          :disabled="
            fileCenter.submittingKey === `report:${row.type}`
            || fileCenter.reportContext?.has_results === false
            || (row.type === 'annotated_original_pdf' && originalsAvailable === false)
          "
          @click="emit('generate', row.type, true)"
        >
          重新生成
        </AppButton>
        <AppButton variant="secondary"
          v-else
          :data-testid="`generate-${row.type}`"
          :disabled="
            fileCenter.submittingKey === `report:${row.type}`
            || fileCenter.reportContext?.has_results === false
            || (row.type === 'annotated_original_pdf' && originalsAvailable === false)
          "
          @click="emit('generate', row.type, false)"
        >
          {{ row.latest ? '重新生成' : '生成' }}
        </AppButton>
        </template>
        <AppButton variant="ghost"
          v-if="row.type === 'score_excel'"
          data-testid="configure-score-excel"
          :disabled="fileCenter.reportContext?.has_results === false"
          @click="emit('open-excel-settings')"
        >
          导出设置
        </AppButton>
        <button
          v-if="row.history.length"
          type="button"
          class="file-link-button"
          :aria-expanded="historyOpen.has(row.type)"
          @click="emit('toggle-history', row.type)"
        >
          历史 {{ row.history.length }} 份
        </button>
      </div>
      <ul v-if="row.history.length && historyOpen.has(row.type)" class="file-report-table__history-list">
        <li v-for="job in row.history" :key="job.id">
          <span class="file-report-table__history-name">
            {{ reportFilename(job) }}
            <small>{{ formatTime(job.created_at) }} · 任务 #{{ job.id }}</small>
          </span>
          <span class="file-status" :class="`file-status--${statusTone(reportDisplayStatus(job))}`">
            {{ fileStatusLabel(reportDisplayStatus(job)) }}
          </span>
          <AppButton variant="ghost"
            v-if="reportDisplayStatus(job) === 'available'"
            :data-testid="`download-report-${job.id}`"
            @click="emit('download', job)"
          >
            下载
          </AppButton>
          <AppButton variant="ghost"
            v-if="reportDisplayStatus(job) === 'available' && isRetainedReport(job)"
            :data-testid="`delete-report-${job.id}`"
            @click="emit('delete-report', job)"
          >
            删除
          </AppButton>
          <AppButton variant="ghost"
            v-else-if="
              row.type !== 'personal_analysis_html' && (reportDisplayStatus(job) === 'expired'
              || reportDisplayStatus(job) === 'unavailable'
              || reportDisplayStatus(job) === 'stale')
            "
            :disabled="row.type === 'annotated_original_pdf' && originalsAvailable === false"
            @click="emit('generate', job.payload.report_type as ReportType, true)"
          >
            重新生成
          </AppButton>
        </li>
      </ul>
    </li>
  </ul>
  <table v-else class="app-table file-report-table">
    <thead>
      <tr>
        <th scope="col">文件</th>
        <th scope="col">状态</th>
        <th scope="col">最近生成</th>
        <th scope="col" class="file-report-table__actions-head">操作</th>
      </tr>
    </thead>
    <tbody>
      <template v-for="row in rows" :key="row.type">
        <tr>
          <th scope="row" :title="row.description">
            {{ row.title }}<span class="file-report-table__kind">{{ row.kind }}</span>
            <span v-if="row.latest" class="file-report-table__filename">{{ reportFilename(row.latest) }}</span>
            <p v-if="row.type === 'annotated_original_pdf' && originalsAvailable === false" class="file-report-table__notes">原卷已清理，不能再导出批注原卷。</p>
            <p
              v-if="row.latest !== null && reviewNoteCount(row.latest) > 0"
              class="file-report-table__notes"
            >
              发现 {{ reviewNoteCount(row.latest) }} 条建议核对，
              <button
                type="button"
                class="file-link-button"
                data-testid="open-review-notes"
                @click="emit('open-review-notes')"
              >在成绩中心查看</button>
            </p>
          </th>
          <td>
            <template v-if="row.liveJob">
              <span class="file-status file-status--progress">
                {{ statusLabel(row.liveJob.status) }}
                {{ Math.round(row.liveJob.progress * 100) }}%
              </span>
              <span v-if="reportLiveDetail(row.liveJob)" class="file-report-table__detail">
                {{ reportLiveDetail(row.liveJob) }}
              </span>
            </template>
            <span
              v-else-if="row.latest"
              class="file-status"
              :class="`file-status--${statusTone(reportDisplayStatus(row.latest))}`"
            >
              {{ fileStatusLabel(reportDisplayStatus(row.latest)) }}
            </span>
            <span v-else class="file-status file-status--muted">尚未生成</span>
          </td>
          <td>{{ row.latest ? formatTime(row.latest.finished_at ?? row.latest.created_at) : '—' }}</td>
          <td class="file-report-table__actions">
            <p v-if="row.type === 'personal_analysis_html'">{{ personalSummary }}</p>
            <AppButton variant="danger"
              v-if="row.liveJob && !TERMINAL_JOB_STATUSES.has(row.liveJob.status)"
              :data-testid="`cancel-job-${row.liveJob.id}`"
              @click="emit('cancel-job', row.liveJob.id)"
            >
              取消
            </AppButton>
            <AppButton variant="primary"
              v-if="row.latest && reportDisplayStatus(row.latest) === 'available'"
              :data-testid="`download-report-${row.latest.id}`"
              @click="emit('download', row.latest)"
            >
              下载
            </AppButton>
            <template v-if="row.type === 'personal_analysis_html'">
              <AppButton variant="primary" data-testid="export-personal-reports" @click="emit('export-personal')">导出…</AppButton>
            </template>
            <template v-else>
            <AppButton variant="secondary"
              v-if="row.latest && reportDisplayStatus(row.latest) === 'available'"
              :data-testid="`regenerate-${row.type}`"
              :disabled="
                fileCenter.submittingKey === `report:${row.type}`
                || fileCenter.reportContext?.has_results === false
                || (row.type === 'annotated_original_pdf' && originalsAvailable === false)
              "
              @click="emit('generate', row.type, true)"
            >
              重新生成
            </AppButton>
            <AppButton variant="secondary"
              v-else
              :data-testid="`generate-${row.type}`"
              :disabled="
                fileCenter.submittingKey === `report:${row.type}`
                || fileCenter.reportContext?.has_results === false
                || (row.type === 'annotated_original_pdf' && originalsAvailable === false)
              "
              @click="emit('generate', row.type, false)"
            >
              {{ row.latest ? '重新生成' : '生成' }}
            </AppButton>
            </template>
            <AppButton variant="ghost"
              v-if="row.type === 'score_excel'"
              data-testid="configure-score-excel"
              :disabled="fileCenter.reportContext?.has_results === false"
              @click="emit('open-excel-settings')"
            >
              导出设置
            </AppButton>
            <button
              v-if="row.history.length"
              type="button"
              class="file-link-button"
              :aria-expanded="historyOpen.has(row.type)"
              @click="emit('toggle-history', row.type)"
            >
              历史 {{ row.history.length }} 份
            </button>
          </td>
        </tr>
        <tr v-if="row.history.length && historyOpen.has(row.type)" class="file-report-table__history">
          <td colspan="4">
            <ul class="file-report-table__history-list">
              <li v-for="job in row.history" :key="job.id">
                <span class="file-report-table__history-name">
                  {{ reportFilename(job) }}
                  <small>{{ formatTime(job.created_at) }} · 任务 #{{ job.id }}</small>
                </span>
                <span class="file-status" :class="`file-status--${statusTone(reportDisplayStatus(job))}`">
                  {{ fileStatusLabel(reportDisplayStatus(job)) }}
                </span>
                <AppButton variant="ghost"
                  v-if="reportDisplayStatus(job) === 'available'"
                  :data-testid="`download-report-${job.id}`"
                  @click="emit('download', job)"
                >
                  下载
                </AppButton>
                <AppButton variant="ghost"
                  v-if="reportDisplayStatus(job) === 'available' && isRetainedReport(job)"
                  :data-testid="`delete-report-${job.id}`"
                  @click="emit('delete-report', job)"
                >
                  删除
                </AppButton>
                <AppButton variant="ghost"
                  v-else-if="
                    row.type !== 'personal_analysis_html' && (reportDisplayStatus(job) === 'expired'
                    || reportDisplayStatus(job) === 'unavailable'
                    || reportDisplayStatus(job) === 'stale')
                  "
                  :disabled="row.type === 'annotated_original_pdf' && originalsAvailable === false"
                  @click="emit('generate', job.payload.report_type as ReportType, true)"
                >
                  重新生成
                </AppButton>
              </li>
            </ul>
          </td>
        </tr>
      </template>
    </tbody>
  </table>
</template>
