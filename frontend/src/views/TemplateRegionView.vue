<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import TemplateRegionEditor from '../components/template-regions/TemplateRegionEditor.vue'
import TemplateUploadPanel from '../components/template-regions/TemplateUploadPanel.vue'
import ConfigStageRail from '../components/config/ConfigStageRail.vue'
import { useTemplateRegionStore } from '../stores/template-regions'
import type { PageRole } from '../api/template-regions'
import '../styles/template-regions.css'

const route = useRoute()
const router = useRouter()
const store = useTemplateRegionStore()
const routeSessionId = computed(() => Number(route.params.sessionId))
const focusLayout = computed(() => store.workspace !== null && store.editorReady)
let mainWorkspace: HTMLElement | null = null

function syncFocusLayout(enabled: boolean): void {
  mainWorkspace ??= document.getElementById('main-workspace')
  if (enabled && mainWorkspace) {
    mainWorkspace.scrollTop = 0
    mainWorkspace.scrollLeft = 0
  }
  mainWorkspace?.classList.toggle('main-workspace--template-focus', enabled)
}

function pageStats(page: PageRole) {
  const regions = store.editorState.regions.filter((item) => item.page === page)
  const bound = regions.filter((item) => item.mapped_question_id !== null).length
  const regionIds = new Set(regions.map((item) => item.region_uuid))
  const issues = store.workspace?.issues.filter((item) => item.region_uuid !== null
    && regionIds.has(item.region_uuid)).length ?? 0
  return { total: regions.length, bound, pending: regions.length - bound, issues }
}
const pageCounts = computed(() => ({ front: pageStats('front'), back: pageStats('back') }))
const commitSummary = computed(() => {
  const total = pageCounts.value.front.total + pageCounts.value.back.total
  const bound = pageCounts.value.front.bound + pageCounts.value.back.bound
  return `正面 ${pageCounts.value.front.total} 框，反面 ${pageCounts.value.back.total} 框；已绑定 ${bound} 框，待处理 ${total - bound} 框，校验问题 ${store.workspace?.issues.length ?? 0} 项。确认保存为正式版本？`
})

function loadRoute(): void {
  if (Number.isSafeInteger(routeSessionId.value) && routeSessionId.value > 0) {
    void store.load(routeSessionId.value)
  }
}
async function finish(): Promise<void> {
  if (!window.confirm(commitSummary.value)) return
  const result = await store.commit()
  if (result?.committed && !result.snapshot_pending) await openGradingRun()
}
async function retrySnapshotAndContinue(): Promise<void> {
  await store.retrySnapshot()
  if (store.readOnly && !store.snapshotPending) await openGradingRun()
}
async function openGradingRun(): Promise<void> {
  const id = routeSessionId.value
  if (!Number.isSafeInteger(id) || id <= 0) return
  await router.push(`/sessions/${id}/grading-run`)
}
function selectStage(stage: 'draft' | 'source' | 'generation' | 'editor' | 'template'): void {
  if (stage === 'template') return
  void router.push({ path: '/sessions', query: { stage } })
}
function replaceTemplate(file: File, role: PageRole): void {
  if (store.workspace && !window.confirm('替换样卷后，旧草稿不会自动套用到新样卷。确认继续上传？')) return
  void store.upload(file, role)
}
function confirmLeave(): boolean {
  return !store.hasUnsavedWork || window.confirm('当前画框或上传状态尚未安全保存，确认离开？')
}
function beforeUnload(event: BeforeUnloadEvent): void {
  if (!store.hasUnsavedWork) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  loadRoute()
  syncFocusLayout(focusLayout.value)
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => {
  mainWorkspace?.classList.remove('main-workspace--template-focus')
  window.removeEventListener('beforeunload', beforeUnload)
})
onBeforeRouteLeave(() => confirmLeave())
watch(routeSessionId, loadRoute)
watch(focusLayout, syncFocusLayout, { flush: 'post' })
</script>

<template>
  <article class="template-regions-view" :class="{ 'is-focus-layout': focusLayout }">
    <header class="template-regions-view__header">
      <div>
        <span class="template-regions__eyebrow">考试配置 · 第 5 步</span>
        <h1>样卷题框标定</h1>
        <p>在样卷原图上圈出每道题的作答区域，并绑定题号。</p>
      </div>
      <button type="button" class="secondary" @click="router.push('/sessions')">返回考试配置</button>
    </header>
    <ConfigStageRail
      phase="editor"
      active-stage="template"
      :session-ready="true"
      :source-ready="store.scoringConfigured"
      :generation-submitted="store.scoringConfigured"
      :editor-ready="store.scoringConfigured"
      :template-present="store.workspace !== null"
      :template-ready="store.workspace?.template_ready === true"
      @select="selectStage"
    />

    <div v-if="store.loadState === 'loading'" class="template-regions-view__state" role="status">正在读取样卷工作区…</div>
    <div v-else-if="store.loadState === 'error'" class="template-regions-view__state" role="alert">
      <p>{{ store.errorMessage }}</p>
      <button type="button" @click="store.load(routeSessionId)">重新读取</button>
    </div>
    <section v-else-if="store.workspace" class="template-regions-view__workspace">
      <div class="template-regions-view__ruler" :data-state="store.readOnly ? 'confirmed' : 'draft'">
        <strong>{{ store.readOnly ? '正式版本 · 只读' : '草稿标定中' }}</strong>
        <span>{{ store.saveState === 'saving' ? '正在保存草稿' : store.saveState === 'saved' ? '草稿已保存' : '按原图像素记录' }}</span>
        <span v-if="store.snapshotPending">正式区域已保存，确认快照待补写</span>
        <div v-if="store.editorReady" class="template-regions-view__faces" aria-label="正反面题框状态">
          <button type="button" class="secondary" :aria-pressed="store.editorState.active_page === 'front'"
            @click="store.showPage('front')">正面 {{ pageCounts.front.total }} 框 · 已绑定 {{ pageCounts.front.bound }} · 待处理 {{ pageCounts.front.pending }} · 异常 {{ pageCounts.front.issues }}</button>
          <button type="button" class="secondary" :aria-pressed="store.editorState.active_page === 'back'"
            @click="store.showPage('back')">反面 {{ pageCounts.back.total }} 框 · 已绑定 {{ pageCounts.back.bound }} · 待处理 {{ pageCounts.back.pending }} · 异常 {{ pageCounts.back.issues }}</button>
        </div>
      </div>
      <div class="template-regions-view__messages">
        <p v-if="store.errorMessage" class="template-regions-view__notice" role="alert">{{ store.errorMessage }}</p>
        <div v-if="store.saveState === 'error'" class="template-regions-view__notice" role="alert">
          <p>草稿仍保留在当前页面，可以再次尝试保存。</p>
          <button type="button" @click="store.flushDraft">重试保存草稿</button>
        </div>
        <div v-if="store.saveState === 'conflict'" class="template-regions-view__notice" role="alert">
          <p>服务器已有较新草稿，自动保存已停止。</p>
          <button type="button" @click="store.load(routeSessionId)">重新加载服务器草稿</button>
        </div>
        <div v-if="store.draftChoiceRequired" class="template-regions-view__choice" role="status">
          <h2>发现上次未完成的草稿</h2>
          <p>请选择继续上次草稿，或丢弃它并从已确认版本重新开始。</p>
          <button type="button" @click="store.continueDraft">继续上次草稿</button>
          <button type="button" class="secondary" @click="store.restartFromFormal">从已确认版本重新开始</button>
        </div>
        <div v-if="store.workspace.draft.status === 'incompatible' || store.workspace.draft.status === 'corrupt'"
          class="template-regions-view__notice" role="alert">
          <p>旧草稿与当前样卷不一致，未自动套用。可丢弃旧草稿后从已确认版本重新开始。</p>
          <button type="button" @click="store.discardDraft">丢弃旧草稿</button>
        </div>
      </div>
      <TemplateUploadPanel
        :template="store.workspace.template"
        :status="store.uploadState"
        :assignment-status="store.assignmentState"
        @upload="replaceTemplate"
        @assign="store.assignFirstPageRole"
        @reconcile="store.reconcileUpload"
      />
      <TemplateRegionEditor
        v-if="store.editorReady"
        :model-value="store.editorState"
        :images="store.workspace.template.pages"
        :manual-question-options="store.workspace.manual_question_options"
        :automatic-candidates="store.workspace.automatic_candidates"
        :issues="store.workspace.issues"
        :read-only="store.readOnly"
        :active-page="store.editorState.active_page"
        :save-status="store.saveState === 'saved' ? '草稿已保存' : '草稿'"
        @update:model-value="store.updateEditor"
        @finish="finish"
        @exit="router.push('/sessions')"
      />
      <div v-if="store.snapshotPending" class="template-regions-view__snapshot">
        <p>答题区域已经正式保存，只差补写本机确认快照。</p>
        <button type="button" @click="retrySnapshotAndContinue">重试生成确认快照</button>
      </div>
      <div v-else-if="store.readOnly" class="template-regions-view__snapshot template-regions-view__snapshot--confirmed">
        <p>题框已保存，可重新编辑。</p>
        <button type="button" class="secondary" @click="store.startEditingConfirmed">重新编辑题框草稿</button>
      </div>
    </section>
    <section v-else-if="!store.scoringConfigured" class="template-regions-view__prerequisite">
      <h2>请先完成评分依据</h2>
      <p>样卷题框需要使用已保存的题号和评分依据。返回第 4 步保存后再继续。</p>
      <button type="button" @click="router.push('/sessions')">返回评分依据</button>
    </section>
    <TemplateUploadPanel v-else :template="null" :status="store.uploadState"
      @upload="replaceTemplate" @reconcile="store.reconcileUpload" />
  </article>
</template>
