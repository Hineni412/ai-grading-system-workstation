import { createPinia } from 'pinia'
import { createApp, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import * as aiAdoption from '../aiAdoption'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'
import { teachingPrepWorkbenchKey } from '../workbench/context'
import PresentationVersionsWorkspace from './PresentationVersionsWorkspace.vue'

const operation = (
  id: string,
  page: number,
  mode: 'automatic' | 'manual_only',
  position: Record<string, number> | null,
  signature = `slide-${page}`,
  unitId?: string,
) => ({
  operation_id: id,
  kind: mode === 'manual_only' ? 'manual_note' : 'add_text_box',
  decision: 'proposed' as const,
  target: {
    generated_page_number: page,
    slide_signature: signature,
    ...(unitId ? { material_unit_id: unitId } : {}),
    position,
  },
  reason: `修改第 ${page} 页`, citations: [], planned_minutes: 0,
  risk: mode === 'manual_only' ? 'blocked' as const : 'low' as const,
  execution_mode: mode, support_note: null, teacher_note: null, details: {},
})

function mountChecker(options: {
  operations?: ReturnType<typeof operation>[]
  before?: Array<Record<string, unknown>>
} = {}) {
  const plan = {
    id: 'plan-1', lesson_draft_id: 'draft-1', resource_pack_id: 'pack-1', version_number: 1,
    based_on_plan_id: null, source_ppt_state_sha256: 'a'.repeat(64), status: 'in_review' as const,
    payload: { schema_version: 1, source_presentations: [], slides: [], unsupported_objects: [], approval_history: [], operations: options.operations ?? [
      operation('op-page-2', 2, 'automatic', { x: .2, y: .3, width: .4, height: .2 }),
      operation('op-manual', 1, 'manual_only', null),
    ] }, created_at: '',
  }
  const fake = {
    stage: ref('slides'),
    catalog: {
      slidePlans: [plan], selectedSlidePlanId: 'plan-1',
      slidePlanPreview: { valid_for_execution: true, source_changed: false, includes_proposed_operations: true, before_slide_count: 2, after_slide_count: 2,
        before: options.before ?? [
          { stable_signature: 'slide-1', original_index: 1, title: '第一页', preview_url: '/preview/1' },
          { stable_signature: 'slide-2', original_index: 2, title: '第二页', preview_url: '/preview/2' },
        ], after: [], changes: [], manual_only: [] },
      pptxExecutions: [], saveState: 'idle', upClassPackages: [],
      selectSlidePlan: vi.fn(), reviewSlidePlan: vi.fn(),
      executePptx: async () => undefined, createUpClassPackage: async () => undefined,
      activateUpClassPackage: async () => undefined, recoverUpClassPackage: async () => undefined,
      cancelPptxExecution: async () => undefined, recoverPptxExecution: async () => undefined,
      discardPptxStaging: async () => undefined,
    },
    pptxVersions: ref([]), setDirty: () => undefined, refreshCurrentWorkspace: async () => undefined,
    openStage: vi.fn(),
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(PresentationVersionsWorkspace)
  const pinia = createPinia()
  app.use(pinia)
  app.provide(teachingPrepWorkbenchKey, fake as never)
  app.mount(host)
  return { app, host, fake, pinia }
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('PresentationVersionsWorkspace R7 checker', () => {
  it('selects the operation source slide and scales only a validated target.position', async () => {
    const { app, host } = mountChecker()
    await nextTick()

    expect(host.querySelectorAll('.tp-slide-checker__thumbs button')[1]?.classList.contains('is-active')).toBe(true)
    const overlay = host.querySelector<HTMLElement>('.tp-change-overlay')
    expect(overlay?.style.left).toBe('20%')
    expect(overlay?.style.width).toBe('40%')
    app.unmount()
  })

  it('never draws a fake box or offers automatic acceptance for manual_only', async () => {
    const { app, host } = mountChecker()
    const selectors = host.querySelectorAll<HTMLButtonElement>('.tp-operation-row__selector')
    selectors[1]?.click()
    await nextTick()

    expect(host.querySelector('.tp-change-overlay')).toBeNull()
    expect(host.textContent).toContain('只能由教师人工处理')
    const active = host.querySelector('.tp-operation-row.is-active')
    expect(active?.textContent).toContain('标记为人工处理')
    expect(active?.textContent).not.toContain('接受')
    app.unmount()
  })

  it('hides the selected operation overlay after the teacher opens a different thumbnail', async () => {
    const { app, host } = mountChecker()
    await nextTick()
    expect(host.querySelector('.tp-change-overlay')).not.toBeNull()

    host.querySelectorAll<HTMLButtonElement>('.tp-slide-checker__thumbs button')[0]?.click()
    await nextTick()

    expect(host.querySelectorAll('.tp-slide-checker__thumbs button')[0]?.classList.contains('is-active')).toBe(true)
    expect(host.querySelector('.tp-change-overlay')).toBeNull()
    app.unmount()
  })

  it('prefers the stable slide signature when source PPTs share a page number', async () => {
    const { app, host } = mountChecker({
      operations: [
        operation('op-ppt-b', 1, 'automatic', { x: .1, y: .1, width: .2, height: .2 }, 'sig-b-1', 'unit-b-1'),
      ],
      before: [
        { source_link_id: 'ppt-a', material_unit_id: 'unit-a-1', stable_signature: 'sig-a-1', original_index: 1, title: 'A 第一页', preview_url: '/preview/a/1' },
        { source_link_id: 'ppt-b', material_unit_id: 'unit-b-1', stable_signature: 'sig-b-1', original_index: 1, title: 'B 第一页', preview_url: '/preview/b/1' },
      ],
    })
    await nextTick()

    expect(host.querySelectorAll('.tp-slide-checker__thumbs button')[1]?.classList.contains('is-active')).toBe(true)
    expect(host.querySelector<HTMLImageElement>('.tp-slide-stage img')?.getAttribute('src')).toBe('/preview/b/1')
    app.unmount()
  })

  it.each([
    ['AI proposal', true],
    ['manual plan', false],
  ])('routes %s review through the correct adoption path', async (_label, isAI) => {
    const adoption = vi.spyOn(aiAdoption, 'adoptTeachingPrepProposal')
      .mockResolvedValue(isAI ? ({
        match: { task: { task_id: 'task-slide' } },
        receipt: { object_id: 'reviewed-plan' },
      } as never) : null)
    const { app, host, fake, pinia } = mountChecker()
    if (isAI) {
      vi.spyOn(useWorkspaceAITaskStore(pinia), 'refresh')
        .mockRejectedValue(new Error('synthetic task refresh failure'))
    }
    await nextTick()
    for (const input of host.querySelectorAll<HTMLInputElement>(
      '.tp-operation-row input[type="radio"]:first-of-type',
    )) {
      input.click()
    }
    await nextTick()
    const save = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(item => item.textContent?.includes('保存全部审核决定'))
    save?.click()
    await vi.waitFor(() => expect(adoption).toHaveBeenCalled())

    if (isAI) {
      expect(fake.catalog.reviewSlidePlan).not.toHaveBeenCalled()
      await vi.waitFor(() => {
        expect(host.textContent).toContain('课件审核已保存并生成采用回执')
        expect(host.textContent).not.toContain('课件审核尚未确认')
      })
    } else expect(fake.catalog.reviewSlidePlan).toHaveBeenCalledOnce()
    app.unmount()
  })
})
