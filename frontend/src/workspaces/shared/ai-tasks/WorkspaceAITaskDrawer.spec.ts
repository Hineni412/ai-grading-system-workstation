import { createApp, defineComponent, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse } from '../../../api/jobs'
import { useJobStore } from '../../../stores/jobs'
import WorkspaceAITaskDrawer from './WorkspaceAITaskDrawer.vue'

function job(status: JobResponse['status'], progress: number): JobResponse {
  return {
    id: 71,
    job_type: 'grading_run',
    payload: {},
    result: {},
    status,
    progress,
    stage: '',
    detail: '',
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

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  document.body.innerHTML = ''
})

afterEach(() => {
  vi.useRealTimers()
})

describe('WorkspaceAITaskDrawer completion peek', () => {
  it('automatically minimizes a successful task after it reaches 100%', async () => {
    const { app, host, jobs } = await mountDrawer()
    jobs.track(job('running', 0.8))
    await nextTick()
    expect(host.querySelector('.workspace-ai-task-peek')).not.toBeNull()

    jobs.track(job('succeeded', 1))
    await nextTick()
    await vi.advanceTimersByTimeAsync(999)
    expect(host.querySelector('.workspace-ai-task-peek')).not.toBeNull()
    await vi.advanceTimersByTimeAsync(1)
    await nextTick()
    expect(host.querySelector('.workspace-ai-task-peek')).toBeNull()
    expect(host.querySelector('.workspace-ai-drawer-toggle')?.textContent).toContain('任务中心')
    app.unmount()
  })

  it.each(['failed', 'cancelled'] as const)(
    'keeps a %s task visible for teacher action',
    async (status) => {
      const { app, host, jobs } = await mountDrawer()
      jobs.track(job('running', 0.8))
      await nextTick()
      jobs.track(job(status, 1))
      await nextTick()
      await vi.advanceTimersByTimeAsync(2_000)
      await nextTick()
      expect(host.querySelector('.workspace-ai-task-peek')).not.toBeNull()
      app.unmount()
    },
  )
})
