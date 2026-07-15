<script setup lang="ts">
import ConfigStageRail from '../components/config/ConfigStageRail.vue'
import ConfigSourceUpload from '../components/config/ConfigSourceUpload.vue'
import ConfigGenerationPanel from '../components/config/ConfigGenerationPanel.vue'
import QuestionBlockReview from '../components/config/QuestionBlockReview.vue'
import SessionDraftPanel from '../components/config/SessionDraftPanel.vue'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useSessionStore } from '../stores/session'

const sessionStore = useSessionStore()
const configStore = useConfigWorkspaceStore()

function confirmSourceUpload(): boolean {
  if (!configStore.hasDirtyEditor) return true
  return window.confirm('替换试卷会在新文件接收成功后清除尚未保存的评分依据修改。是否继续？')
}
</script>

<template>
  <article class="session-config-view config-workspace">
    <header class="session-config-view__header">
      <div>
        <h1 tabindex="-1">考试配置</h1>
        <p>从考试草稿开始，按阶段准备试卷来源和评分依据。</p>
      </div>
      <span v-if="sessionStore.currentSession" class="session-config-view__current">
        当前考试：{{ sessionStore.currentSession.name }}
      </span>
    </header>

    <div v-if="sessionStore.loadState === 'loading'" class="session-config-view__state" role="status">
      正在读取考试列表…
    </div>
    <div v-else-if="sessionStore.loadState === 'error'" class="session-config-view__state" role="alert">
      <p>考试列表暂时无法读取，尚未改变任何考试。</p>
      <button type="button" @click="sessionStore.initialize()">重新加载考试列表</button>
    </div>
    <template v-else>
      <ConfigStageRail
        :phase="configStore.phase"
        :session-ready="sessionStore.currentSession !== null"
        :source-ready="configStore.sourceId !== null && configStore.sourceRevision !== null"
        :generation-submitted="configStore.jobId !== null"
        :editor-ready="configStore.editor?.configured === true"
      />
      <p v-if="sessionStore.sessions.length === 0" class="session-config-view__empty">
        还没有考试。创建草稿后，可以继续上传试卷并准备评分依据。
      </p>
      <SessionDraftPanel />
      <template v-if="sessionStore.currentSession">
        <ConfigSourceUpload
          :session-id="sessionStore.currentSession.id"
          :source="configStore.source"
          :before-upload="confirmSourceUpload"
          @uploaded="configStore.acceptUploadedSource"
        />
        <QuestionBlockReview
          v-if="configStore.source"
          :source="configStore.source"
          :decisions="configStore.decisions"
          @update:decisions="configStore.updateDecisions"
        />
        <ConfigGenerationPanel v-if="configStore.source" />
      </template>
    </template>
  </article>
</template>

<style scoped>
.session-config-view__header { display: flex; align-items: end; justify-content: space-between; gap: var(--space-5); margin-block-end: var(--space-5); }
.session-config-view h1,
.session-config-view p { margin: 0; }
.session-config-view h1 { font-size: var(--font-size-h1); line-height: var(--line-height-tight); }
.session-config-view__header p { margin-block-start: var(--space-1); color: var(--color-text-secondary); }
.session-config-view__current { max-width: 45%; overflow: hidden; color: var(--color-text-secondary); font-size: var(--font-size-dense); text-overflow: ellipsis; white-space: nowrap; }
.session-config-view__state,
.session-config-view__empty { padding: var(--space-5); border-block: var(--border-width) solid var(--color-border-default); background: var(--color-bg-subtle); color: var(--color-text-secondary); }
.session-config-view__state button { min-height: var(--control-height-default); margin-block-start: var(--space-3); padding-inline: var(--space-3); border: var(--border-width) solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); }
.session-config-view__empty { border-block-start: 0; }
</style>
