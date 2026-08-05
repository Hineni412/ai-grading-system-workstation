<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useWorkspaceAITaskStore } from './store'
import {
  canCancelTask,
  cancelTaskLabel,
  dispatchEvidenceLabel,
  handoffSummary,
  returnLocation,
} from './taskPresentation'
import type { WorkspaceAITask } from './contracts'

const store = useWorkspaceAITaskStore()
const router = useRouter()
const open = ref(false)

async function returnToTask(task: WorkspaceAITask): Promise<void> {
  open.value = false
  await router.push(returnLocation(task))
}
</script>

<template>
  <button
    class="workspace-ai-drawer-toggle"
    type="button"
    :aria-expanded="open"
    aria-controls="workspace-ai-task-drawer"
    @click="open = !open"
  >
    AI 任务<span v-if="store.activeCount">{{ store.activeCount }}</span>
  </button>

  <aside
    v-if="open"
    id="workspace-ai-task-drawer"
    class="workspace-ai-task-drawer"
    aria-label="教师工作台 AI 任务"
  >
    <header>
      <div><strong>AI 任务</strong><span>只显示安全状态，不显示业务正文</span></div>
      <button type="button" aria-label="关闭 AI 任务" @click="open = false">×</button>
    </header>
    <p v-if="store.orderedTasks.length === 0" class="workspace-ai-task-drawer__empty">
      暂无可恢复的工作台 AI 任务。
    </p>
    <article v-for="task in store.orderedTasks" :key="task.task_id">
      <div class="workspace-ai-task-drawer__heading">
        <strong>{{ task.safe_title }}</strong><span>{{ task.safe_source }}</span>
      </div>
      <progress :value="task.progress" max="1" :aria-label="`${task.safe_title}进度`" />
      <p>{{ task.teacher_message }}</p>
      <ul>
        <li>{{ dispatchEvidenceLabel(task) }}</li>
        <li>{{ handoffSummary(task) }}</li>
        <li>{{ task.next_action }}</li>
      </ul>
      <p v-if="store.syncErrors[task.task_id]" role="status">
        {{ store.syncErrors[task.task_id] }}
      </p>
      <footer>
        <button type="button" @click="returnToTask(task)">返回原页</button>
        <button
          v-if="canCancelTask(task)"
          type="button"
          @click="store.cancel(task.task_id)"
        >
          {{ cancelTaskLabel(task) }}
        </button>
        <button v-else type="button" @click="store.remove(task.task_id)">从列表移除</button>
      </footer>
    </article>
  </aside>
</template>

<style scoped>
.workspace-ai-drawer-toggle{position:fixed;right:20px;bottom:20px;z-index:65;display:flex;align-items:center;gap:8px;min-height:44px;padding:0 16px;border:1px solid var(--color-accent);border-radius:999px;background:var(--color-accent);color:#fff;font:inherit;font-weight:700;box-shadow:var(--shadow-floating)}
.workspace-ai-drawer-toggle span{display:grid;place-items:center;min-width:22px;height:22px;padding:0 6px;border-radius:999px;background:#fff;color:var(--color-accent)}
.workspace-ai-task-drawer{position:fixed;inset:0 0 0 auto;z-index:70;width:min(430px,100vw);overflow:auto;padding:20px;background:var(--color-bg-canvas);border-left:1px solid var(--color-border-default);box-shadow:var(--shadow-floating)}
.workspace-ai-task-drawer>header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;position:sticky;top:-20px;z-index:1;padding:20px 0 14px;background:var(--color-bg-canvas)}
.workspace-ai-task-drawer>header div{display:grid;gap:4px}.workspace-ai-task-drawer>header span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.workspace-ai-task-drawer>header button{border:0;background:transparent;font-size:28px;line-height:1}
.workspace-ai-task-drawer article{display:grid;gap:10px;margin:12px 0;padding:16px;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}
.workspace-ai-task-drawer__heading{display:flex;justify-content:space-between;gap:12px}.workspace-ai-task-drawer__heading span{color:var(--color-text-secondary)}
.workspace-ai-task-drawer progress{width:100%}.workspace-ai-task-drawer p,.workspace-ai-task-drawer ul{margin:0}.workspace-ai-task-drawer ul{padding-left:20px;color:var(--color-text-secondary)}
.workspace-ai-task-drawer footer{display:flex;flex-wrap:wrap;gap:8px}.workspace-ai-task-drawer footer button{min-height:38px;padding:0 12px;border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}.workspace-ai-task-drawer footer button:first-child{border-color:var(--color-accent);color:var(--color-accent)}
.workspace-ai-task-drawer__empty{padding:32px 12px;color:var(--color-text-secondary);text-align:center}
</style>
