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
  it('does not show a floating peek card while a job runs', async () => {
    const { app, host, jobs } = await mountDrawer()
    jobs.track(job('running', 0.35, 'question_bank_sync', '正在写入题目'))
    await nextTick()

    expect(host.querySelector('.workspace-ai-task-peek')).toBeNull()
    expect(host.querySelector('.workspace-ai-drawer-toggle')?.textContent).toContain('任务中心')
    expect(host.querySelector('.workspace-ai-drawer-toggle')?.textContent).toContain('35%')
    app.unmount()
  })

  it('does not repeat tagging progress as a percentage in the task center', async () => {
    const { app, host, jobs } = await mountDrawer()
    jobs.track(job('running', 0.82, 'tagging_sync', 'AI 分析已处理 16/20 道题'))
    await nextTick()

    expect(host.querySelector('.workspace-ai-drawer-toggle')?.textContent).toContain('任务中心')
    expect(host.querySelector('.workspace-ai-drawer-toggle')?.textContent).not.toContain('82%')
    await openDrawer(host)
    expect(host.textContent).toContain('题库分析进行中，点这里回到试卷库')
    expect(host.textContent).toContain('回到试卷库')
    expect(host.querySelector('progress')).toBeNull()
    app.unmount()
  })

  it('keeps a failed job in 需要处理 and names unknown types in Chinese', async () => {
    const { app, host, jobs } = await mountDrawer()
    jobs.track(job('failed', 0.35, 'question_bank_sync', '同步中断'))
    await nextTick()
    await openDrawer(host)

    expect(host.querySelector('.workspace-ai-task-peek')).toBeNull()
    expect(host.textContent).toContain('需要处理')
    expect(host.textContent).toContain('题库同步')
    expect(host.textContent).toContain('失败')
    expect(host.textContent).toContain('35% · 同步中断')
    expect(host.textContent).not.toContain('后台处理任务')
    expect(host.querySelector('.workspace-ai-task-drawer__archive')).toBeNull()
    app.unmount()
  })

  it('archives a succeeded job under 最近完成', async () => {
    const { app, host, jobs } = await mountDrawer()
    jobs.track(job('succeeded', 1))
    await nextTick()
    await openDrawer(host)

    expect(host.querySelector('.workspace-ai-task-drawer__timeline')).toBeNull()
    expect(host.querySelector('.workspace-ai-task-drawer__archive')?.textContent).toContain('最近完成 1')
    expect(host.querySelector('.workspace-ai-task-drawer__archive')?.textContent).toContain('考试批改')
    app.unmount()
  })
})
