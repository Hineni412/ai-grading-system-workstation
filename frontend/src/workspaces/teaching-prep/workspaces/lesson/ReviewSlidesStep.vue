<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import type { SlideOperationDecision, SlidePlan } from '../../api/catalog'
import { useWorkspaceAITaskStore } from '../../../shared/ai-tasks/store'
import { adoptTeachingPrepProposal } from '../../aiAdoption'
import { useTeachingPrepLessonWorkbenchContext } from '../../workbench/routeContext'

const workbench = useTeachingPrepLessonWorkbenchContext()
const routeState = workbench.routeState
const catalog = workbench.catalog
const aiTasks = useWorkspaceAITaskStore()
const decisions = reactive<Record<string, SlideOperationDecision>>({})
const reasons = reactive<Record<string, string>>({})
const teacherNotes = reactive<Record<string, string>>({})
const plannedMinutes = reactive<Record<string, number>>({})
const targetSlideNumbers = reactive<Record<string, number>>({})
const positions = reactive<Record<string, { x: number; y: number; width: number; height: number }>>({})
const textbookLabels = reactive<Record<string, string>>({})
const reviewMessage = ref('逐项审核课件建议；只有全部决定后才能执行 WPS。')
const activeOperationId = ref<string | null>(null)
const activeSlideIndex = ref(0)
const handledTaskRevisions = new Set<string>()

const selectedPlan = computed(() => catalog.slidePlans.find(
  item => item.id === catalog.selectedSlidePlanId,
) ?? catalog.slidePlans[0] ?? null)
const preview = computed(() => catalog.slidePlanPreview)
const currentSlideTask = computed(() => {
  const lessonId = routeState.currentLessonId.value
  if (!lessonId) return null
  return aiTasks.orderedTasks.find(task => (
    task.module === 'teaching_prep'
    && task.task_kind === 'teaching_prep.slide_change_proposal'
    && task.source_ref.kind === 'lesson'
    && task.source_ref.id === lessonId
    && task.status !== 'discarded'
  )) ?? null
})
const taskPresentation = computed(() => {
  const task = currentSlideTask.value
  if (!task) return { title: '还没有发送改编任务', detail: '返回上一步，确认主课件和参考资料后再发送。', tone: 'idle' }
  if (task.status === 'prepared' || task.status === 'queued') return { title: '改编任务正在排队', detail: '资料范围已经锁定，可以离开此页面；任务不会重复发送。', tone: 'running' }
  if (task.status === 'running') return { title: 'AI 正在逐页分析课件', detail: task.teacher_message || '完成后会在这里显示保留、删除、调整和新增建议。', tone: 'running' }
  if (task.status === 'proposal_ready') return { title: '改编建议已经返回', detail: '正在载入逐页审核内容。', tone: 'ready' }
  if (task.status === 'needs_input') return { title: '需要教师确认后继续', detail: task.teacher_message || task.next_action, tone: 'attention' }
  if (['failed', 'failed_before_dispatch', 'invalid_result', 'result_unknown'].includes(task.status)) {
    return { title: '本次改编没有完成', detail: task.teacher_message || '没有改动原 PPT。返回确认资料后，可由教师明确重新发送。', tone: 'error' }
  }
  return { title: '正在恢复改编状态', detail: task.teacher_message || '请稍候。', tone: 'idle' }
})
const activeOperation = computed(() => selectedPlan.value?.payload.operations.find(
  item => item.operation_id === activeOperationId.value,
) ?? selectedPlan.value?.payload.operations[0] ?? null)
const activeSlide = computed(() => preview.value?.before[activeSlideIndex.value] ?? null)
const activeOperationOnSlide = computed(() => (
  activeOperation.value !== null && slideIndexFor(activeOperation.value) === activeSlideIndex.value
))
const activeOverlayStyle = computed(() => {
  const operation = activeOperation.value
  if (!operation || operation.execution_mode === 'manual_only' || !activeSlide.value || !activeOperationOnSlide.value) return null
  const editedPosition = positions[operation.operation_id]
  return overlayStyle(editedPosition ? { position: editedPosition } : operation.target)
})
const allOperationsDecided = computed(() => selectedPlan.value?.payload.operations.every(
  item => (decisions[item.operation_id] ?? item.decision) !== 'proposed',
) ?? false)

function describeSlide(item: Record<string, unknown>, index: number): string {
  return String(item.title ?? item.name ?? item.slide_ref ?? `第 ${index + 1} 页`)
}

function describeChange(item: Record<string, unknown>): string {
  return String(item.summary ?? item.reason ?? item.kind ?? '页面调整')
}

function operationLabel(kind: string): string {
  return {
    delete_slide: '删除整页',
    delete_shape: '删除页内题目',
    add_text_box: '填写教材页码',
    insert_static_image: '插入教辅题',
    add_slide: '新增练习页',
    manual_note: '需要人工处理',
  }[kind] ?? kind
}

function operationOverlayClass(kind: string): string {
  if (kind === 'delete_shape' || kind === 'delete_slide') return 'is-delete'
  if (kind === 'insert_static_image') return 'is-insert'
  if (kind === 'add_text_box') return 'is-label'
  return 'is-generic'
}

function editablePosition(item: NonNullable<typeof activeOperation.value>): boolean {
  return item.kind === 'insert_static_image' || item.kind === 'add_text_box'
}

function isTextbookLabel(item: NonNullable<typeof activeOperation.value>): boolean {
  return item.kind === 'add_text_box' && item.details.semantic_role === 'textbook_page_label'
}

function initializeEditableOperation(item: NonNullable<typeof activeOperation.value>): void {
  const rawPosition = item.target.position
  if (rawPosition && typeof rawPosition === 'object' && !Array.isArray(rawPosition)) {
    const value = rawPosition as Record<string, unknown>
    positions[item.operation_id] = {
      x: Number(value.x),
      y: Number(value.y),
      width: Number(value.width),
      height: Number(value.height),
    }
  }
  if (item.kind === 'insert_static_image' && item.target.target_kind === 'existing_slide') {
    targetSlideNumbers[item.operation_id] = Number(item.target.generated_page_number)
  }
  if (isTextbookLabel(item)) {
    textbookLabels[item.operation_id] = String(item.details.text ?? item.target.content_summary ?? '')
  }
}

function overlayStyle(target: Record<string, unknown>): Record<string, string> | null {
  const position = target.position
  if (!position || typeof position !== 'object' || Array.isArray(position)) return null
  const value = position as Record<string, unknown>
  const x = Number(value.x)
  const y = Number(value.y)
  const width = Number(value.width)
  const height = Number(value.height)
  if (![x, y, width, height].every(Number.isFinite) || x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1 || y + height > 1) return null
  return { left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }
}

function slideIndexFor(operation: NonNullable<typeof activeOperation.value>): number {
  const hasEditedTargetPage = (
    operation.kind === 'insert_static_image'
    && operation.target.target_kind === 'existing_slide'
    && targetSlideNumbers[operation.operation_id] !== undefined
  )
  const page = Number(
    hasEditedTargetPage
      ? targetSlideNumbers[operation.operation_id] ?? operation.target.generated_page_number
      : operation.target.generated_page_number,
  )
  const signature = String(operation.target.slide_signature ?? '')
  const unitId = String(operation.target.material_unit_id ?? '')
  const sourceLinkId = String(operation.target.source_link_id ?? '')
  const slides = preview.value?.before ?? []

  if (hasEditedTargetPage && Number.isFinite(page)) {
    const index = slides.findIndex(item => Number(item.original_index) === page)
    if (index >= 0) return index
  }
  if (signature) return slides.findIndex(item => String(item.stable_signature ?? '') === signature)
  if (unitId) return slides.findIndex(item => String(item.material_unit_id ?? '') === unitId)
  if (sourceLinkId && Number.isFinite(page)) {
    return slides.findIndex(item => (
      String(item.source_link_id ?? '') === sourceLinkId
      && Number(item.original_index) === page
    ))
  }
  if (!Number.isFinite(page)) return -1
  const pageMatches = slides
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => Number(item.original_index) === page)
  return pageMatches.length === 1 ? pageMatches[0]!.index : -1
}

function targetSlideChanged(operation: NonNullable<typeof activeOperation.value>): void {
  workbench.setDirty('教辅题目标页')
  const index = slideIndexFor(operation)
  if (index >= 0) activeSlideIndex.value = index
}

function selectOperation(operation: NonNullable<typeof activeOperation.value>): void {
  activeOperationId.value = operation.operation_id
  const index = slideIndexFor(operation)
  if (index >= 0) activeSlideIndex.value = index
}

watch(selectedPlan, (plan) => {
  if (!plan) return
  for (const item of plan.payload.operations) {
    decisions[item.operation_id] = item.decision
    reasons[item.operation_id] = item.reason
    teacherNotes[item.operation_id] = item.teacher_note ?? ''
    plannedMinutes[item.operation_id] = item.planned_minutes
    initializeEditableOperation(item)
  }
  const first = plan.payload.operations[0]
  activeOperationId.value = first?.operation_id ?? null
  activeSlideIndex.value = first ? Math.max(0, slideIndexFor(first)) : 0
}, { immediate: true })

watch(
  () => currentSlideTask.value ? `${currentSlideTask.value.task_id}:${currentSlideTask.value.revision}` : '',
  async (key) => {
    const task = currentSlideTask.value
    if (!key || !task || handledTaskRevisions.has(key) || task.status !== 'proposal_ready' || !task.proposal_ref_id) return
    handledTaskRevisions.add(key)
    const lesson = catalog.selectedLesson
    if (lesson) await catalog.selectLesson(lesson)
  },
  { immediate: true },
)

async function selectPlan(plan: SlidePlan): Promise<void> {
  await catalog.selectSlidePlan(plan)
  for (const item of plan.payload.operations) {
    decisions[item.operation_id] = item.decision
    reasons[item.operation_id] = item.reason
    teacherNotes[item.operation_id] = item.teacher_note ?? ''
    plannedMinutes[item.operation_id] = item.planned_minutes
    initializeEditableOperation(item)
  }
  activeOperationId.value = plan.payload.operations[0]?.operation_id ?? null
  const first = plan.payload.operations[0]
  activeSlideIndex.value = first ? Math.max(0, slideIndexFor(first)) : 0
}

async function saveReview(): Promise<boolean> {
  if (!selectedPlan.value) return false
  const plan = selectedPlan.value
  const operationReviews = plan.payload.operations.map(item => ({
      operation_id: item.operation_id,
      decision: decisions[item.operation_id] ?? item.decision,
      reason: reasons[item.operation_id] || item.reason,
      planned_minutes: plannedMinutes[item.operation_id] ?? item.planned_minutes,
      teacher_note: teacherNotes[item.operation_id]?.trim() || null,
      ...(item.kind === 'insert_static_image' && item.target.target_kind === 'existing_slide'
        ? { target_slide_number: targetSlideNumbers[item.operation_id] }
        : {}),
      ...(editablePosition(item) && positions[item.operation_id]
        ? { position: positions[item.operation_id] }
        : {}),
      ...(isTextbookLabel(item)
        ? { text: textbookLabels[item.operation_id] }
        : {}),
  }))
  try {
    const adopted = await adoptTeachingPrepProposal(
      aiTasks.orderedTasks,
      'teaching_prep.slide_change_proposal',
      plan.id,
      {
        kind: 'review_slide_plan',
        operation_reviews: operationReviews,
        approve_low_risk_deletions: false,
        review_note: '教师已在课件版本工作面逐项审核',
      },
    )
    if (adopted) {
      workbench.setDirty(null)
      reviewMessage.value = '课件计划审核已保存并生成采用回执。原 PPTX 没有被修改。'
      const refreshes = await Promise.allSettled([
        aiTasks.refresh(adopted.match.task.task_id),
        workbench.refresh(),
      ])
      if (refreshes.some(result => result.status === 'rejected')) {
        reviewMessage.value = '课件审核已保存并生成采用回执；页面状态暂未刷新，请刷新页面。'
      }
      return true
    }
    await catalog.reviewSlidePlan(
      plan,
      operationReviews,
      { reviewNote: '教师已在课件版本工作面逐项审核' },
    )
    workbench.setDirty(null)
    reviewMessage.value = '课件计划审核已保存。原 PPTX 没有被修改。'
    await workbench.refresh()
    return true
  } catch {
    reviewMessage.value = '课件审核尚未确认；逐项决定仍保留，请直接重试。'
    return false
  }
}

async function runPrimary(): Promise<void> {
  const saved = await saveReview()
  if (saved) await routeState.setStep(3)
}

defineExpose({
  primaryLabel: computed(() => '保存审核决定并进入副本'),
  primaryDisabled: computed(() => !allOperationsDecided.value),
  runPrimary,
})
</script>

<template>
  <div class="tp-step-canvas">
    <section v-if="!selectedPlan" class="tp-panel tp-ai-waiting" :class="`is-${taskPresentation.tone}`" role="status">
      <div class="tp-panel__body">
        <h2>{{ taskPresentation.title }}</h2>
        <p>{{ taskPresentation.detail }}</p>
        <div class="tp-inline-actions">
          <AppButton variant="secondary" @click="routeState.setStep(1)">返回确认资料</AppButton>
          <AppButton v-if="currentSlideTask" variant="ghost" @click="aiTasks.refresh(currentSlideTask.task_id)">刷新任务状态</AppButton>
        </div>
      </div>
    </section>

    <template v-else>
      <section v-if="catalog.slidePlans.length > 1" class="tp-panel">
        <div class="tp-panel__head"><h2>计划版本</h2></div>
        <div class="tp-panel__body tp-inline-actions">
          <AppButton
            v-for="plan in catalog.slidePlans"
            :key="plan.id"
            :variant="plan.id === catalog.selectedSlidePlanId ? 'primary' : 'secondary'"
            @click="selectPlan(plan)"
          >
            计划 {{ plan.version_number }} · {{ plan.status }}
          </AppButton>
        </div>
      </section>

      <section class="tp-panel" aria-label="AI 改编建议逐页审核">
        <div class="tp-panel__head">
          <h2>AI 改编建议 · 逐页审核</h2>
          <span class="tp-panel__hint">接受 = 采用 AI 修改；拒绝 = 保留原页；人工 = 课后自己改。原 PPTX 不会被修改。</span>
        </div>
        <div class="tp-slide-checker">
          <aside class="tp-slide-checker__thumbs" aria-label="源幻灯片缩略图">
            <button
              v-for="(item, index) in preview?.before ?? []"
              :key="`source-${index}`"
              type="button"
              :class="{ 'is-active': index === activeSlideIndex }"
              @click="activeSlideIndex = index"
            >
              <span>{{ index + 1 }}</span><strong>{{ describeSlide(item, index) }}</strong>
            </button>
          </aside>

          <section class="tp-slide-checker__canvas" aria-label="源幻灯片与修改定位">
            <header><span>源幻灯片 · 不会原地修改</span><strong>{{ preview?.before_slide_count ?? 0 }} 页</strong></header>
            <div class="tp-slide-stage">
              <img v-if="activeSlide?.preview_url" :src="String(activeSlide.preview_url)" alt="当前源幻灯片预览">
              <div v-else class="tp-slide-stage__paper"><strong>{{ describeSlide(activeSlide ?? {}, 0) }}</strong><span>当前数据没有可用图片预览，仍可审核结构化定位。</span></div>
              <span
                v-if="activeOperation && activeOverlayStyle"
                class="tp-change-overlay"
                :class="operationOverlayClass(activeOperation.kind)"
                :style="activeOverlayStyle"
              ><b>{{ operationLabel(activeOperation.kind) }}</b></span>
            </div>
            <p v-if="activeOperation?.execution_mode === 'manual_only'" class="tp-inline-message">这项修改只能由教师人工处理，不会交给 WPS 自动执行。</p>
            <p v-else-if="activeOperationOnSlide && activeOperation && !activeOverlayStyle" class="tp-inline-message">这项修改没有可验证的源页区域，因此不显示定位框；请按文字说明核对。</p>
          </section>

          <section class="tp-slide-checker__inspector">
            <article
              v-for="item in selectedPlan.payload.operations"
              :key="item.operation_id"
              class="tp-operation-row"
              :class="{ 'is-active': activeOperation?.operation_id === item.operation_id }"
            >
              <button class="tp-operation-row__selector" type="button" @click="selectOperation(item)">
                <strong>{{ operationLabel(item.kind) }}</strong>
                <span>{{ item.reason }}</span>
                <small>{{ item.support_note || describeChange(item.details) }}</small>
              </button>
              <fieldset>
                <legend class="tp-visually-hidden">审核 {{ item.kind }}</legend>
                <label v-if="item.execution_mode !== 'manual_only'">
                  <input v-model="decisions[item.operation_id]" type="radio" :name="item.operation_id" value="approved" @change="workbench.setDirty('课件审核决定')">接受
                </label>
                <label v-else>
                  <input v-model="decisions[item.operation_id]" type="radio" :name="item.operation_id" value="rejected" @change="workbench.setDirty('课件审核决定')">标记为人工处理
                </label>
                <label v-if="item.execution_mode !== 'manual_only'">
                  <input v-model="decisions[item.operation_id]" type="radio" :name="item.operation_id" value="rejected" @change="workbench.setDirty('课件审核决定')">拒绝
                </label>
              </fieldset>
              <details class="tp-operation-editor">
                <summary>编辑 AI 建议</summary>
                <label>
                  <span>修改说明</span>
                  <textarea v-model="reasons[item.operation_id]" rows="3" maxlength="1000" @input="workbench.setDirty('课件建议文字')" />
                </label>
                <label>
                  <span>教师备注（可选）</span>
                  <textarea v-model="teacherNotes[item.operation_id]" rows="2" maxlength="1000" @input="workbench.setDirty('课件建议文字')" />
                </label>
                <label>
                  <span>预计占用课堂时间（分钟）</span>
                  <input v-model.number="plannedMinutes[item.operation_id]" type="number" min="0" max="120" @input="workbench.setDirty('课件建议时间')">
                </label>
                <label v-if="item.kind === 'insert_static_image' && item.target.target_kind === 'existing_slide'">
                  <span>插到主 PPT 第几页</span>
                  <input v-model.number="targetSlideNumbers[item.operation_id]" data-testid="operation-target-slide" type="number" min="1" :max="preview?.before_slide_count ?? 2000" @input="workbench.setDirty('教辅题目标页')" @change="targetSlideChanged(item)">
                </label>
                <label v-if="isTextbookLabel(item)">
                  <span>教材页码文字（例如：教材 P9）</span>
                  <input v-model="textbookLabels[item.operation_id]" data-testid="operation-textbook-label" type="text" maxlength="120" @input="workbench.setDirty('教材页码文字')">
                </label>
                <fieldset v-if="editablePosition(item) && positions[item.operation_id]" class="tp-position-editor">
                  <legend>页面位置（0—1，可人工修正）</legend>
                  <label><span>左边距 x</span><input v-model.number="positions[item.operation_id]!.x" data-axis="x" type="number" min="0" max="1" step="0.01" @input="workbench.setDirty('课件对象位置')"></label>
                  <label><span>上边距 y</span><input v-model.number="positions[item.operation_id]!.y" data-axis="y" type="number" min="0" max="1" step="0.01" @input="workbench.setDirty('课件对象位置')"></label>
                  <label><span>宽度</span><input v-model.number="positions[item.operation_id]!.width" data-axis="width" type="number" min="0.01" max="1" step="0.01" @input="workbench.setDirty('课件对象位置')"></label>
                  <label><span>高度</span><input v-model.number="positions[item.operation_id]!.height" data-axis="height" type="number" min="0.01" max="1" step="0.01" @input="workbench.setDirty('课件对象位置')"></label>
                </fieldset>
              </details>
            </article>
            <div v-if="preview?.source_changed" class="tp-banner tp-banner--warn" role="alert">来源 PPTX 已变化，本计划不可执行；请基于新来源创建计划。</div>
          </section>
        </div>
        <div class="tp-panel__foot">
          <AppButton variant="primary" :disabled="!allOperationsDecided" @click="saveReview">保存全部审核决定</AppButton>
          <span class="tp-inline-message" role="status">{{ reviewMessage }}</span>
        </div>
      </section>
    </template>
  </div>
</template>
