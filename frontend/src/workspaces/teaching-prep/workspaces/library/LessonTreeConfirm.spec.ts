import { createPinia, setActivePinia } from 'pinia'
import { createApp, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SemesterMappingProposal } from '../../api/catalog'
import { teachingPrepCatalogApi } from '../../api/catalog'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import LessonTreeConfirm from './LessonTreeConfirm.vue'

const semesterId = 's'.repeat(32)
const recordId = 'r'.repeat(32)

function treeProposal(
  decision: 'pending' | 'accepted' = 'pending',
): SemesterMappingProposal {
  return {
    id: 'p'.repeat(32), semester_id: semesterId, operation_id: 'operation-tree',
    source_state_sha256: 'f'.repeat(64), status: 'proposed',
    payload: {
      tree: [
        {
          key: 'ppt-chapter-1', title: '第一章 勾股定理',
          sections: [{
            key: 'ppt-section-1-1', title: '1.1',
            lessons: [
              { key: 'ppt-lesson-1-1-1', title: '1.1 第1课时 认识勾股定理', duration_minutes: 45 },
              { key: 'ppt-lesson-1-1-2', title: '1.1 第2课时 验证勾股定理', duration_minutes: 45 },
            ],
          }],
        },
        {
          key: 'ppt-chapter-2', title: '第二章 实数',
          sections: [{
            key: 'ppt-section-2-1', title: '2.1',
            lessons: [
              { key: 'ppt-lesson-2-1-1', title: '2.1 认识无理数', duration_minutes: 45 },
            ],
          }],
        },
      ],
      mappings: [
        {
          mapping_id: 'mapping-1', material_record_id: recordId,
          lesson_ref: 'proposal:ppt-lesson-1-1-1', start_unit: 1, end_unit: 26,
          purpose: 'reference_ppt', decision,
          teacher_revision: null, decision_reason: null,
        },
        {
          mapping_id: 'mapping-2', material_record_id: recordId,
          lesson_ref: 'proposal:ppt-lesson-1-1-2', start_unit: 27, end_unit: 51,
          purpose: 'reference_ppt', decision,
          teacher_revision: null, decision_reason: null,
        },
        {
          mapping_id: 'mapping-3', material_record_id: recordId,
          lesson_ref: 'proposal:ppt-lesson-2-1-1', start_unit: 52, end_unit: 75,
          purpose: 'reference_ppt', decision,
          teacher_revision: null, decision_reason: null,
        },
      ],
      uncertainties: [], source_material_record_ids: [recordId],
      generation_source: 'local_reference_ppt_names',
    },
    revision: 1, created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z', applied_at: null,
  }
}

async function mountConfirm(options: { decision?: 'pending' | 'accepted' } = {}) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.semesterMappingProposals = [treeProposal(options.decision ?? 'pending')]
  catalog.lessonNodes = []
  vi.spyOn(teachingPrepCatalogApi, 'listReferencePptCollections').mockResolvedValue([])

  const notices: string[] = []
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(LessonTreeConfirm, {
    onNotice: (message: string) => notices.push(message),
  })
  app.mount(host)
  await nextTick()
  await nextTick()
  return { app, host, catalog, notices }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('LessonTreeConfirm', () => {
  it('renders chapter folders with lesson chips and pending badges', async () => {
    const { app, host } = await mountConfirm()

    expect(host.textContent).toContain('第一章 勾股定理')
    expect(host.textContent).toContain('2 课时')
    expect(host.textContent).toContain('1.1 第1课时 认识勾股定理')
    expect(host.textContent).toContain('待确认')
    expect(host.textContent).toContain('不需要 AI、不产生费用')
    app.unmount()
  })

  it('confirms one chapter by accepting its mappings and applying with chapter_key', async () => {
    const partial = treeProposal()
    partial.payload.mappings[0] = { ...partial.payload.mappings[0]!, decision: 'accepted' }
    const decided = treeProposal('accepted')
    const review = vi.spyOn(teachingPrepWorkbenchApi, 'reviewMapping')
      .mockResolvedValueOnce(partial as never)
      .mockResolvedValue(decided as never)
    const { app, host, catalog } = await mountConfirm()
    const apply = vi.spyOn(catalog, 'applySemesterMapping').mockResolvedValue(undefined)

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-chapter-ppt-chapter-1"]')?.click()
    await vi.waitFor(() => expect(apply).toHaveBeenCalled())

    // 只批量接受第一章的两条映射，第二章保持待决定
    expect(review).toHaveBeenCalledTimes(2)
    expect(review.mock.calls.map(call => call[1])).toEqual(['mapping-1', 'mapping-2'])
    expect(apply.mock.calls[0]?.[1]).toBe('ppt-chapter-1')
    app.unmount()
  })

  it('confirms all chapters sequentially with per-chapter apply', async () => {
    const decided = treeProposal('accepted')
    vi.spyOn(teachingPrepWorkbenchApi, 'reviewMapping')
      .mockResolvedValue(decided as never)
    const { app, host, catalog } = await mountConfirm()
    const apply = vi.spyOn(catalog, 'applySemesterMapping').mockResolvedValue(undefined)

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-all-chapters"]')?.click()
    await vi.waitFor(() => expect(apply).toHaveBeenCalledTimes(2))

    expect(apply.mock.calls.map(call => call[1])).toEqual(['ppt-chapter-1', 'ppt-chapter-2'])
    app.unmount()
  })

  it('applies a chapter directly when its mappings are already decided', async () => {
    const { app, host, catalog } = await mountConfirm({ decision: 'accepted' })
    const review = vi.spyOn(teachingPrepWorkbenchApi, 'reviewMapping')
    const apply = vi.spyOn(catalog, 'applySemesterMapping').mockResolvedValue(undefined)

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-chapter-ppt-chapter-2"]')?.click()
    await vi.waitFor(() => expect(apply).toHaveBeenCalled())

    expect(review).not.toHaveBeenCalled()
    expect(apply.mock.calls[0]?.[1]).toBe('ppt-chapter-2')
    app.unmount()
  })
})
