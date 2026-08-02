<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import type { JobResponse } from '../../../api/jobs'
import type {
  MaterialLinkPurpose,
  MaterialVersion,
  SemesterMappingProposalRange,
  SemesterMaterialRecord,
  SemesterMaterialRole,
} from '../api/catalog'
import { teachingPrepWorkbenchApi } from '../api/workbench'
import TeachingPrepDocumentWorkspace from '../components/TeachingPrepDocumentWorkspace.vue'
import TeachingPrepStickyActions from '../components/TeachingPrepStickyActions.vue'
import { useTeachingPrepWorkbenchContext } from '../workbench/context'

const workbench = useTeachingPrepWorkbenchContext()
const activeUnitId = ref<string | null>(null)
const selectedSemesterMaterialId = ref<string | null>(null)
const importBatchRunning = ref(false)
const attachingMaterialId = ref<string | null>(null)
const existingRoleDrafts = reactive<Record<string, SemesterMaterialRole>>({})
const mappingEdits = reactive<Record<string, {
  lessonRef: string
  startUnit: number
  endUnit: number
  reason: string
}>>({})
const manualMapping = reactive<{
  lessonId: string
  startUnit: number
  endUnit: number
  purpose: MaterialLinkPurpose
  note: string
}>({
  lessonId: '',
  startUnit: 1,
  endUnit: 1,
  purpose: 'supplement',
  note: '',
})
const mappingMessage = ref('先导入并解析资料，再逐份建立课时目录。')

const MATERIAL_ROLES: Array<{ value: SemesterMaterialRole; label: string }> = [
  { value: 'textbook', label: '教材' },
  { value: 'reference_ppt', label: '参考课件 / 备选 PPT' },
  { value: 'exercise_workbook', label: '普通教辅' },
  { value: 'homework_workbook', label: '日常作业教辅' },
  { value: 'answer_book', label: '答案册' },
  { value: 'supplement', label: '补充资料' },
]

const LINK_PURPOSES: Array<{ value: MaterialLinkPurpose; label: string }> = [
  { value: 'textbook', label: '教材依据' },
  { value: 'reference_ppt', label: '参考课件' },
  { value: 'exercise', label: '课堂练习' },
  { value: 'answer', label: '答案 / 解析' },
  { value: 'supplement', label: '补充资料' },
]

type PendingImportState =
  | 'pending'
  | 'uploading'
  | 'processing'
  | 'done'
  | 'failed'
  | 'cancelled'

interface PendingMaterialImport {
  id: string
  file: File
  role: SemesterMaterialRole
  state: PendingImportState
  materialId: string | null
  error: string
}

const pendingImports = ref<PendingMaterialImport[]>([])

const activeUnit = computed(
  () => workbench.catalog.materialUnits.find(item => item.id === activeUnitId.value)
    ?? workbench.catalog.materialUnits[0]
    ?? null,
)
const activeMaterial = computed(
  () => workbench.catalog.materials.find(
    item => item.id === workbench.catalog.selectedMaterialId,
  ) ?? null,
)
const activeProposal = computed(
  () => workbench.catalog.semesterMappingProposals.find(
    item => item.status === 'proposed',
  ) ?? null,
)
const allMappingsDecided = computed(() => {
  const mappings = activeProposal.value?.payload.mappings ?? []
  return mappings.length > 0
    && mappings.every(item => item.decision !== 'pending')
})
const eligibleSemesterMaterials = computed(
  () => workbench.catalog.semesterMaterials.filter(item => (
    item.is_active
    && item.parse_status === 'parsed'
    && !item.has_unparsed_update
    && (item.current_unit_count ?? 0) > 0
  )),
)
const activeSemesterRecord = computed(() => (
  activeMaterial.value ? semesterRecordFor(activeMaterial.value) : null
))
const activeMaterialReadyForMapping = computed(() => (
  activeMaterial.value !== null
  && activeSemesterRecord.value?.parse_status === 'parsed'
  && !activeSemesterRecord.value.has_unparsed_update
  && ['ready', 'scanned_no_text'].includes(
    activeMaterial.value.inspection_status,
  )
))
const pendingImportCount = computed(
  () => pendingImports.value.filter(item => (
    item.state === 'pending' || item.state === 'failed'
    || item.state === 'cancelled'
  )).length,
)

watch(
  () => workbench.catalog.materialUnits,
  units => {
    if (!units.some(item => item.id === activeUnitId.value)) {
      activeUnitId.value = units[0]?.id ?? null
    }
    if (units.length > 0) {
      const maximum = units.at(-1)?.unit_index ?? 1
      manualMapping.startUnit = Math.min(manualMapping.startUnit, maximum)
      manualMapping.endUnit = Math.min(
        Math.max(manualMapping.endUnit, manualMapping.startUnit),
        maximum,
      )
    }
  },
  { immediate: true },
)

watch(
  () => eligibleSemesterMaterials.value.map(item => item.id),
  ids => {
    if (!ids.includes(selectedSemesterMaterialId.value ?? '')) {
      selectedSemesterMaterialId.value = ids[0] ?? null
    }
  },
  { immediate: true },
)

watch(
  () => workbench.catalog.lessonNodes,
  nodes => {
    const lessons = nodes.filter(item => item.node_type === 'lesson' && item.is_active)
    if (!lessons.some(item => item.id === manualMapping.lessonId)) {
      manualMapping.lessonId = workbench.catalog.selectedLessonId
        ?? lessons[0]?.id
        ?? ''
    }
  },
  { immediate: true },
)

function semesterRecordFor(material: MaterialVersion): SemesterMaterialRecord | null {
  return workbench.catalog.semesterMaterials.find(item => (
    item.material_source_id === material.source_id
    || item.current_material_version_id === material.id
  )) ?? null
}

function roleLabel(role: SemesterMaterialRole): string {
  return MATERIAL_ROLES.find(item => item.value === role)?.label ?? role
}

function rolePurpose(role: SemesterMaterialRole): MaterialLinkPurpose {
  return {
    textbook: 'textbook',
    reference_ppt: 'reference_ppt',
    exercise_workbook: 'exercise',
    homework_workbook: 'exercise',
    answer_book: 'answer',
    supplement: 'supplement',
  }[role] as MaterialLinkPurpose
}

function suggestedRole(name: string): SemesterMaterialRole {
  return name.toLowerCase().endsWith('.pptx')
    ? 'reference_ppt'
    : 'supplement'
}

function fileSizeLabel(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(size / 1024))} KB`
}

function importStateLabel(item: PendingMaterialImport): string {
  const job = parseJobFor(item)
  if (item.state === 'processing' && job) {
    if (job.status === 'queued') return '等待解析'
    if (job.cancel_requested) return '正在安全停止'
    return job.detail || '正在逐页处理'
  }
  return {
    pending: '等待导入',
    uploading: '正在复制到本机资料库',
    processing: '正在逐页处理',
    done: '已完成',
    failed: '未完成，可重试',
    cancelled: '已停止，可继续',
  }[item.state]
}

function parseJobFor(item: PendingMaterialImport): JobResponse | null {
  return item.materialId
    ? workbench.catalog.materialParseJobs[item.materialId] ?? null
    : null
}

function progressPercent(item: PendingMaterialImport): number {
  if (item.state === 'done') return 100
  const job = parseJobFor(item)
  if (job) return Math.max(0, Math.min(100, Math.round(job.progress * 100)))
  return item.state === 'uploading' ? 2 : 0
}

function parseJobForMaterial(material: MaterialVersion): JobResponse | null {
  return workbench.catalog.materialParseJobs[material.id] ?? null
}

function materialProgressLabel(material: MaterialVersion): string {
  const job = parseJobForMaterial(material)
  if (!job) return ''
  if (job.status === 'succeeded') return '逐页处理完成'
  if (job.status === 'failed') return '处理未完成，可重新打开后继续'
  if (job.status === 'cancelled') return '已停止，可重新打开后继续'
  return job.detail || (job.status === 'queued' ? '等待后台处理' : '正在逐页处理')
}

function failureMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message
  return workbench.catalog.errorMessage || '资料处理没有完成，可保留进度后重试。'
}

async function openMaterial(item: MaterialVersion): Promise<void> {
  mappingMessage.value = (item.unit_count ?? 0) > 0
    ? '正在打开已保存的页级目录…'
    : '正在本机解析资料并生成逐页预览，大文件需要一些时间…'
  try {
    await workbench.catalog.openMaterial(item)
    activeUnitId.value = workbench.catalog.materialUnits[0]?.id ?? null
    const record = semesterRecordFor(item)
    if (record) manualMapping.purpose = rolePurpose(record.material_role)
    mappingMessage.value = workbench.catalog.materialUnits.length > 0
      ? `已载入 ${workbench.catalog.materialUnits.length} 页/张，可核对原页并建立课时关联。`
      : '没有取得可预览页面，请查看上方错误后重试。'
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage
      || '资料没有完成解析，请保留原文件后重试。'
  }
}

function queueFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  for (const file of files) {
    pendingImports.value.push({
      id: globalThis.crypto.randomUUID(),
      file,
      role: suggestedRole(file.name),
      state: 'pending',
      materialId: null,
      error: '',
    })
  }
  if (files.length > 0) {
    mappingMessage.value = `已加入 ${files.length} 个文件，请逐份确认资料角色后开始导入。`
  }
}

function removePendingImport(id: string): void {
  pendingImports.value = pendingImports.value.filter(item => item.id !== id)
}

function clearFinishedImports(): void {
  pendingImports.value = pendingImports.value.filter(item => item.state !== 'done')
}

async function importQueuedFiles(): Promise<void> {
  if (importBatchRunning.value || pendingImportCount.value === 0) return
  importBatchRunning.value = true
  let completed = 0
  let failed = 0
  const parseTasks: Promise<void>[] = []
  try {
    for (const item of pendingImports.value) {
      if (
        item.state !== 'pending'
        && item.state !== 'failed'
        && item.state !== 'cancelled'
      ) continue
      item.state = 'uploading'
      item.error = ''
      mappingMessage.value = `正在复制“${item.file.name}”，复制后会在后台逐页处理…`
      try {
        const material = item.materialId
          ? workbench.catalog.materials.find(value => value.id === item.materialId)
          : await workbench.catalog.importMaterialCopy(item.file, item.role)
        if (!material) throw new Error('没有找到可继续处理的资料副本。')
        item.materialId = material.id
        item.state = 'processing'
        parseTasks.push(
          workbench.catalog.parseMaterialInBackground(material)
            .then(() => {
              item.state = 'done'
              completed += 1
            })
            .catch((error: unknown) => {
              const job = parseJobFor(item)
              item.state = job?.status === 'cancelled' ? 'cancelled' : 'failed'
              item.error = failureMessage(error)
              failed += 1
            }),
        )
      } catch (error) {
        item.state = 'failed'
        item.error = failureMessage(error)
        failed += 1
      }
    }
    importBatchRunning.value = false
    await Promise.allSettled(parseTasks)
    await workbench.refreshCurrentWorkspace()
    mappingMessage.value = failed > 0
      ? `已完成 ${completed} 份，${failed} 份未完成；其余资料已保留，可单独重试失败项。`
      : `已完成 ${completed} 份资料的受控复制、角色登记和页级解析。`
  } finally {
    importBatchRunning.value = false
  }
}

async function cancelImport(item: PendingMaterialImport): Promise<void> {
  if (!item.materialId || item.state !== 'processing') return
  try {
    await workbench.catalog.cancelMaterialParse(item.materialId)
    mappingMessage.value = `正在安全停止“${item.file.name}”；已完成的预览会保留。`
  } catch (error) {
    item.error = failureMessage(error)
  }
}

async function attachExistingMaterial(item: MaterialVersion): Promise<void> {
  if (semesterRecordFor(item) || !workbench.catalog.selectedSemester) return
  attachingMaterialId.value = item.id
  try {
    const role = existingRoleDrafts[item.id] ?? suggestedRole(item.safe_filename)
    await workbench.catalog.attachSemesterMaterial(item, role)
    manualMapping.purpose = rolePurpose(role)
    mappingMessage.value = `已把“${item.display_name}”作为${roleLabel(role)}加入本学期。`
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage || '资料未能加入本学期。'
  } finally {
    attachingMaterialId.value = null
  }
}

async function prepareSemesterMapping(): Promise<void> {
  if (!selectedSemesterMaterialId.value) return
  mappingMessage.value = '正在检查本次目录整理范围，不会自动调用模型…'
  try {
    await workbench.catalog.prepareSemesterMapping([
      selectedSemesterMaterialId.value,
    ])
    mappingMessage.value = '发送范围已准备好；确认后可生成一份待审核的课时目录建议。'
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage || '发送范围检查未完成。'
  }
}

async function generateSemesterMapping(): Promise<void> {
  if (!selectedSemesterMaterialId.value) return
  mappingMessage.value = '正在生成课时—页段建议；本次不会自动追加请求…'
  try {
    await workbench.catalog.generateSemesterMapping([
      selectedSemesterMaterialId.value,
    ])
    mappingMessage.value = '目录建议已生成，请逐条接受、修改或拒绝。'
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage || '目录建议没有完成。'
  }
}

function editFor(item: SemesterMappingProposalRange) {
  const teacher = item.teacher_revision
  return mappingEdits[item.mapping_id] ??= {
    lessonRef: teacher?.lesson_ref ?? item.lesson_ref,
    startUnit: teacher?.start_unit ?? item.start_unit,
    endUnit: teacher?.end_unit ?? item.end_unit,
    reason: item.decision_reason ?? '',
  }
}

async function decideMapping(
  item: SemesterMappingProposalRange,
  decision: 'accepted' | 'modified' | 'rejected',
): Promise<void> {
  const proposal = activeProposal.value
  if (!proposal) return
  const edit = editFor(item)
  mappingMessage.value = '正在保存本条决定…'
  try {
    const updated = await teachingPrepWorkbenchApi.reviewMapping(
      proposal.id,
      item.mapping_id,
      {
        expected_revision: proposal.revision,
        decision,
        ...(decision === 'accepted'
          ? {
              lesson_ref: item.lesson_ref,
              start_unit: item.start_unit,
              end_unit: item.end_unit,
            }
          : decision === 'modified'
            ? {
                lesson_ref: edit.lessonRef,
                start_unit: edit.startUnit,
                end_unit: edit.endUnit,
              }
            : {}),
        reason: edit.reason || null,
      },
    )
    const index = workbench.catalog.semesterMappingProposals.findIndex(
      proposalItem => proposalItem.id === updated.id,
    )
    if (index >= 0) workbench.catalog.semesterMappingProposals[index] = updated
    mappingMessage.value = decision === 'rejected'
      ? '已拒绝本条建议，原始建议仍保留。'
      : decision === 'modified'
        ? '已保存教师修改，原始建议仍可追溯。'
        : '已接受本条建议。'
  } catch {
    mappingMessage.value = '本条建议没有保存，请刷新后核对版本。'
  }
}

async function applyProposal(): Promise<void> {
  if (!activeProposal.value || !allMappingsDecided.value) return
  try {
    await workbench.catalog.applySemesterMapping(activeProposal.value)
    await workbench.refreshCurrentWorkspace()
    mappingMessage.value = '全部接受项已在一个事务中写入正式映射。'
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage || '正式映射没有写入。'
  }
}

async function saveManualMapping(): Promise<void> {
  const material = activeMaterial.value
  const record = activeSemesterRecord.value
  const lesson = workbench.catalog.lessonNodes.find(
    item => item.id === manualMapping.lessonId && item.node_type === 'lesson',
  )
  const maximum = workbench.catalog.materialUnits.at(-1)?.unit_index ?? 0
  if (
    !material
    || !record
    || !activeMaterialReadyForMapping.value
    || !lesson
    || maximum === 0
  ) {
    mappingMessage.value = '请先打开已加入本学期且解析完成的资料，并选择现有课时。'
    return
  }
  if (
    manualMapping.startUnit < 1
    || manualMapping.endUnit < manualMapping.startUnit
    || manualMapping.endUnit > maximum
  ) {
    mappingMessage.value = `页段应在 1—${maximum} 之间，且结束页不能早于起始页。`
    return
  }
  mappingMessage.value = '正在保存人工课时页段关联…'
  try {
    if (workbench.catalog.selectedLessonId !== lesson.id) {
      await workbench.openLesson(lesson.id)
    }
    await workbench.catalog.confirmMaterialRanges(
      [{ start: manualMapping.startUnit, end: manualMapping.endUnit }],
      manualMapping.purpose,
      manualMapping.note.trim() || null,
    )
    await workbench.refreshCurrentWorkspace()
    mappingMessage.value = `已把第 ${manualMapping.startUnit}—${manualMapping.endUnit} 页/张关联到“${lesson.title}”。`
    manualMapping.note = ''
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage || '人工关联没有保存。'
  }
}
</script>

<template>
  <section class="tp-workspace tp-material-workspace">
    <header class="tp-workspace__header">
      <div>
        <p class="tp-eyebrow">资料库</p>
        <h1 data-workbench-title tabindex="-1">建立可复用的学期资料目录</h1>
        <p>逐份确认角色并在本机解析成页级索引；以后备课只引用当前课时需要的几页。</p>
      </div>
      <label
        class="tp-button tp-button--secondary tp-file-button"
        :class="{ 'is-disabled': importBatchRunning }"
      >
        选择多份资料
        <input
          type="file"
          multiple
          accept=".pdf,.pptx,.png,.jpg,.jpeg,.webp"
          :disabled="importBatchRunning"
          @change="queueFiles"
        >
      </label>
    </header>

    <div
      v-if="workbench.catalog.errorMessage"
      class="tp-global-notice"
      role="alert"
    >
      <strong>资料处理没有完成</strong>
      <span>{{ workbench.catalog.errorMessage }}</span>
    </div>

    <section v-if="pendingImports.length" class="tp-section-block tp-import-queue">
      <div class="tp-section-heading">
        <div>
          <h2>待导入资料</h2>
          <p>先逐份复制，再由后台并行处理；每一页的预览和文字识别进度都会保留。</p>
        </div>
        <div class="tp-inline-actions">
          <button
            type="button"
            :disabled="importBatchRunning || !pendingImports.some(item => item.state === 'done')"
            @click="clearFinishedImports"
          >
            清除已完成
          </button>
          <button
            class="tp-button--primary"
            type="button"
            :disabled="importBatchRunning || pendingImportCount === 0"
            @click="importQueuedFiles"
          >
            {{ importBatchRunning ? '正在复制资料…' : `开始导入 ${pendingImportCount} 份` }}
          </button>
        </div>
      </div>
      <div class="tp-import-queue__list">
        <article
          v-for="item in pendingImports"
          :key="item.id"
          class="tp-import-row"
          :class="`is-${item.state}`"
        >
          <div class="tp-import-row__identity">
            <strong>{{ item.file.name }}</strong>
            <small>{{ fileSizeLabel(item.file.size) }} · {{ importStateLabel(item) }}</small>
            <small v-if="item.error" class="tp-error-text">{{ item.error }}</small>
            <div
              v-if="item.state !== 'pending'"
              class="tp-import-row__progress"
            >
              <progress
                :value="progressPercent(item)"
                max="100"
                :aria-label="`${item.file.name}：${importStateLabel(item)}`"
              />
              <span>{{ progressPercent(item) }}%</span>
            </div>
          </div>
          <label class="tp-field">
            资料角色
            <select
              v-model="item.role"
              :disabled="item.state === 'uploading' || item.state === 'processing' || item.state === 'done'"
            >
              <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">
                {{ role.label }}
              </option>
            </select>
          </label>
          <div class="tp-import-row__actions">
            <button
              v-if="item.state === 'processing'"
              type="button"
              :disabled="parseJobFor(item)?.cancel_requested"
              @click="cancelImport(item)"
            >
              {{ parseJobFor(item)?.cancel_requested ? '正在停止…' : '停止' }}
            </button>
            <button
              v-else
              type="button"
              :disabled="item.state === 'uploading'"
              @click="removePendingImport(item.id)"
            >
              移除
            </button>
          </div>
        </article>
      </div>
    </section>

    <section class="tp-section-block tp-directory-actions">
      <div>
        <p class="tp-eyebrow">课时目录整理</p>
        <h2>一次整理一份已解析资料</h2>
        <p>先检查会参考哪些页，再由模型提出待审核建议；模型不会直接修改正式课时树。</p>
      </div>
      <label class="tp-field">
        本次资料
        <select v-model="selectedSemesterMaterialId">
          <option :value="null">请选择已解析资料</option>
          <option
            v-for="record in eligibleSemesterMaterials"
            :key="record.id"
            :value="record.id"
          >
            {{ roleLabel(record.material_role) }} · {{ record.display_name }} · {{ record.current_unit_count }} 页/张
          </option>
        </select>
      </label>
      <div class="tp-inline-actions">
        <button
          type="button"
          :disabled="!selectedSemesterMaterialId || workbench.catalog.loadState === 'loading'"
          @click="prepareSemesterMapping"
        >
          检查发送范围
        </button>
        <button
          class="tp-button--primary"
          type="button"
          :disabled="(
            !selectedSemesterMaterialId
            || !workbench.catalog.semesterMappingPreflight?.model_available
            || workbench.catalog.saveState === 'saving'
          )"
          @click="generateSemesterMapping"
        >
          生成待确认建议
        </button>
      </div>
      <div v-if="workbench.catalog.semesterMappingPreflight" class="tp-inline-guidance">
        <strong>
          本次 {{ workbench.catalog.semesterMappingPreflight.material_count }} 份资料，
          {{ workbench.catalog.semesterMappingPreflight.unit_count }} 页/张
        </strong>
        <span>
          {{
            workbench.catalog.semesterMappingPreflight.creates_initial_tree
              ? '将建议初始章—节—课时树'
              : `将映射到现有 ${workbench.catalog.semesterMappingPreflight.existing_lesson_count} 个课时`
          }}
          · 最多一次模型调用 · 不自动重试
        </span>
        <span v-if="!workbench.catalog.semesterMappingPreflight.model_available">
          当前模型不可用；仍可在右侧把页段人工关联到现有课时。
        </span>
      </div>
      <p v-else-if="eligibleSemesterMaterials.length === 0" class="tp-muted">
        暂无已解析的本学期资料。先在上方导入，或把左侧已有资料补充加入本学期。
      </p>
    </section>

    <TeachingPrepDocumentWorkspace
      :title="activeMaterial?.display_name ?? '原页预览'"
      :subtitle="activeUnit ? `第 ${activeUnit.unit_index} 页/张 · ${activeUnit.formula_review_required ? '公式待核对' : '原文可核对'}` : undefined"
      :preview-url="activeUnit?.preview_url"
      :empty-message="workbench.catalog.saveState === 'saving'
        ? '正在复制并解析资料，大文件需要一些时间。'
        : '选择左侧资料后在这里核对原页；若解析失败，上方会显示原因。'"
    >
      <template #rail>
        <h2>资料版本</h2>
        <p v-if="workbench.catalog.materials.length === 0" class="tp-muted">
          尚未导入教材、教辅或课件。
        </p>
        <article
          v-for="item in workbench.catalog.materials"
          :key="item.id"
          class="tp-material-card"
          :class="{ 'is-selected': item.id === workbench.catalog.selectedMaterialId }"
        >
          <button class="tp-rail-item" type="button" @click="openMaterial(item)">
            <strong>{{ item.display_name }}</strong>
            <small>{{ item.material_type.toUpperCase() }} · {{ item.unit_count ?? 0 }} 页/张</small>
            <span>{{ item.availability === 'available' ? '可用' : '需重新定位' }}</span>
            <span v-if="semesterRecordFor(item)" class="tp-source-tag">
              {{ roleLabel(semesterRecordFor(item)!.material_role) }} · 已加入本学期
            </span>
            <span
              v-if="parseJobForMaterial(item)"
              class="tp-material-card__progress"
            >
              <progress
                :value="Math.round((parseJobForMaterial(item)?.progress ?? 0) * 100)"
                max="100"
              />
              <small>{{ materialProgressLabel(item) }}</small>
            </span>
          </button>
          <div v-if="!semesterRecordFor(item) && workbench.catalog.selectedSemester" class="tp-material-card__attach">
            <select v-model="existingRoleDrafts[item.id]">
              <option :value="undefined">选择资料角色</option>
              <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">
                {{ role.label }}
              </option>
            </select>
            <button
              type="button"
              :disabled="!existingRoleDrafts[item.id] || attachingMaterialId === item.id"
              @click="attachExistingMaterial(item)"
            >
              {{ attachingMaterialId === item.id ? '正在加入…' : '加入本学期' }}
            </button>
          </div>
        </article>
      </template>
      <template #toolbar>
        <div class="tp-page-picker" aria-label="原页导航">
          <button
            v-for="unit in workbench.catalog.materialUnits"
            :key="unit.id"
            type="button"
            :class="{ 'is-selected': unit.id === activeUnit?.id }"
            @click="activeUnitId = unit.id"
          >
            {{ unit.unit_index }}
          </button>
        </div>
      </template>
      <template #inspector>
        <h2>逐条映射审核</h2>
        <p v-if="!activeProposal" class="tp-inline-guidance">
          暂无待审核建议。可在上方生成目录建议，或在下方人工关联当前资料页段。
        </p>
        <article
          v-for="item in activeProposal?.payload.mappings ?? []"
          :key="item.mapping_id"
          class="tp-mapping-review"
          :class="`is-${item.decision}`"
        >
          <header>
            <strong>{{ item.start_unit }}—{{ item.end_unit }} 页/张</strong>
            <span>{{ item.decision === 'pending' ? '待处理' : item.decision }}</span>
          </header>
          <label>
            目标课时
            <select v-model="editFor(item).lessonRef">
              <option
                v-for="lesson in workbench.catalog.lessonNodes.filter(node => node.node_type === 'lesson')"
                :key="lesson.id"
                :value="lesson.id"
              >
                {{ lesson.title }}
              </option>
            </select>
          </label>
          <div class="tp-field-pair">
            <label>起始页<input v-model.number="editFor(item).startUnit" type="number" min="1"></label>
            <label>结束页<input v-model.number="editFor(item).endUnit" type="number" min="1"></label>
          </div>
          <label>决定说明<input v-model="editFor(item).reason" type="text"></label>
          <div class="tp-inline-actions">
            <button type="button" @click="decideMapping(item, 'accepted')">接受</button>
            <button type="button" @click="decideMapping(item, 'modified')">保存修改</button>
            <button class="is-danger" type="button" @click="decideMapping(item, 'rejected')">拒绝</button>
          </div>
        </article>

        <section class="tp-manual-mapping">
          <h3>人工关联当前页段</h3>
          <p class="tp-muted">模型不可用时，也可以把当前资料的连续页段直接关联到现有课时。</p>
          <p v-if="!activeSemesterRecord" class="tp-error-text">
            当前资料尚未加入本学期，请先在左侧选择角色并加入。
          </p>
          <p
            v-else-if="!activeMaterialReadyForMapping"
            class="tp-error-text"
          >
            当前资料仍在处理或需要续跑；完整发布前不能保存正式课时关联。
          </p>
          <p v-if="!workbench.catalog.lessonNodes.some(item => item.node_type === 'lesson' && item.is_active)" class="tp-error-text">
            还没有可关联课时，请先返回“个人课时树”建立课时。
          </p>
          <label class="tp-field">
            目标课时
            <select v-model="manualMapping.lessonId">
              <option value="">请选择课时</option>
              <option
                v-for="lesson in workbench.catalog.lessonNodes.filter(item => item.node_type === 'lesson' && item.is_active)"
                :key="lesson.id"
                :value="lesson.id"
              >
                {{ lesson.title }}
              </option>
            </select>
          </label>
          <label class="tp-field">
            资料用途
            <select v-model="manualMapping.purpose">
              <option v-for="purpose in LINK_PURPOSES" :key="purpose.value" :value="purpose.value">
                {{ purpose.label }}
              </option>
            </select>
          </label>
          <div class="tp-field-pair">
            <label class="tp-field">起始页<input v-model.number="manualMapping.startUnit" type="number" min="1"></label>
            <label class="tp-field">结束页<input v-model.number="manualMapping.endUnit" type="number" min="1"></label>
          </div>
          <label class="tp-field">
            备注（可选）
            <input v-model="manualMapping.note" type="text" placeholder="例如：第一课时新授范围">
          </label>
          <button
            class="tp-button tp-button--primary"
            type="button"
            :disabled="(
              !activeMaterialReadyForMapping
              || !manualMapping.lessonId
              || workbench.catalog.materialUnits.length === 0
              || workbench.catalog.saveState === 'saving'
            )"
            @click="saveManualMapping"
          >
            保存人工关联
          </button>
        </section>
      </template>
    </TeachingPrepDocumentWorkspace>

    <TeachingPrepStickyActions
      :state="allMappingsDecided ? 'saved' : 'dirty'"
      :message="mappingMessage"
    >
      <button class="tp-button tp-button--secondary" type="button" @click="workbench.openStage('select')">
        返回选课时
      </button>
      <button
        class="tp-button tp-button--primary"
        type="button"
        :disabled="!allMappingsDecided"
        aria-describedby="mapping-gate"
        @click="applyProposal"
      >
        应用全部接受项
      </button>
      <span id="mapping-gate" class="tp-visually-hidden">
        {{ allMappingsDecided ? '可以应用' : '请先决定每一条映射建议' }}
      </span>
    </TeachingPrepStickyActions>
  </section>
</template>
