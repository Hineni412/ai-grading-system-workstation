<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'

import AppButton from '../design-system/AppButton.vue'
import StudentMatchSelect from './StudentMatchSelect.vue'
import type { ScanPreflightController } from './useScanPreflight'

const props = defineProps<{ recon: ScanPreflightController }>()

const {
  store,
  selectedStudents,
  viewerSide,
  viewerZoom,
  viewerCanvas,
  viewerTargets,
  activeViewerTarget,
  activeViewerIndex,
  activeViewerUrl,
  decisionFor,
  decisionStatus,
  saveDecision,
  closeViewer,
  moveViewer,
  setViewerSide,
  changeViewerZoom,
  beginViewerPan,
  moveViewerPan,
  endViewerPan,
} = props.recon

function onWindowKeydown(event: KeyboardEvent): void {
  if (!activeViewerTarget.value) return
  if (event.key === 'Escape') closeViewer()
  if (event.key === 'ArrowLeft') moveViewer(-1)
  if (event.key === 'ArrowRight') moveViewer(1)
}

onMounted(() => {
  window.addEventListener('keydown', onWindowKeydown)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onWindowKeydown)
})
</script>

<template>
  <div
    v-if="activeViewerTarget"
    class="scan-viewer"
    role="dialog"
    aria-modal="true"
    aria-labelledby="scan-viewer-title"
  >
    <div class="scan-viewer__shell">
      <header class="scan-viewer__header">
        <div>
          <span>原卷核对</span>
          <h2 id="scan-viewer-title">{{ activeViewerTarget.title }}</h2>
        </div>
        <AppButton variant="secondary" aria-label="关闭原卷大图" @click="closeViewer">关闭</AppButton>
      </header>
      <div class="scan-viewer__toolbar" aria-label="原卷查看工具">
        <button type="button" :disabled="activeViewerIndex <= 0" @click="moveViewer(-1)">上一份</button>
        <button type="button" :aria-pressed="viewerSide === 'front'" @click="setViewerSide('front')">正面</button>
        <button type="button" :disabled="!activeViewerTarget.backUrl" :aria-pressed="viewerSide === 'back'" @click="setViewerSide('back')">反面</button>
        <button type="button" @click="changeViewerZoom(-0.25)">缩小</button>
        <span>{{ Math.round(viewerZoom * 100) }}%</span>
        <button type="button" @click="changeViewerZoom(0.25)">放大</button>
        <button type="button" :disabled="activeViewerIndex >= viewerTargets.length - 1" @click="moveViewer(1)">下一份</button>
      </div>
      <div class="scan-viewer__body">
        <div
          ref="viewerCanvas"
          class="scan-viewer__canvas"
          :data-pannable="viewerZoom > 1"
          @pointerdown="beginViewerPan"
          @pointermove="moveViewerPan"
          @pointerup="endViewerPan"
          @pointercancel="endViewerPan"
        >
          <img
            :src="activeViewerUrl"
            :alt="`${activeViewerTarget.title}${viewerSide === 'front' ? '正面' : '反面'}整页原卷`"
            :style="{ width: `${viewerZoom * 100}%` }"
          >
        </div>
        <aside class="scan-viewer__decision">
          <span>识别结果</span>
          <strong>{{ activeViewerTarget.detectedName }}</strong>
          <small>{{ activeViewerTarget.title }}</small>
          <StudentMatchSelect
            v-model="selectedStudents[activeViewerTarget.targetId]"
            :students="store.students"
            placeholder="姓名、学号或拼音"
            aria-label="为当前原卷选择学生"
          />
          <AppButton
            variant="primary"
            :disabled="!selectedStudents[activeViewerTarget.targetId] || !activeViewerTarget.backUrl || Boolean(store.busyAction)"
            @click="saveDecision(activeViewerTarget.targetType, activeViewerTarget.targetId, 'match')"
          >
            {{ activeViewerTarget.targetType === 'group' ? '确认归属' : '匹配为该学生' }}
          </AppButton>
          <AppButton variant="secondary" :disabled="Boolean(store.busyAction)" @click="saveDecision(activeViewerTarget.targetType, activeViewerTarget.targetId, 'invalid')">标记无效</AppButton>
          <AppButton variant="ghost" :disabled="Boolean(store.busyAction)" @click="saveDecision(activeViewerTarget.targetType, activeViewerTarget.targetId, 'pending')">稍后处理</AppButton>
          <p v-if="decisionFor(activeViewerTarget.targetType, activeViewerTarget.targetId)" class="scan-decision-state">
            {{ decisionStatus(decisionFor(activeViewerTarget.targetType, activeViewerTarget.targetId)) }}
          </p>
        </aside>
      </div>
    </div>
  </div>
</template>
