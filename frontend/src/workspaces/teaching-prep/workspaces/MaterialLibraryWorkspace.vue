<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import type {
  MaterialVersion,
  SemesterMappingProposalRange,
} from '../api/catalog'
import { teachingPrepWorkbenchApi } from '../api/workbench'
import TeachingPrepDocumentWorkspace from '../components/TeachingPrepDocumentWorkspace.vue'
import TeachingPrepStickyActions from '../components/TeachingPrepStickyActions.vue'
import { useTeachingPrepWorkbenchContext } from '../workbench/context'

const workbench = useTeachingPrepWorkbenchContext()
const activeUnitId = ref<string | null>(null)
const mappingEdits = reactive<Record<string, {
  lessonRef: string
  startUnit: number
  endUnit: number
  reason: string
}>>({})
const mappingMessage = ref('逐条核对后，再一次写入正式映射。')

const activeUnit = computed(
  () => workbench.catalog.materialUnits.find(item => item.id === activeUnitId.value)
    ?? workbench.catalog.materialUnits[0]
    ?? null,
)
const activeProposal = computed(
  () => workbench.catalog.semesterMappingProposals.find(item => item.status === 'proposed')
    ?? workbench.catalog.semesterMappingProposals[0]
    ?? null,
)
const allMappingsDecided = computed(() => (
  activeProposal.value?.payload.mappings.every(item => item.decision !== 'pending') ?? false
))

watch(
  () => workbench.catalog.materialUnits,
  units => {
    if (!units.some(item => item.id === activeUnitId.value)) {
      activeUnitId.value = units[0]?.id ?? null
    }
  },
  { immediate: true },
)

async function openMaterial(item: MaterialVersion): Promise<void> {
  await workbench.catalog.openMaterial(item)
  activeUnitId.value = workbench.catalog.materialUnits[0]?.id ?? null
}

async function importCopy(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  await workbench.catalog.importMaterialCopy(file)
  input.value = ''
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
}

async function applyProposal(): Promise<void> {
  if (!activeProposal.value || !allMappingsDecided.value) return
  await workbench.catalog.applySemesterMapping(activeProposal.value)
  await workbench.refreshCurrentWorkspace()
  mappingMessage.value = '全部接受项已在一个事务中写入正式映射。'
}
</script>

<template>
  <section class="tp-workspace tp-material-workspace">
    <header class="tp-workspace__header">
      <div>
        <p class="tp-eyebrow">资料库</p>
        <h1 data-workbench-title tabindex="-1">资料对应哪些课时和原页？</h1>
        <p>先看原页，再逐条接受、修改或拒绝建议；模型建议不会直接改正式课时树。</p>
      </div>
      <label class="tp-button tp-button--secondary tp-file-button">
        导入受控副本
        <input type="file" accept=".pdf,.pptx,.png,.jpg,.jpeg,.webp" @change="importCopy">
      </label>
    </header>

    <TeachingPrepDocumentWorkspace
      :title="workbench.catalog.materials.find(item => item.id === workbench.catalog.selectedMaterialId)?.display_name ?? '原页预览'"
      :subtitle="activeUnit ? `第 ${activeUnit.unit_index} 页/张 · ${activeUnit.formula_review_required ? '公式待核对' : '原文可核对'}` : undefined"
      :preview-url="activeUnit?.preview_url"
    >
      <template #rail>
        <h2>本学期资料</h2>
        <button
          v-for="item in workbench.catalog.materials"
          :key="item.id"
          class="tp-rail-item"
          :class="{ 'is-selected': item.id === workbench.catalog.selectedMaterialId }"
          type="button"
          @click="openMaterial(item)"
        >
          <strong>{{ item.display_name }}</strong>
          <small>{{ item.material_type.toUpperCase() }} · {{ item.unit_count ?? 0 }} 页/张</small>
          <span>{{ item.availability === 'available' ? '可用' : '需重新定位' }}</span>
        </button>
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
          暂无待审核建议。模型不可用时仍可使用现有人工关联功能。
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
