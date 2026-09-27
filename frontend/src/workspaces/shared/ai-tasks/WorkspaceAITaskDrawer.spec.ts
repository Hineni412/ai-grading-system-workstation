import { createApp, defineComponent, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse } from '../../../api/jobs'
import { useJobStore } from '../../../stores/jobs'
import WorkspaceAITaskDrawer from './WorkspaceAITaskDrawer.vue'

function job(
  status: JobResponse['status'],
  progress: number,
  jobType = 'grading_run',
  detail = '',
): JobResponse {
  return {
    id: 71,
    job_type: jobType,
    payload: {},
    result: {},
    status,
    progress,
    stage: '',
    detail,
    error: status === 'failed' ? 'safe failure' : null,
    cancel_requested: false,
    created_at: '2026-08-08T00:00:00Z',
    started_at: '2026-08-08T00:00:01Z',
    updated_at: `2026-08-08T00:00:0${status === 'running' ? '2' : '3'}Z`,
    finished_at: status === 'running' ? null : '2026-08-08T00:00:03Z',
  }
}

async function mountDrawer() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: defineComponent({ template: '<div />' }) }],
  })
  await router.push('/')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(WorkspaceAITaskDrawer)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  await nextTick()
  return { app, host, jobs: useJobStore() }
}

async function openDrawer(host: HTMLElement): Promise<void> {
  host.querySelector<HTMLButtonElement>('.workspace-ai-drawer-toggle')!.click()
  await nextTick()
}

beforeEach(() => {
  document.body.innerHTML = ''
})

afterEach(() => {
  vi.useRealTimers()
})

describe('WorkspaceAITaskDrawer', () => {

  it('keeps a failed job in 需要处理 and names unknown types in Chinese', async () => {
    const { app, host, jobs } = await mountDrawer()
    jobs.track(job('failed', 0.35, 'question_bank_sync', '同步中断'))
    await nextTick()
    await openDrawer(host)

    expect(document.body.querySelector('.workspace-ai-task-peek')).toBeNull()
    expect(document.body.textContent).toContain('需要处理')
    expect(document.body.textContent).toContain('题库同步')
    expect(document.body.textContent).toContain('失败')
    expect(document.body.textContent).toContain('35% · 同步中断')
    expect(document.body.textContent).not.toContain('后台处理任务')
    expect(document.body.querySelector('.workspace-ai-task-drawer__archive')).toBeNull()
    app.unmount()
  })

})
