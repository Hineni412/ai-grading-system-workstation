import { createPinia, setActivePinia } from 'pinia'
import { createApp, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  LessonNode,
  MaterialVersion,
  SemesterMappingProposal,
  SemesterMaterialRecord,
} from '../../api/catalog'
import { teachingPrepCatalogApi } from '../../api/catalog'
import type { WorkspaceAITask } from '../../../shared/ai-tasks/contracts'
import { useWorkspaceAITaskStore } from '../../../shared/ai-tasks/store'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import MaterialCorrespondence from './MaterialCorrespondence.vue'

const semesterId = 's'.repeat(32)
const lessonId = '1'.repeat(32)

function material(id: string, name: string): MaterialVersion {
  return {
    id, source_id: id, display_name: name, material_type: 'pdf',
    content_sha256: id[0]!.repeat(64), safe_filename: `${id[0]}.pdf`, size_bytes: 20,
    modified_ns: null, unit_count: 2, inspection_status: 'ready',
    availability: 'available', created_at: '2026-08-03T00:00:00Z',
  }
}

function record(
  id: string,
  item: MaterialVersion,
  role: SemesterMaterialRecord['material_role'] = 'textbook',
): SemesterMaterialRecord {
  return {
    id, semester_id: semesterId, material_source_id: item.source_id,
    display_name: item.display_name, material_role: role, parse_status: 'parsed',
    mapping_status: 'unmapped', current_material_version_id: item.id,
    safe_filename: item.safe_filename, current_inspection_status: 'ready',
    current_unit_count: 2, last_parsed_version_id: item.id, has_unparsed_update: false,
    parsed_at: '2026-08-03T00:00:00Z', is_active: true, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function lesson(id: string): LessonNode {
  return {
    id, curriculum_id: 'c'.repeat(32), parent_id: null, node_type: 'lesson',
    title: '第一课时', sort_order: 1, duration_minutes: 45, source_kind: 'teacher',
    is_active: true, revision: 1, created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
  }
}

function bookProposal(
  recordId: string,
  decision: 'pending' | 'accepted' = 'pending',
  uncertainties: string[] = [],
): SemesterMappingProposal {
  return {
    id: 'p'.repeat(32), semester_id: semesterId, operation_id: 'operation-book',
    source_state_sha256: 'f'.repeat(64), status: 'proposed',
    payload: {
      tree: [], uncertainties, source_material_record_ids: [recordId],
      mappings: [{
        mapping_id: 'mapping-1', material_record_id: recordId,
        lesson_ref: lessonId, start_unit: 1, end_unit: 2, purpose: 'textbook',
        decision, teacher_revision: null, decision_reason: null,
        basis: '依据目录页码提出。',
      }],
    },
    revision: 1, created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z', applied_at: null,
  }
}

const textbook = material('a'.repeat(32), '教材.pdf')
const textbookRecord = record('r'.repeat(32), textbook)

interface MountOptions {
  proposals?: SemesterMappingProposal[]
  withLesson?: boolean
  role?: SemesterMaterialRecord['material_role']
}

async function mountPanel(options: MountOptions = {}) {
  const catalog = useTeachingPrepCatalogStore()
  const currentRecord = options.role
    ? record(textbookRecord.id, textbook, options.role)
    : textbookRecord
  catalog.materials = [textbook]
  catalog.semesterMaterials = [currentRecord]
  catalog.semesterMappingProposals = options.proposals ?? []
  catalog.lessonNodes = options.withLesson === false ? [] : [lesson(lessonId)]
  vi.spyOn(teachingPrepCatalogApi, 'listReferencePptCollections').mockResolvedValue([])
  vi.spyOn(catalog, 'openMaterial').mockResolvedValue('loaded')
  vi.spyOn(catalog, 'prepareSemesterMapping').mockResolvedValue(undefined)

  const notices: string[] = []
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(MaterialCorrespondence, {
    materialId: textbook.id,
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

describe('MaterialCorrespondence', () => {
  it('renders mapping rows with a waiting-for-teacher badge', async () => {
    const { app, host } = await mountPanel({
      proposals: [bookProposal(textbookRecord.id)],
    })

    expect(host.textContent).toContain('1—2 页/张')
    expect(host.textContent).toContain('待你确认')
    expect(host.textContent).toContain('依据目录页码提出。')
    app.unmount()
  })

  it('accepts a mapping through the workbench review API', async () => {
    const updated = bookProposal(textbookRecord.id, 'accepted')
    const review = vi.spyOn(teachingPrepWorkbenchApi, 'reviewMapping')
      .mockResolvedValue(updated as never)
    const { app, host } = await mountPanel({
      proposals: [bookProposal(textbookRecord.id)],
    })

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '接受')?.click()
    await vi.waitFor(() => expect(review).toHaveBeenCalled())
    expect(review.mock.calls[0]?.[2]).toMatchObject({
      decision: 'accepted',
      lesson_ref: lessonId,
      start_unit: 1,
      end_unit: 2,
    })
    app.unmount()
  })

  it('blocks applying while any mapping is still pending', async () => {
    const { app, host, catalog } = await mountPanel({
      proposals: [bookProposal(textbookRecord.id)],
    })
    const apply = vi.spyOn(catalog, 'applySemesterMapping').mockResolvedValue(undefined)

    host.querySelector<HTMLButtonElement>('[data-testid="apply-book-mapping"]')?.click()
    await nextTick()
    expect(apply).not.toHaveBeenCalled()
    app.unmount()
  })

  it('applies the proposal through the store once every mapping is decided', async () => {
    const { app, host, catalog } = await mountPanel({
      proposals: [bookProposal(textbookRecord.id, 'accepted')],
    })
    const apply = vi.spyOn(catalog, 'applySemesterMapping').mockResolvedValue(undefined)
    vi.spyOn(catalog, 'load').mockResolvedValue()

    host.querySelector<HTMLButtonElement>('[data-testid="apply-book-mapping"]')?.click()
    await vi.waitFor(() => expect(apply).toHaveBeenCalled())
    expect(apply.mock.calls[0]?.[1]).toBeUndefined()
    app.unmount()
  })

  it('keeps the AI entry collapsed and prepares the scope only when opened', async () => {
    const { app, host, catalog } = await mountPanel()
    const details = host.querySelector<HTMLDetailsElement>('[data-testid="ai-reinfer"]')

    expect(details?.open).toBe(false)
    expect(details?.textContent).toContain('让 AI 重新推断对应关系')
    expect(catalog.prepareSemesterMapping).not.toHaveBeenCalled()

    details?.querySelector('summary')?.click()
    await vi.waitFor(() => expect(catalog.prepareSemesterMapping).toHaveBeenCalled())
    expect(details?.textContent).toContain('单次调用、按次计费')
    app.unmount()
  })

  it('asks the teacher to confirm the lesson tree before calling AI', async () => {
    const { app, host } = await mountPanel({ withLesson: false })

    host.querySelector<HTMLDetailsElement>('[data-testid="ai-reinfer"] summary')?.click()
    await nextTick()
    expect(host.textContent).toContain('请先在资料柜底部确认本学期课时树')
    app.unmount()
  })

  it('lists unmatched page ranges for exercise workbooks', async () => {
    const { app, host } = await mountPanel({
      role: 'exercise_workbook',
      proposals: [bookProposal(textbookRecord.id, 'accepted', ['专题：勾股定理中的动点问题 P135-140'])],
    })

    expect(host.textContent).toContain('未对应页段')
    expect(host.textContent).toContain('专题：勾股定理中的动点问题 P135-140')
    app.unmount()
  })

  it('shows the teacher-readable failure detail from the AI task', async () => {
    const curriculumId = 'c'.repeat(32)
    vi.spyOn(teachingPrepCatalogApi, 'listReferencePptCollections').mockResolvedValue([])
    const catalog = useTeachingPrepCatalogStore()
    catalog.curricula = [{ id: curriculumId } as never]
    catalog.semesters = [{ id: semesterId, curriculum_id: curriculumId } as never]
    catalog.selectedCurriculumId = curriculumId
    catalog.selectedSemesterId = semesterId
    const failedTask: WorkspaceAITask = {
      contract_version: 'teacher_workspace_ai_task.v1',
      task_id: 'task-failed-mapping',
      operation_id: 'operation-failed-mapping',
      module: 'teaching_prep',
      task_kind: 'teaching_prep.semester_mapping',
      source_ref: { kind: 'semester', id: semesterId, revision: 'f'.repeat(64) },
      context_refs: [
        { kind: 'material', id: textbookRecord.id, revision: String(textbookRecord.revision) },
      ],
      return_target: 'teaching_prep.library',
      status: 'failed',
      phase: 'finished',
      progress: 1,
      send_attempt_count: 1,
      dispatch_evidence: 'response_persisted',
      cancel_requested: false,
      job_id: 1,
      proposal_ref_id: null,
      proposal_revision: null,
      error_code: 'semester_mapping_unexplained_coverage_gap',
      error_detail: '资料中有页面既没有对应到课时，模型也没有说明原因，本次整理没有产出结果。',
      revision: 3,
      safe_title: '备课 · 整理学期资料',
      safe_source: '备课',
      teacher_message: '模型已返回或调用已结束，但任务没有完成。',
      next_action: '查看说明后新建一次操作',
      handoffs: [],
      handoff_total: 0,
      adopted_count: 0,
      discarded_count: 0,
      stale_count: 0,
      pending_count: 0,
      created_at: '2026-08-03T00:00:00Z',
      updated_at: '2026-08-03T00:01:00Z',
      finished_at: '2026-08-03T00:01:00Z',
    }
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.tasks = { [failedTask.task_id]: failedTask }

    const { app, host } = await mountPanel()

    expect(host.textContent).toContain(
      '资料中有页面既没有对应到课时，模型也没有说明原因，本次整理没有产出结果。',
    )
    app.unmount()
  })
})
