<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import TemplateRegionEditor from '../components/template-regions/TemplateRegionEditor.vue'
import TemplateUploadPanel from '../components/template-regions/TemplateUploadPanel.vue'
import { useTemplateRegionStore } from '../stores/template-regions'
import type { PageRole } from '../api/template-regions'

const route = useRoute()
const router = useRouter()
const store = useTemplateRegionStore()
const routeSessionId = computed(() => Number(route.params.sessionId))
const pageCounts = computed(() => ({
  front: store.editorState.regions.filter((item) => item.page === 'front').length,
  back: store.editorState.regions.filter((item) => item.page === 'back').length,
}))

function loadRoute(): void {
  if (Number.isSafeInteger(routeSessionId.value) && routeSessionId.value > 0) {
    void store.load(routeSessionId.value)
  }
}
async function finish(): Promise<void> {
  if (!window.confirm('确认将当前题框保存为正式版本？保存后将进入只读查看。')) return
  await store.commit()
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

onMounted(() => { loadRoute(); window.addEventListener('beforeunload', beforeUnload) })
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => confirmLeave())
watch(routeSessionId, loadRoute)
</script>

<template>
  <article class="template-regions-view">
    <header class="template-regions-view__header">
      <div>
        <span class="template-regions__eyebrow">考试配置 · 第 5 步</span>
        <h1>样卷题框标定</h1>
        <p>在样卷原图上圈出每道题的作答区域，并绑定题号。</p>
      </div>
      <button type="button" class="secondary" @click="router.push('/sessions')">返回考试配置</button>
    </header>

    <div v-if="store.loadState === 'loading'" class="template-regions-view__state" role="status">正在读取样卷工作区…</div>
    <div v-else-if="store.loadState === 'error'" class="template-regions-view__state" role="alert">
      <p>{{ store.errorMessage }}</p>
      <button type="button" @click="store.load(routeSessionId)">重新读取</button>
    </div>
    <template v-else-if="store.workspace">
      <div class="template-regions-view__ruler" :data-state="store.readOnly ? 'confirmed' : 'draft'">
        <strong>{{ store.readOnly ? '正式版本 · 只读' : '草稿标定中' }}</strong>
        <span>{{ store.saveState === 'saving' ? '正在保存草稿' : store.saveState === 'saved' ? '草稿已保存' : '按原图像素记录' }}</span>
        <span v-if="store.snapshotPending">正式区域已保存，确认快照待补写</span>
        <div v-if="store.editorReady" class="template-regions-view__faces" aria-label="正反面题框状态">
          <button type="button" class="secondary" @click="store.showPage('front')">正面 {{ pageCounts.front }} 框</button>
          <button type="button" class="secondary" @click="store.showPage('back')">反面 {{ pageCounts.back }} 框</button>
        </div>
      </div>
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
      <TemplateUploadPanel
        :template="store.workspace.template"
        :status="store.uploadState"
        @upload="replaceTemplate"
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
        <button type="button" @click="store.retrySnapshot">重试生成确认快照</button>
      </div>
      <div v-else-if="store.readOnly" class="template-regions-view__snapshot">
        <p>样卷题框已确认，已具备进入 P2-11“整班答卷上传与扫描预检”的前置条件。</p>
        <button type="button" class="secondary" @click="store.startEditingConfirmed">重新编辑题框草稿</button>
      </div>
    </template>
    <section v-else-if="!store.scoringConfigured" class="template-regions-view__prerequisite">
      <h2>请先完成评分依据</h2>
      <p>样卷题框需要使用已保存的题号和评分依据。返回第 4 步保存后再继续。</p>
      <button type="button" @click="router.push('/sessions')">返回评分依据</button>
    </section>
    <TemplateUploadPanel v-else :template="null" :status="store.uploadState"
      @upload="replaceTemplate" @reconcile="store.reconcileUpload" />
  </article>
</template>
