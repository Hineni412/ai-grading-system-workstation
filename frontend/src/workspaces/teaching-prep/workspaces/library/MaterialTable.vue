<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import type { JobResponse } from '../../../../api/jobs'
import type {
  DeleteMaterialSourceResult,
  MaterialDeletionPreview,
  MaterialVersion,
  SemesterMaterialRole,
} from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { MATERIAL_ROLES, roleLabel, suggestedRole, useSemesterRecordLookup } from './libraryShared'

const catalog = useTeachingPrepCatalogStore()
const semesterRecordFor = useSemesterRecordLookup()

const emit = defineEmits<{ notice: [message: string] }>()

const attachingMaterialId = ref<string | null>(null)
const existingRoleDrafts = reactive<Record<string, SemesterMaterialRole>>({})
const workbookSeriesDrafts = reactive<Record<string, string>>({})
const workbookVolumeDrafts = reactive<Record<string, 'A' | 'B'>>({})
const showOtherTermMaterials = ref(false)
const batchRunning = ref(false)

// Legacy archived rows are surfaced as ordinary materials so teachers can
// delete them; the recycle-bin concept is no longer part of this workspace.
const currentTermMaterials = computed(() => catalog.materials.filter(item => Boolean(
  semesterRecordFor(item),
)))
const otherTermMaterials = computed(() => catalog.materials.filter(item => !semesterRecordFor(item)))

function notice(message: string): void {
  emit('notice', message)
}

function parseJobForMaterial(material: MaterialVersion): JobResponse | null {
  return catalog.materialParseJobs[material.id] ?? null
}

function materialStatusLabel(material: MaterialVersion): { tone: 'success' | 'info' | 'warning' | 'neutral'; label: string } {
  const record = semesterRecordFor(material)
  const job = parseJobForMaterial(material)
  if (job && ['queued', 'running'].includes(job.status)) {
    return { tone: 'info', label: job.detail || '解析中' }
  }
  if (materialNeedsContinue(material)) return { tone: 'warning', label: '解析未完成' }
  if (record && !record.is_active) return { tone: 'neutral', label: '已移出本学期' }
  if (record) return { tone: 'success', label: '已解析' }
  return { tone: 'neutral', label: '未加入本学期' }
}

function materialProgressLabel(material: MaterialVersion): string {
  const job = parseJobForMaterial(material)
  const expected = material.parse_expected_unit_count ?? material.unit_count ?? 0
  const checkpoint = expected > 0
    ? `已保存：预览 ${material.preview_completed_count ?? 0}/${expected} · 文字 ${material.ocr_completed_count ?? 0}/${material.ocr_total_count ?? 0}`
    : ''
  if (!job) return checkpoint
  if (job.status === 'succeeded') return '逐页处理完成'
  if (job.status === 'failed') return `${checkpoint} · 处理未完成，可继续`
  if (job.status === 'cancelled') return `${checkpoint} · 已停止，可继续`
  return job.detail || (job.status === 'queued' ? '等待后台处理' : '正在逐页处理')
}

function materialNeedsContinue(material: MaterialVersion): boolean {
  const job = parseJobForMaterial(material)
  if (job && ['queued', 'running'].includes(job.status)) return false
  const expected = material.parse_expected_unit_count ?? material.unit_count
  return expected === null || expected === undefined
    ? material.inspection_status === 'uninspected'
    : (material.preview_completed_count ?? 0) < expected
      || !['ready', 'scanned_no_text'].includes(material.inspection_status)
}

async function openMaterial(item: MaterialVersion): Promise<void> {
  notice((item.unit_count ?? 0) > 0 ? '正在打开已保存的页级目录…' : '正在本机解析资料并生成逐页预览，大文件需要一些时间…')
  try {
    await catalog.openMaterial(item)
    notice(`已选中「${item.display_name}」，可在下方「AI 整理课时树」中核对页段。`)
  } catch {
    notice(catalog.errorMessage || '资料没有完成解析，请保留原文件后重试。')
  }
}

async function continueMaterial(item: MaterialVersion): Promise<void> {
  notice('正在从已保存的检查点继续，只补未完成页面…')
  try {
    await catalog.parseMaterialInBackground(item)
    await openMaterial(item)
  } catch {
    notice(catalog.errorMessage || '续跑没有完成，已经保存的页面仍然保留。')
  }
}

async function attachExistingMaterial(item: MaterialVersion): Promise<void> {
  if (semesterRecordFor(item) || !catalog.selectedSemester) return
  attachingMaterialId.value = item.id
  try {
    const role = existingRoleDrafts[item.id] ?? suggestedRole(item.safe_filename)
    const workbook = role === 'homework_workbook'
      ? {
          series: workbookSeriesDrafts[item.id]?.trim() || item.display_name,
          volume: workbookVolumeDrafts[item.id] ?? 'A' as const,
        }
      : undefined
    await catalog.attachSemesterMaterial(item, role, workbook)
    await openMaterial(item)
    notice(`已把“${item.display_name}”作为${roleLabel(role)}加入本学期并选中。`)
  } catch {
    notice(catalog.errorMessage || '资料未能加入本学期。')
  } finally {
    attachingMaterialId.value = null
  }
}

async function updateExistingRole(item: MaterialVersion): Promise<void> {
  const record = semesterRecordFor(item)
  if (!record) return
  const role = existingRoleDrafts[item.id] ?? record.material_role
  await catalog.updateSemesterMaterial(record, {
    materialRole: role,
    workbookSeries: role === 'homework_workbook'
      ? workbookSeriesDrafts[item.id]?.trim() || record.workbook_series || item.display_name
      : null,
    workbookVolume: role === 'homework_workbook'
      ? workbookVolumeDrafts[item.id] ?? record.workbook_volume ?? 'A'
      : null,
  })
  notice('资料角色已更新。')
}

async function removeFromSemester(item: MaterialVersion): Promise<void> {
  const record = semesterRecordFor(item)
  if (!record) return
  if (!window.confirm('移出后本学期课时将无法引用该资料，原件保留，可随时加回。确定移出吗？')) return
  await catalog.updateSemesterMaterial(record, { isActive: false })
  notice('已移出本学期，历史课时和版本记录仍保留。')
}

async function restoreToSemester(item: MaterialVersion): Promise<void> {
  const record = semesterRecordFor(item)
  if (!record) return
  await catalog.updateSemesterMaterial(record, { isActive: true })
  notice('已恢复到本学期。')
}

async function renameMaterial(item: MaterialVersion): Promise<void> {
  const name = window.prompt('资料名称', item.display_name)?.trim()
  if (!name || name === item.display_name) return
  await catalog.updateMaterialSource(item, { displayName: name })
  notice('资料名称已更新。')
}

/* ===== 彻底删除：影响预览 + 请求号幂等核对（旧逻辑平移） ===== */

type MaterialDeletionState = 'ready' | 'deleting' | 'checking' | 'succeeded' | 'failed' | 'unknown' | 'blocked'
interface MaterialDeletionPlanItem {
  material: MaterialVersion
  preview: MaterialDeletionPreview
  operationId: string
  state: MaterialDeletionState
  result: DeleteMaterialSourceResult | null
  detail: string
}
const deletionPlan = ref<MaterialDeletionPlanItem[]>([])
const deletionPlanMode = ref<'single' | 'batch'>('single')

function deletionOperationId(): string {
  return `delete-material-${crypto.randomUUID().replaceAll('-', '')}`
}

function deletionTermLabel(term: 'first' | 'second'): string {
  return term === 'first' ? '上学期' : '下学期'
}

function deletionImpactSummary(preview: MaterialDeletionPreview): string {
  return `将删除 ${preview.impact_counts.material_versions} 个资料版本、${preview.impact_counts.material_units} 页解析内容、${preview.impact_counts.lesson_material_links} 条课时关联。`
}

function deletionSnapshotDisclosure(previews: MaterialDeletionPreview[]): string {
  const count = previews.reduce(
    (total, preview) => total + preview.preserved_snapshot_count,
    0,
  )
  return count > 0
    ? `会保留 ${count} 份生成事实快照，仅含资料名、安全文件名、类型、版本标识和内容指纹等生成事实；不保留原文件、解析正文或本机路径。`
    : '本次不会保留生成事实快照。'
}

async function prepareMaterialDeletionPlan(
  candidates: MaterialVersion[],
  mode: 'single' | 'batch',
): Promise<void> {
  if (batchRunning.value || candidates.length === 0) return
  batchRunning.value = true
  deletionPlanMode.value = mode
  deletionPlan.value = []
  notice(mode === 'single' ? '正在核对完整删除影响……' : `正在核对 ${candidates.length} 份资料的删除影响……`)
  try {
    const prepared: MaterialDeletionPlanItem[] = []
    for (const material of candidates) {
      try {
        const preview = await catalog.getMaterialDeletionPreview(material)
        prepared.push({
          material,
          preview,
          operationId: deletionOperationId(),
          state: preview.can_delete ? 'ready' : 'blocked',
          result: null,
          detail: preview.can_delete
            ? '等待教师最终确认'
            : preview.blocking_generation_count > 0
              ? `仍有 ${preview.blocking_generation_count} 个课件生成进行中或等待恢复；请先完成、取消或放弃恢复。`
              : preview.preserved_history_note ?? '当前资料暂时不能彻底删除。',
        })
      } catch {
        notice(catalog.errorMessage || `无法核对“${material.display_name}”的删除影响。`)
      }
    }
    deletionPlan.value = prepared
    const blocked = prepared.filter(item => item.state === 'blocked').length
    const ready = prepared.length - blocked
    notice(prepared.length === 0
      ? '删除影响没有载入，未执行任何删除。'
      : blocked > 0
        ? `影响已载入：${ready} 份可删除，${blocked} 份因课件生成进行中或等待恢复而暂时阻止。`
        : '完整影响已载入；核对后再做一次最终确认。')
  } finally {
    batchRunning.value = false
  }
}

function applyDeletionResult(
  plan: MaterialDeletionPlanItem,
  result: DeleteMaterialSourceResult,
): void {
  plan.result = result
  if (result.status === 'succeeded') {
    plan.state = 'succeeded'
    plan.detail = `请求已确认成功，已删除 ${result.deleted_file_count} 个受控文件。`
  } else if (result.status === 'failed') {
    plan.state = 'failed'
    plan.detail = result.error_code
      ? `删除未完成（${result.error_code}），不会自动重试。`
      : '删除未完成，不会自动重试。'
  } else {
    plan.state = 'unknown'
    plan.detail = '结果尚未完成；请使用同一请求号继续核对，不要重新发起删除。'
  }
}

async function replayMaterialDeletion(plan: MaterialDeletionPlanItem): Promise<void> {
  plan.state = 'deleting'
  plan.detail = '正在用同一请求号幂等续作，不会生成新的删除请求。'
  try {
    const replayed = await catalog.deleteMaterialSource(plan.material, {
      operation_id: plan.operationId,
      preview_version: plan.preview.preview_version,
      confirmation_phrase: plan.preview.confirmation_phrase,
    })
    applyDeletionResult(plan, replayed)
  } catch {
    plan.state = 'unknown'
    plan.detail = '网络结果仍不明；请求号已保留，可继续按同一编号核对或幂等重放。'
  }
}

async function checkMaterialDeletion(
  plan: MaterialDeletionPlanItem,
  replayInterrupted = false,
): Promise<void> {
  plan.state = 'checking'
  plan.detail = '正在按同一请求号核对结果……'
  try {
    const result = await catalog.getMaterialDeletionStatus(
      plan.operationId,
      plan.material,
    )
    if (result.status === 'interrupted' && replayInterrupted) {
      await replayMaterialDeletion(plan)
      return
    }
    applyDeletionResult(plan, result)
  } catch {
    await replayMaterialDeletion(plan)
  }
}

async function executeMaterialDeletion(plan: MaterialDeletionPlanItem): Promise<void> {
  if (plan.state !== 'ready') return
  plan.state = 'deleting'
  plan.detail = '删除请求已提交，正在等待明确结果……'
  try {
    const result = await catalog.deleteMaterialSource(plan.material, {
      operation_id: plan.operationId,
      preview_version: plan.preview.preview_version,
      confirmation_phrase: plan.preview.confirmation_phrase,
    })
    applyDeletionResult(plan, result)
  } catch {
    await checkMaterialDeletion(plan)
  }
}

async function confirmMaterialDeletionPlan(): Promise<void> {
  const ready = deletionPlan.value.filter(item => item.state === 'ready')
  if (ready.length === 0) {
    notice('当前没有可执行的删除项；被阻止的资料仍完整保留。')
    return
  }
  const promptTitle = deletionPlanMode.value === 'single'
    ? `确认彻底删除“${ready[0]!.material.display_name}”吗？已展示的原文件副本、解析内容和关联将永久删除。`
    : `确认彻底删除已核对的 ${ready.length} 份资料吗？每份结果和请求号都会保留在本页。`
  const prompt = [
    promptTitle,
    deletionSnapshotDisclosure(ready.map(item => item.preview)),
    '原文件、解析页、课时和学期关联将永久删除且无法恢复。',
  ].join('\n\n')
  if (!window.confirm(prompt)) return
  batchRunning.value = true
  try {
    for (const plan of ready) await executeMaterialDeletion(plan)
  } finally {
    batchRunning.value = false
  }
  const succeeded = deletionPlan.value.filter(item => item.state === 'succeeded').length
  const unresolved = deletionPlan.value.filter(item => !['succeeded', 'blocked'].includes(item.state)).length
  notice(unresolved > 0
    ? `已确认成功 ${succeeded} 份，${unresolved} 份未得到明确结果；请按各自请求号核对。`
    : `请求已确认成功 ${succeeded} 份；被阻止的资料未发生变化。`)
}

function closeMaterialDeletionPlan(): void {
  if (deletionPlan.value.some(item => ['deleting', 'checking'].includes(item.state))) return
  deletionPlan.value = []
}

const deletionStateLabels: Record<MaterialDeletionState, string> = {
  ready: '等待确认',
  blocked: '禁止删除',
  succeeded: '已确认成功',
  failed: '已失败',
  unknown: '结果待核对',
  deleting: '正在删除',
  checking: '正在核对',
}
</script>

<template>
  <section class="tp-panel" aria-label="本学期资料">
    <div class="tp-panel__head">
      <h2>② 本学期资料</h2>
      <span class="tp-panel__hint">悬停每一行可改名、调整角色、移出或删除</span>
    </div>
    <table v-if="currentTermMaterials.length" class="tp-grid">
      <thead>
        <tr><th class="tp-grid__main">资料</th><th>角色</th><th>页数</th><th>状态</th><th class="tp-grid__actions">操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in currentTermMaterials" :key="item.id">
          <td>
            <div class="tp-cell-main">{{ item.display_name }}</div>
            <div class="tp-cell-sub">
              {{ item.material_type.toUpperCase() }}
              <template v-if="parseJobForMaterial(item) || item.parse_expected_unit_count">
                · {{ materialProgressLabel(item) }}
              </template>
            </div>
          </td>
          <td>
            <select
              aria-label="资料角色"
              :value="existingRoleDrafts[item.id] ?? semesterRecordFor(item)?.material_role"
              @change="existingRoleDrafts[item.id] = ($event.target as HTMLSelectElement).value as SemesterMaterialRole"
            >
              <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">{{ role.label }}</option>
            </select>
            <template v-if="(existingRoleDrafts[item.id] ?? semesterRecordFor(item)?.material_role) === 'homework_workbook'">
              <input v-model="workbookSeriesDrafts[item.id]" type="text" :placeholder="semesterRecordFor(item)?.workbook_series ?? '教辅套组名称'">
              <select v-model="workbookVolumeDrafts[item.id]" aria-label="分册">
                <option value="A">A 本</option><option value="B">B 本</option>
              </select>
            </template>
          </td>
          <td>{{ item.unit_count ?? 0 }}</td>
          <td>
            <StatusBadge
              :tone="materialStatusLabel(item).tone"
              :label="materialStatusLabel(item).label"
            />
          </td>
          <td>
            <div class="tp-row-actions">
              <AppButton v-if="materialNeedsContinue(item)" variant="ghost" @click="continueMaterial(item)">继续解析</AppButton>
              <AppButton variant="ghost" @click="openMaterial(item)">选中</AppButton>
              <AppButton variant="ghost" @click="renameMaterial(item)">重命名</AppButton>
              <AppButton variant="ghost" @click="updateExistingRole(item)">保存角色</AppButton>
              <AppButton v-if="semesterRecordFor(item)?.is_active" variant="ghost" @click="removeFromSemester(item)">移出学期</AppButton>
              <AppButton v-else variant="ghost" @click="restoreToSemester(item)">恢复</AppButton>
              <AppButton variant="ghost" class="tp-danger-text" @click="prepareMaterialDeletionPlan([item], 'single')">删除</AppButton>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="tp-panel__body">
      <p class="tp-muted">本学期还没有资料。先在上方导入，或在下方把其他资料加入本学期。</p>
    </div>
    <div class="tp-panel__foot">
      <AppButton
        v-if="currentTermMaterials.length"
        variant="ghost"
        class="tp-danger-text"
        :disabled="batchRunning"
        @click="prepareMaterialDeletionPlan([...currentTermMaterials], 'batch')"
      >
        {{ batchRunning ? '正在核对删除影响…' : `删除本学期全部资料（${currentTermMaterials.length}）` }}
      </AppButton>
      <AppButton
        v-if="otherTermMaterials.length"
        variant="ghost"
        :aria-expanded="showOtherTermMaterials"
        @click="showOtherTermMaterials = !showOtherTermMaterials"
      >
        {{ showOtherTermMaterials ? '收起其他学期或未归类资料' : `展开其他学期或未归类资料（${otherTermMaterials.length}）` }}
      </AppButton>
    </div>

    <table v-if="showOtherTermMaterials && otherTermMaterials.length" class="tp-grid">
      <thead>
        <tr><th class="tp-grid__main">未归类资料</th><th>加入角色</th><th class="tp-grid__actions">操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in otherTermMaterials" :key="item.id">
          <td>
            <div class="tp-cell-main">{{ item.display_name }}</div>
            <div class="tp-cell-sub">{{ item.material_type.toUpperCase() }} · {{ item.unit_count ?? 0 }} 页/张</div>
          </td>
          <td>
            <select v-model="existingRoleDrafts[item.id]" aria-label="选择资料角色">
              <option :value="undefined">选择资料角色</option>
              <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">{{ role.label }}</option>
            </select>
            <template v-if="existingRoleDrafts[item.id] === 'homework_workbook'">
              <input v-model="workbookSeriesDrafts[item.id]" type="text" placeholder="教辅套组名称">
              <select v-model="workbookVolumeDrafts[item.id]" aria-label="分册">
                <option value="A">A 本</option><option value="B">B 本</option>
              </select>
            </template>
          </td>
          <td>
            <div class="tp-row-actions">
              <AppButton
                variant="ghost"
                :disabled="!existingRoleDrafts[item.id] || attachingMaterialId === item.id || !catalog.selectedSemester"
                @click="attachExistingMaterial(item)"
              >
                {{ attachingMaterialId === item.id ? '正在加入…' : '加入本学期并选中' }}
              </AppButton>
              <AppButton variant="ghost" @click="renameMaterial(item)">重命名</AppButton>
              <AppButton variant="ghost" class="tp-danger-text" @click="prepareMaterialDeletionPlan([item], 'single')">删除</AppButton>
            </div>
          </td>
        </tr>
      </tbody>
    </table>

    <section v-if="deletionPlan.length" class="tp-deletion-preview" aria-labelledby="material-deletion-title">
      <div class="tp-panel__head">
        <h2 id="material-deletion-title">
          {{ deletionPlanMode === 'single' ? '彻底删除：先核对这一份资料' : `彻底删除：逐项核对 ${deletionPlan.length} 份资料` }}
        </h2>
        <AppButton variant="ghost" :disabled="batchRunning" @click="closeMaterialDeletionPlan">关闭预览</AppButton>
      </div>
      <div class="tp-panel__body">
        <p class="tp-muted">此处仅展示影响。只有一次最终确认后才会提交删除；网络结果不明时只按原请求号查询。</p>
        <article
          v-for="plan in deletionPlan"
          :key="plan.operationId"
          class="tp-deletion-preview__item"
          :class="`is-${plan.state}`"
        >
          <header>
            <div>
              <strong>{{ plan.preview.display_name }}</strong>
              <small>请求号：<span data-testid="deletion-operation-id">{{ plan.operationId }}</span></small>
            </div>
            <StatusBadge
              :tone="plan.state === 'succeeded' ? 'success' : plan.state === 'blocked' || plan.state === 'failed' ? 'danger' : plan.state === 'unknown' ? 'warning' : 'info'"
              :label="deletionStateLabels[plan.state]"
            />
          </header>
          <p>{{ deletionImpactSummary(plan.preview) }}</p>
          <dl class="tp-deletion-impact-grid">
            <div><dt>资料源</dt><dd>{{ plan.preview.impact_counts.material_sources }}</dd></div>
            <div><dt>资料版本</dt><dd>{{ plan.preview.impact_counts.material_versions }}</dd></div>
            <div><dt>解析页/张</dt><dd>{{ plan.preview.impact_counts.material_units }}</dd></div>
            <div><dt>课时关联</dt><dd>{{ plan.preview.impact_counts.lesson_material_links }}</dd></div>
            <div><dt>学期资料记录</dt><dd>{{ plan.preview.impact_counts.semester_material_records }}</dd></div>
            <div><dt>目录建议</dt><dd>{{ plan.preview.impact_counts.semester_mapping_proposals }}</dd></div>
            <div><dt>参考 PPT 合集</dt><dd>{{ plan.preview.impact_counts.reference_ppt_collections }}</dd></div>
            <div><dt>习题区域</dt><dd>{{ plan.preview.impact_counts.exercise_regions }}</dd></div>
            <div><dt>习题候选</dt><dd>{{ plan.preview.impact_counts.exercise_candidates }}</dd></div>
            <div><dt>受控文件</dt><dd>{{ plan.preview.owned_file_count }}</dd></div>
            <div><dt>正式生成历史</dt><dd>{{ plan.preview.generation_history_count }}</dd></div>
            <div><dt>保留事实快照</dt><dd>{{ plan.preview.preserved_snapshot_count }}</dd></div>
            <div><dt>阻断中的生成</dt><dd>{{ plan.preview.blocking_generation_count }}</dd></div>
          </dl>
          <div v-if="plan.preview.affected_semesters.length" class="tp-deletion-semesters">
            <strong>受影响学期</strong>
            <span v-for="semesterItem in plan.preview.affected_semesters" :key="semesterItem.semester_id">
              {{ semesterItem.title }} · {{ semesterItem.school_year }} · {{ deletionTermLabel(semesterItem.term) }}
            </span>
          </div>
          <p v-if="plan.preview.preserved_history_note" class="tp-inline-message">{{ plan.preview.preserved_history_note }}</p>
          <p v-if="plan.preview.blocking_generation_count > 0" class="tp-error-text">
            仍有 {{ plan.preview.blocking_generation_count }} 个课件生成进行中或等待恢复；请先完成、取消或放弃恢复，再重新核对删除影响。
          </p>
          <p v-else-if="plan.preview.blocker_code" class="tp-error-text">
            当前资料暂时不能删除（{{ plan.preview.blocker_code }}），请按上方说明处理后重新核对。
          </p>
          <p role="status" :class="{ 'tp-error-text': ['failed', 'unknown', 'blocked'].includes(plan.state) }">{{ plan.detail }}</p>
          <AppButton
            v-if="plan.state === 'unknown' || plan.state === 'failed'"
            variant="secondary"
            @click="checkMaterialDeletion(plan, true)"
          >
            按此请求号核对 / 幂等重放
          </AppButton>
        </article>
        <div class="tp-inline-actions">
          <AppButton variant="secondary" :disabled="batchRunning" @click="closeMaterialDeletionPlan">取消，不删除</AppButton>
          <AppButton
            variant="danger"
            data-testid="confirm-material-deletion"
            :disabled="batchRunning"
            @click="confirmMaterialDeletionPlan"
          >
            {{ batchRunning ? '正在核对删除结果…' : '确认彻底删除可删除项' }}
          </AppButton>
        </div>
      </div>
    </section>
  </section>
</template>
