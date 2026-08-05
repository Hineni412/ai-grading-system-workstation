<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import type { SlideOperationDecision, SlidePlan } from '../api/catalog'
import { teachingPrepWorkbenchApi, type TrustedPptxVersion } from '../api/workbench'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'
import { adoptTeachingPrepProposal } from '../aiAdoption'
import TeachingPrepStickyActions from '../components/TeachingPrepStickyActions.vue'
import { useTeachingPrepWorkbenchContext } from '../workbench/context'

const workbench = useTeachingPrepWorkbenchContext()
const aiTasks = useWorkspaceAITaskStore()
const decisions = reactive<Record<string, SlideOperationDecision>>({})
const reviewMessage = ref('逐项审核课件建议；只有全部决定后才能执行 WPS。')
const activatingId = ref<string | null>(null)
const activeOperationId = ref<string | null>(null)
const activeSlideIndex = ref(0)

const selectedPlan = computed(() => workbench.catalog.slidePlans.find(
  item => item.id === workbench.catalog.selectedSlidePlanId,
) ?? workbench.catalog.slidePlans[0] ?? null)
const preview = computed(() => workbench.catalog.slidePlanPreview)
const latestRun = computed(() => workbench.catalog.pptxExecutions[0] ?? null)
const packageMode = computed(() => workbench.stage.value === 'package')
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
  return overlayStyle(operation.target)
})
const allOperationsDecided = computed(() => selectedPlan.value?.payload.operations.every(
  item => (decisions[item.operation_id] ?? item.decision) !== 'proposed',
) ?? false)
const canExecute = computed(() => (
  selectedPlan.value?.status === 'approved'
  && preview.value?.valid_for_execution
  && !latestRun.value
    ? true
    : selectedPlan.value?.status === 'approved'
      && preview.value?.valid_for_execution
      && !['running', 'verifying', 'publishing'].includes(latestRun.value?.status ?? '')
))

const phases = computed(() => {
  const order = ['copying', 'executing', 'verifying', 'publishing'] as const
  const current = latestRun.value?.phase ?? 'copying'
  const currentIndex = current === 'done' ? order.length : order.indexOf(current)
  return order.map((id, index) => ({
    id,
    label: {
      copying: '创建安全副本',
      executing: '执行已批准操作',
      verifying: '验证输出',
      publishing: '发布可信版本',
    }[id],
    state: latestRun.value
      ? index < currentIndex || latestRun.value.status === 'published'
        ? 'complete'
        : index === currentIndex && latestRun.value.status !== 'failed'
          ? 'running'
          : latestRun.value.status === 'failed' && index === currentIndex
            ? 'failed'
            : 'pending'
      : 'pending',
  }))
})

function describeSlide(item: Record<string, unknown>, index: number): string {
  return String(item.title ?? item.name ?? item.slide_ref ?? `第 ${index + 1} 页`)
}

function describeChange(item: Record<string, unknown>): string {
  return String(item.summary ?? item.reason ?? item.kind ?? '页面调整')
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
  const page = Number(operation.target.generated_page_number)
  const signature = String(operation.target.slide_signature ?? '')
  const unitId = String(operation.target.material_unit_id ?? '')
  const sourceLinkId = String(operation.target.source_link_id ?? '')
  const slides = preview.value?.before ?? []

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

function selectOperation(operation: NonNullable<typeof activeOperation.value>): void {
  activeOperationId.value = operation.operation_id
  const index = slideIndexFor(operation)
  if (index >= 0) activeSlideIndex.value = index
}

watch(selectedPlan, (plan) => {
  if (!plan) return
  for (const item of plan.payload.operations) decisions[item.operation_id] = item.decision
  const first = plan.payload.operations[0]
  activeOperationId.value = first?.operation_id ?? null
  activeSlideIndex.value = first ? Math.max(0, slideIndexFor(first)) : 0
}, { immediate: true })

async function selectPlan(plan: SlidePlan): Promise<void> {
  await workbench.catalog.selectSlidePlan(plan)
  for (const item of plan.payload.operations) decisions[item.operation_id] = item.decision
  activeOperationId.value = plan.payload.operations[0]?.operation_id ?? null
  const first = plan.payload.operations[0]
  activeSlideIndex.value = first ? Math.max(0, slideIndexFor(first)) : 0
}

async function saveReview(): Promise<void> {
  if (!selectedPlan.value) return
  const plan = selectedPlan.value
  const operationReviews = plan.payload.operations.map(item => ({
      operation_id: item.operation_id,
      decision: decisions[item.operation_id] ?? item.decision,
      reason: item.reason,
      planned_minutes: item.planned_minutes,
      teacher_note: item.teacher_note,
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
        workbench.refreshCurrentWorkspace(),
      ])
      if (refreshes.some(result => result.status === 'rejected')) {
        reviewMessage.value = '课件审核已保存并生成采用回执；页面状态暂未刷新，请刷新页面。'
      }
      return
    } else {
      await workbench.catalog.reviewSlidePlan(
        plan,
        operationReviews,
        { reviewNote: '教师已在课件版本工作面逐项审核' },
      )
    }
    workbench.setDirty(null)
    reviewMessage.value = '课件计划审核已保存。原 PPTX 没有被修改。'
    await workbench.refreshCurrentWorkspace()
  } catch {
    reviewMessage.value = '课件审核尚未确认；逐项决定仍保留，请直接重试。'
  }
}

async function executePlan(): Promise<void> {
  if (!selectedPlan.value) return
  await workbench.catalog.executePptx(selectedPlan.value)
  reviewMessage.value = '已创建异步 WPS 任务。关闭或刷新页面不会重复启动。'
  await workbench.refreshCurrentWorkspace()
}

async function activateVersion(version: TrustedPptxVersion): Promise<void> {
  activatingId.value = version.id
  try {
    const result = await teachingPrepWorkbenchApi.activatePptxVersion(
      version.id,
      version.current_revision,
    )
    reviewMessage.value = result.changed
      ? `已把第 ${version.version_number} 版恢复为当前可信 PPTX；上课包没有随之切换。`
      : '这个版本已经是当前可信 PPTX。'
    await workbench.refreshCurrentWorkspace()
  } finally {
    activatingId.value = null
  }
}

async function createPackage(version: TrustedPptxVersion): Promise<void> {
  await workbench.catalog.createUpClassPackage(version.id)
  reviewMessage.value = '上课包已从所选可信 PPTX 单独生成。'
}
</script>

<template>
  <section class="tp-workspace tp-versions-workspace">
    <header class="tp-workspace__header">
      <div>
        <p class="tp-eyebrow">课件版本</p>
        <h1 data-workbench-title tabindex="-1">{{ packageMode ? '可信 PPTX 与上课包' : '逐页审核课件改编' }}</h1>
        <p>先看原页与改后页，再批准操作；WPS 始终创建副本，原课件不会被覆盖。</p>
      </div>
      <button class="tp-button tp-button--secondary" type="button" @click="workbench.openStage('select')">返回课时树</button>
    </header>

    <div class="tp-version-layout">
      <aside class="tp-version-rail">
        <p class="tp-eyebrow">计划版本</p>
        <button
          v-for="plan in workbench.catalog.slidePlans"
          :key="plan.id"
          type="button"
          :class="{ 'is-selected': plan.id === workbench.catalog.selectedSlidePlanId }"
          @click="selectPlan(plan)"
        >
          <strong>计划 {{ plan.version_number }}</strong><small>{{ plan.status }}</small>
        </button>
        <p v-if="!workbench.catalog.slidePlans.length" class="tp-muted">先确认课堂草稿并创建课件计划。</p>
      </aside>

      <main v-if="!packageMode" class="tp-slide-checker">
        <aside class="tp-slide-checker__thumbs" aria-label="源幻灯片缩略图">
          <button v-for="(item, index) in preview?.before ?? []" :key="`source-${index}`" type="button" :class="{ 'is-active': index === activeSlideIndex }" @click="activeSlideIndex = index">
            <span>{{ index + 1 }}</span><strong>{{ describeSlide(item, index) }}</strong>
          </button>
        </aside>

        <section class="tp-slide-checker__canvas" aria-label="源幻灯片与修改定位">
          <header><span>源幻灯片 · 不会原地修改</span><strong>{{ preview?.before_slide_count ?? 0 }} 页</strong></header>
          <div class="tp-slide-stage">
            <img v-if="activeSlide?.preview_url" :src="String(activeSlide.preview_url)" alt="当前源幻灯片预览">
            <div v-else class="tp-slide-stage__paper"><strong>{{ describeSlide(activeSlide ?? {}, 0) }}</strong><span>当前数据没有可用图片预览，仍可审核结构化定位。</span></div>
            <span v-if="activeOperation && activeOverlayStyle" class="tp-change-overlay" :style="activeOverlayStyle"><b>{{ activeOperation.kind }}</b></span>
          </div>
          <p v-if="activeOperation?.execution_mode === 'manual_only'" class="tp-inline-guidance">这项修改只能由教师人工处理，不会交给 WPS 自动执行。</p>
          <p v-else-if="activeOperationOnSlide && activeOperation && !activeOverlayStyle" class="tp-inline-guidance">这项修改没有可验证的源页区域，因此不显示定位框；请按文字说明核对。</p>
        </section>

        <section class="tp-slide-checker__inspector">
          <div class="tp-section-heading"><div><p class="tp-eyebrow">AI 修改检查器</p><h2>逐项定位，再决定</h2></div></div>
          <article v-for="item in selectedPlan?.payload.operations ?? []" :key="item.operation_id" class="tp-operation-row" :class="{ 'is-active': activeOperation?.operation_id === item.operation_id }">
            <button class="tp-operation-row__selector" type="button" @click="selectOperation(item)"><strong>{{ item.kind }}</strong><span>{{ item.reason }}</span><small>{{ item.support_note || describeChange(item.details) }}</small></button>
            <fieldset>
              <legend class="tp-visually-hidden">审核 {{ item.kind }}</legend>
              <label v-if="item.execution_mode !== 'manual_only'"><input v-model="decisions[item.operation_id]" type="radio" :name="item.operation_id" value="approved" @change="workbench.setDirty('课件审核决定')">接受</label>
              <label v-else><input v-model="decisions[item.operation_id]" type="radio" :name="item.operation_id" value="rejected" @change="workbench.setDirty('课件审核决定')">标记为人工处理</label>
              <label v-if="item.execution_mode !== 'manual_only'"><input v-model="decisions[item.operation_id]" type="radio" :name="item.operation_id" value="rejected" @change="workbench.setDirty('课件审核决定')">拒绝</label>
            </fieldset>
          </article>
          <div v-if="preview?.source_changed" class="tp-inline-error" role="alert">来源 PPTX 已变化，本计划不可执行；请基于新来源创建计划。</div>
          <button class="tp-button tp-button--primary" type="button" :disabled="!allOperationsDecided" @click="saveReview">保存全部审核决定</button>
        </section>
      </main>

      <main v-else class="tp-release-center">
        <section class="tp-section-block">
          <div class="tp-section-heading"><div><p class="tp-eyebrow">WPS 实时状态</p><h2>从安全副本到可信版本</h2></div><span class="tp-status-pill">{{ latestRun?.status ?? '未开始' }}</span></div>
          <ol class="tp-execution-phases">
            <li v-for="item in phases" :key="item.id" :class="`is-${item.state}`"><span /><div><strong>{{ item.label }}</strong><small>{{ item.state }}</small></div></li>
          </ol>
          <p v-if="latestRun?.error_code" class="tp-inline-error">失败代码：{{ latestRun.error_code }}。未发布不可信文件，原课件保持不变。</p>
          <div class="tp-inline-actions">
            <button class="tp-button tp-button--primary" type="button" :disabled="!canExecute" @click="executePlan">创建新 PPTX 副本</button>
            <button v-if="latestRun && ['running', 'verifying', 'publishing'].includes(latestRun.status)" type="button" @click="workbench.catalog.cancelPptxExecution(latestRun)">取消后续阶段</button>
            <button v-if="latestRun?.recovery_actions.includes('retry')" type="button" @click="workbench.catalog.recoverPptxExecution(latestRun)">从安全检查点恢复</button>
            <button v-if="latestRun?.staging_retained" class="is-danger" type="button" @click="workbench.catalog.discardPptxStaging(latestRun)">清理暂存副本</button>
          </div>
        </section>

        <section class="tp-section-block">
          <div class="tp-section-heading"><div><p class="tp-eyebrow">PPTX 历史</p><h2>课时级可信版本</h2></div></div>
          <div class="tp-pptx-grid">
            <article v-for="version in workbench.pptxVersions.value" :key="version.id" :class="{ 'is-current': version.is_current }">
              <img v-if="version.file_verified" :src="version.preview_url" :alt="`PPTX 第 ${version.version_number} 版首屏预览`">
              <div v-else class="tp-file-missing">预览或文件已缺失，不能恢复</div>
              <header><div><strong>第 {{ version.version_number }} 版</strong><small>{{ version.created_at }}</small></div><span v-if="version.is_current" class="tp-trust-badge">当前可信</span></header>
              <p>{{ version.output_filename }} · {{ version.slide_count }} 页</p>
              <div class="tp-inline-actions">
                <a :href="version.download_url">下载副本</a>
                <button type="button" :disabled="version.is_current || !version.file_verified || activatingId === version.id" @click="activateVersion(version)">恢复为当前 PPTX</button>
                <button type="button" :disabled="!version.file_verified" @click="createPackage(version)">生成上课包</button>
              </div>
            </article>
          </div>
        </section>

        <section class="tp-section-block">
          <div class="tp-section-heading"><div><p class="tp-eyebrow">上课包</p><h2>与 PPTX 分离管理</h2></div></div>
          <div class="tp-package-list">
            <details v-for="item in workbench.catalog.upClassPackages" :key="item.id" :open="item.is_current">
              <summary><strong>上课包 {{ item.version_number }}</strong><span>{{ item.status }}{{ item.is_current ? ' · 当前使用' : '' }}</span></summary>
              <p>关联 PPTX：{{ item.pptx_version_id }}</p>
              <ul v-if="item.manifest">
                <li v-for="(value, key) in item.manifest" :key="key"><strong>{{ key }}</strong>：{{ value }}</li>
              </ul>
              <div class="tp-inline-actions">
                <a v-if="item.download_url" :href="item.download_url">下载上课包</a>
                <button type="button" :disabled="item.is_current || item.status !== 'complete'" @click="workbench.catalog.activateUpClassPackage(item)">恢复对应上课包</button>
                <button v-if="item.status === 'interrupted'" type="button" @click="workbench.catalog.recoverUpClassPackage(item)">恢复构建</button>
              </div>
            </details>
            <p v-if="!workbench.catalog.upClassPackages.length" class="tp-muted">可信 PPTX 发布后，可单独生成上课包。</p>
          </div>
        </section>
      </main>
    </div>

    <TeachingPrepStickyActions :state="workbench.catalog.saveState === 'saving' ? 'saving' : 'saved'" :message="reviewMessage">
      <button class="tp-button tp-button--secondary" type="button" @click="workbench.openStage(packageMode ? 'slides' : 'plan')">{{ packageMode ? '返回审课件' : '返回定方案' }}</button>
      <button v-if="!packageMode" class="tp-button tp-button--primary" type="button" @click="workbench.openStage('package')">查看 PPTX 与上课包</button>
    </TeachingPrepStickyActions>
  </section>
</template>
