<script setup lang="ts">
import { computed, onMounted, provide, ref } from 'vue'

import AppButton from '../../../components/design-system/AppButton.vue'
import FeedbackBanner from '../../../components/design-system/FeedbackBanner.vue'
import StatePanel from '../../../components/design-system/StatePanel.vue'
import { lessonProgressLabel } from '../progressLabels'
import { teachingPrepLessonWorkbenchKey } from '../workbench/routeContext'
import { useTeachingPrepLessonWorkbench } from '../workbench/lessonWorkbench'
import type { TeachingPrepLessonStep } from '../workbench/routeState'
import ConfirmMaterialsStep from './lesson/ConfirmMaterialsStep.vue'
import ReviewSlidesStep from './lesson/ReviewSlidesStep.vue'
import CopiesStep from './lesson/CopiesStep.vue'

interface StepHandle {
  primaryLabel: string
  primaryDisabled: boolean
  runPrimary: () => Promise<void>
}

const workbench = useTeachingPrepLessonWorkbench()
provide(teachingPrepLessonWorkbenchKey, workbench)

const routeState = workbench.routeState
const stepRef = ref<StepHandle | null>(null)

const STEPS: Array<{ id: TeachingPrepLessonStep; title: string; desc: string; hint: string }> = [
  { id: 1, title: '① 确认资料', desc: '挑选本课资料', hint: '确认本课要用的主课件和参考资料，然后发送给 AI 改编。' },
  { id: 2, title: '② 审核改编', desc: '逐页确认 AI 修改', hint: 'AI 给出了每一页的修改建议，逐页选择接受、拒绝或标记人工处理。' },
  { id: 3, title: '③ 副本与上课包', desc: '生成上课用 PPT', hint: '检查生成的 PPT 副本，确认无误后打包为上课包。' },
]

const currentStepMeta = computed(() => STEPS.find(item => item.id === routeState.currentStep.value) ?? STEPS[0]!)
const lessonTitle = computed(() => workbench.selectedLesson.value?.title ?? '当前课时')

function stepDone(step: TeachingPrepLessonStep): boolean {
  const status = workbench.selectedStatus.value
  if (!status) return false
  if (step === 1) return status.cells.materials.status === 'ready'
  if (step === 2) return status.cells.slides.status === 'ready'
  return Boolean(status.latest.pptx_version_id)
}

onMounted(() => { void workbench.load() })
</script>

<template>
  <section class="tp-page" aria-label="课时备课">
    <FeedbackBanner
      v-if="workbench.workbenchError.value"
      tone="warning"
      title="当前状态未完全载入"
      :description="workbench.workbenchError.value"
      action-label="重新载入"
      @action="workbench.refresh()"
    />
    <StatePanel
      v-if="workbench.loading.value"
      kind="loading"
      title="正在载入课时"
      description="正在整理本课资料与版本……"
    />
    <div v-else class="tp-lesson-frame">
      <aside class="tp-rail" aria-label="备课步骤">
        <p class="tp-rail__label">本课备课步骤</p>
        <button
          v-for="step in STEPS"
          :key="step.id"
          type="button"
          class="tp-rail__step"
          :class="{ 'is-active': step.id === routeState.currentStep.value, 'is-done': stepDone(step.id) }"
          @click="routeState.setStep(step.id)"
        >
          <span class="tp-rail__num">{{ stepDone(step.id) ? '✓' : step.id }}</span>
          <span>
            <span class="tp-rail__step-title">{{ step.title }}</span><br>
            <span class="tp-rail__step-desc">{{ step.desc }}</span>
          </span>
        </button>
        <button type="button" class="tp-rail__back" @click="workbench.requestLeave().then((ok) => { if (ok) void routeState.openOverview() })">
          ← 返回备课首页
        </button>
      </aside>

      <section class="tp-lesson-canvas" aria-label="当前步骤工作面">
        <ConfirmMaterialsStep v-if="routeState.currentStep.value === 1" ref="stepRef" />
        <ReviewSlidesStep v-else-if="routeState.currentStep.value === 2" ref="stepRef" />
        <CopiesStep v-else ref="stepRef" />
      </section>

      <aside class="tp-inspector" aria-label="本步说明">
        <h3>准备摘要</h3>
        <p class="tp-inspector__lesson">{{ lessonTitle }}</p>
        <dl>
          <div><dt>授课状态</dt><dd>{{ lessonProgressLabel(workbench.selectedStatus.value?.manual_progress) }}</dd></div>
          <div><dt>当前步骤</dt><dd>{{ currentStepMeta.title }}</dd></div>
        </dl>
        <p v-if="workbench.dirtyReason.value" class="tp-inline-message" role="status">
          尚未保存：{{ workbench.dirtyReason.value }}
        </p>
        <div class="tp-inspector__action">
          <p>{{ currentStepMeta.hint }}</p>
          <AppButton
            variant="primary"
            block
            data-testid="lesson-step-primary"
            :disabled="stepRef?.primaryDisabled ?? false"
            @click="stepRef?.runPrimary()"
          >
            {{ stepRef?.primaryLabel ?? currentStepMeta.title }}
          </AppButton>
        </div>
      </aside>
    </div>
  </section>
</template>
