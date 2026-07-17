import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api/template-regions'
import { createAppRouter } from '../router'
import TemplateRegionView from '../views/TemplateRegionView.vue'

vi.mock('../api/template-regions', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/template-regions')>(),
  fetchRegionReadiness: vi.fn(), fetchRegionWorkspace: vi.fn(), saveRegionDraft: vi.fn(), uploadTemplate: vi.fn(),
  fetchTemplateSubmission: vi.fn(), abandonTemplateSubmission: vi.fn(),
  discardRegionDraft: vi.fn(), commitRegions: vi.fn(), retryRegionSnapshot: vi.fn(),
}))

function confirmedWorkspace(): api.RegionWorkspace {
  return {
    session_id: 7,
    template: {
      session_id: 7, template_id: 3, template_fingerprint: 'a'.repeat(64),
      first_page_role: 'front', pages: {
        front: { url: '/api/sessions/7/template/pages/front', width: 1000, height: 1400 },
        back: { url: '/api/sessions/7/template/pages/back', width: 1000, height: 1400 },
      }, is_confirmed: true, regions_snapshot_pending: true,
    },
    formal_regions: [{ region_uuid: 'r1', page: 'front', region_order: 1,
      x: 10, y: 20, w: 100, h: 80, mapped_question_id: 'Q1', mapping_status: 'manual',
      is_confirmed: true, multi_region_confirmed: false }],
    draft: { status: 'missing', revision: 0, regions: [] }, automatic_candidates: ['Q1'],
    manual_question_options: [{ value: 'Q1', label: 'Q1' }], issues: [], template_ready: true,
  }
}

function editableWorkspace(): api.RegionWorkspace {
  const value = confirmedWorkspace()
  value.template.is_confirmed = false
  value.template.regions_snapshot_pending = false
  value.template_ready = false
  value.formal_regions = [
    { ...value.formal_regions[0]!, is_confirmed: false },
    { ...value.formal_regions[0]!, region_uuid: 'r2', page: 'back', region_order: 2,
      mapped_question_id: null, mapping_status: 'unbound', is_confirmed: false },
  ]
  value.issues = [{ code: 'unbound_question', message: '待绑定', region_uuid: 'r2',
    question_id: null }]
  return value
}

async function mountView() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/sessions/7/regions')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(TemplateRegionView)
  app.use(createPinia())
  app.use(router)
  app.mount(host)
  await Promise.resolve(); await nextTick(); await Promise.resolve(); await nextTick()
  return { app, host }
}

beforeEach(() => {
  document.body.innerHTML = ''; vi.clearAllMocks()
  vi.mocked(api.fetchRegionReadiness).mockResolvedValue({
    session_id: 7, scoring_configured: true, template_present: true,
    template_ready: false,
  })
})

describe('TemplateRegionView', () => {
  it('offers the first upload when the session has no template yet', async () => {
    vi.mocked(api.fetchRegionReadiness).mockResolvedValue({
      session_id: 7, scoring_configured: true, template_present: false,
      template_ready: false,
    })
    const { app, host } = await mountView()

    expect(host.textContent).toContain('上传双页样卷')
    expect(host.querySelector<HTMLInputElement>('input[type="file"]')).not.toBeNull()
    app.unmount()
  })

  it('blocks upload until the scoring configuration is saved', async () => {
    vi.mocked(api.fetchRegionReadiness).mockResolvedValue({
      session_id: 7, scoring_configured: false, template_present: false,
      template_ready: false,
    })
    const { app, host } = await mountView()

    expect(host.textContent).toContain('请先完成评分依据')
    expect(host.querySelector<HTMLInputElement>('input[type="file"]')).toBeNull()
    app.unmount()
  })

  it('shows a confirmed snapshot-pending workspace as read-only', async () => {
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(confirmedWorkspace())
    const { app, host } = await mountView()

    expect(host.textContent).toContain('正式版本 · 只读')
    expect(host.textContent).toContain('重试生成确认快照')
    expect(host.querySelector<HTMLButtonElement>('[data-action="finish"]')?.disabled).toBe(true)
    expect(host.querySelectorAll('[data-region-uuid]')).toHaveLength(1)
    app.unmount()
  })

  it('summarizes both pages, bindings and issues before formal commit', async () => {
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(editableWorkspace())
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { app, host } = await mountView()

    host.querySelector<HTMLButtonElement>('[data-action="finish"]')!.click()
    await nextTick()

    expect(confirm).toHaveBeenCalledWith(
      '正面 1 框，反面 1 框；已绑定 1 框，待处理 1 框，校验问题 1 项。确认保存为正式版本？',
    )
    expect(api.commitRegions).not.toHaveBeenCalled()
    app.unmount()
  })
})
