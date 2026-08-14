import { createPinia, setActivePinia } from 'pinia'
import { createApp, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  LessonNode,
  MaterialUnit,
  MaterialVersion,
  SemesterMappingPreflight,
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

function lesson(id: string, overrides: Partial<LessonNode> = {}): LessonNode {
  return {
    id, curriculum_id: 'c'.repeat(32), parent_id: null, node_type: 'lesson',
    title: '第一课时', sort_order: 1, duration_minutes: 45, source_kind: 'teacher',
    is_active: true, revision: 1, created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
    ...overrides,
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
  units?: MaterialUnit[]
  lessons?: LessonNode[]
}

function pageUnit(idChar: string, index: number): MaterialUnit {
  const id = idChar.repeat(32)
  return {
    id,
    material_version_id: textbook.id,
    unit_kind: 'pdf_page',
    unit_index: index,
    title: null,
    text_excerpt: '',
    text_status: 'empty',
    formula_review_required: false,
    object_summary: {},
    preview_url: `/api/teaching-prep/material-units/${id}/preview`,
    revision: 1,
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
  }
}

function twoRangeProposal(
  recordId: string,
  firstDecision: 'pending' | 'accepted' = 'pending',
  secondDecision: 'pending' | 'accepted' = 'pending',
): SemesterMappingProposal {
  const base = bookProposal(recordId, firstDecision)
  const first = base.payload.mappings[0]
  if (!first) throw new Error('expected first mapping')
  return {
    ...base,
    payload: {
      ...base.payload,
      mappings: [
        first,
        {
          ...first,
          mapping_id: 'mapping-2',
          start_unit: 3,
          end_unit: 4,
          decision: secondDecision,
          basis: '第二段依据。',
        },
      ],
    },
  }
}

function seedSelectedSemester(): ReturnType<typeof useTeachingPrepCatalogStore> {
  const curriculumId = 'c'.repeat(32)
  const catalog = useTeachingPrepCatalogStore()
  catalog.curricula = [{ id: curriculumId } as never]
  catalog.semesters = [{ id: semesterId, curriculum_id: curriculumId } as never]
  catalog.selectedCurriculumId = curriculumId
  catalog.selectedSemesterId = semesterId
  return catalog
}

function readyMappingTask(): WorkspaceAITask {
  return {
    contract_version: 'teacher_workspace_ai_task.v1',
    task_id: 'task-ready-mapping',
    operation_id: 'operation-ready-mapping',
    module: 'teaching_prep',
    task_kind: 'teaching_prep.semester_mapping',
    source_ref: { kind: 'semester', id: semesterId, revision: 'f'.repeat(64) },
    context_refs: [
      { kind: 'material', id: textbookRecord.id, revision: String(textbookRecord.revision) },
    ],
    return_target: 'teaching_prep.library',
    status: 'proposal_ready',
    phase: 'proposal_ready',
    progress: 1,
    send_attempt_count: 1,
    dispatch_evidence: 'response_persisted',
    cancel_requested: false,
    job_id: 1,
    proposal_ref_id: 'p'.repeat(32),
    proposal_revision: '1',
    error_code: null,
    error_detail: null,
    revision: 2,
    safe_title: '备课 · 整理学期资料',
    safe_source: '备课',
    teacher_message: 'AI 建议已经保存，尚未写入正式业务数据。',
    next_action: '审阅草稿',
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
}

async function mountPanel(options: MountOptions = {}) {
  const catalog = useTeachingPrepCatalogStore()
  const currentRecord = options.role
    ? record(textbookRecord.id, textbook, options.role)
    : textbookRecord
  catalog.materials = [textbook]
  catalog.semesterMaterials = [currentRecord]
  catalog.semesterMappingProposals = options.proposals ?? []
  catalog.lessonNodes = options.withLesson === false
    ? []
    : (options.lessons ?? [lesson(lessonId)])
  vi.spyOn(teachingPrepCatalogApi, 'listReferencePptCollections').mockResolvedValue([])
  vi.spyOn(catalog, 'openMaterial').mockImplementation(async (item) => {
    catalog.selectedMaterialId = item.id
    if (options.units) catalog.materialUnits = options.units
    return 'loaded'
  })
  vi.spyOn(catalog, 'prepareSemesterMapping').mockResolvedValue(undefined)

  const notices: string[] = []
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(MaterialCorrespondence, {
    materialId: textbook.id,
    onNotice: (message: string) => notices.push(message),
  })
  app.mount(host)
  await vi.waitFor(() => expect(catalog.openMaterial).toHaveBeenCalled())
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

    expect(host.querySelector('[data-testid="mapping-page-checker"]')).toBeTruthy()
    expect(host.textContent).toContain('1—2 页/张')
    expect(host.textContent).toContain('待你确认')
    expect(host.textContent).toContain('依据目录页码提出。')
    expect(host.textContent).toContain('本页还没有图')
    app.unmount()
  })

  it('paints adjacent lessons with alternating tones on the page strip', async () => {
    const secondLessonId = '2'.repeat(32)
    const pending = twoRangeProposal(textbookRecord.id)
    const second = pending.payload.mappings[1]
    if (!second) throw new Error('expected second mapping')
    pending.payload.mappings[1] = { ...second, lesson_ref: secondLessonId }

    const { app, host } = await mountPanel({
      proposals: [pending],
      lessons: [lesson(lessonId), lesson(secondLessonId, { title: '第二课时' })],
    })
    const tones = [...host.querySelectorAll('[data-page-index]')]
      .map(cell => cell.getAttribute('data-lesson-tone'))

    expect(tones).toEqual(['a', 'a', 'b', 'b'])
    expect(host.textContent).toContain('相邻小节用两种底色区分')
    expect(host.querySelector('[data-page-index="1"]')?.getAttribute('aria-label'))
      .toContain('第一课时')
    app.unmount()
  })

  it('keeps the same tone across consecutive pages of one lesson', async () => {
    const { app, host } = await mountPanel({
      proposals: [twoRangeProposal(textbookRecord.id)],
    })
    const tones = [...host.querySelectorAll('[data-page-index]')]
      .map(cell => cell.getAttribute('data-lesson-tone'))

    expect(tones).toEqual(['a', 'a', 'a', 'a'])
    app.unmount()
  })

  it('loads only the current page and neighboring page previews', async () => {
    const { app, host } = await mountPanel({
      proposals: [bookProposal(textbookRecord.id)],
      units: [pageUnit('1', 1), pageUnit('2', 2), pageUnit('3', 3)],
    })

    const preview = host.querySelector('[data-testid="mapping-page-preview"]')
    const sources = [...(preview?.querySelectorAll('img') ?? [])]
      .map(image => image.getAttribute('src'))
    expect(sources).toEqual([
      `/api/teaching-prep/material-units/${'1'.repeat(32)}/preview`,
      `/api/teaching-prep/material-units/${'2'.repeat(32)}/preview`,
    ])
    expect(host.textContent).not.toContain('本页还没有图')
    app.unmount()
  })

  it('accepts the current range and jumps to the next pending range', async () => {
    const secondLessonId = '2'.repeat(32)
    const pending = twoRangeProposal(textbookRecord.id)
    const second = pending.payload.mappings[1]
    if (!second) throw new Error('expected second mapping')
    pending.payload.mappings[1] = { ...second, lesson_ref: secondLessonId }
    const afterFirst = twoRangeProposal(textbookRecord.id, 'accepted', 'pending')
    const afterSecond = afterFirst.payload.mappings[1]
    if (!afterSecond) throw new Error('expected second mapping after accept')
    afterFirst.payload.mappings[1] = { ...afterSecond, lesson_ref: secondLessonId }
    const review = vi.spyOn(teachingPrepWorkbenchApi, 'reviewMapping')
      .mockResolvedValue(afterFirst as never)
    const { app, host } = await mountPanel({
      proposals: [pending],
      lessons: [lesson(lessonId), lesson(secondLessonId, { title: '第二课时' })],
    })

    expect(host.querySelector('[data-testid="mapping-current-range"]')?.textContent)
      .toContain('1—2 页/张')
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-current-range"]')?.click()
    await vi.waitFor(() => {
      expect(review).toHaveBeenCalled()
      expect(host.querySelector('[data-page-index="3"]')?.classList.contains('is-active')).toBe(true)
    })
    expect(review.mock.calls[0]?.[2]).toMatchObject({
      decision: 'accepted',
      lesson_ref: lessonId,
      start_unit: 1,
      end_unit: 2,
    })
    expect(host.querySelector('[data-testid="mapping-current-range"]')?.textContent)
      .toContain('3—4 页/张')
    expect(host.querySelector('[data-testid="mapping-current-range"]')?.textContent)
      .toContain('第二段依据。')
    app.unmount()
  })

  it('confirms one textbook section for every lesson that shares it', async () => {
    const sectionId = 's'.repeat(32)
    const secondLessonId = '2'.repeat(32)
    const pending = twoRangeProposal(textbookRecord.id)
    const second = pending.payload.mappings[1]
    if (!second) throw new Error('expected second mapping')
    pending.payload.mappings[1] = { ...second, lesson_ref: secondLessonId }
    let current = pending
    const review = vi.spyOn(teachingPrepWorkbenchApi, 'reviewMapping')
      .mockImplementation(async (_id, mappingId, body) => {
        current = {
          ...current,
          revision: current.revision + 1,
          payload: {
            ...current.payload,
            mappings: current.payload.mappings.map((item) => {
              if (item.mapping_id !== mappingId) return item
              return {
                ...item,
                decision: body.decision,
                decision_reason: body.reason ?? null,
                teacher_revision: body.decision === 'modified'
                  ? {
                      lesson_ref: String(body.lesson_ref),
                      start_unit: Number(body.start_unit),
                      end_unit: Number(body.end_unit),
                    }
                  : item.teacher_revision,
              }
            }),
          },
        }
        return current as never
      })
    const { app, host, notices } = await mountPanel({
      proposals: [pending],
      lessons: [
        lesson(sectionId, {
          node_type: 'section',
          title: '1 探索勾股定理',
          duration_minutes: null,
        }),
        lesson(lessonId, { parent_id: sectionId, title: '第1课时' }),
        lesson(secondLessonId, { parent_id: sectionId, title: '第2课时' }),
      ],
    })

    expect(host.textContent).toContain('1 个小节待你确认')
    expect(host.querySelector('[data-testid="mapping-current-range"]')?.textContent)
      .toContain('1—4 页/张')
    expect(host.querySelector('[data-testid="mapping-current-range"]')?.textContent)
      .toContain('1 探索勾股定理 · 2 个课时共用')
    const tones = [...host.querySelectorAll('[data-page-index]')]
      .map(cell => cell.getAttribute('data-lesson-tone'))
    expect(tones).toEqual(['a', 'a', 'a', 'a'])

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-current-range"]')?.click()
    await vi.waitFor(() => expect(review).toHaveBeenCalledTimes(2))
    expect(review.mock.calls.map(call => call[2])).toEqual([
      expect.objectContaining({
        decision: 'modified',
        lesson_ref: lessonId,
        start_unit: 1,
        end_unit: 4,
      }),
      expect.objectContaining({
        decision: 'modified',
        lesson_ref: secondLessonId,
        start_unit: 1,
        end_unit: 4,
      }),
    ])
    expect(notices.some(item => item.includes('已确认本小节，2 个课时将共用 1—4 页。'))).toBe(true)
    expect(host.textContent).toContain('已调整')
    app.unmount()
  })

  it('blocks applying while any mapping is still pending', async () => {
    const { app, host, catalog } = await mountPanel({
      proposals: [bookProposal(textbookRecord.id)],
    })
    const apply = vi.spyOn(catalog, 'applySemesterMapping').mockResolvedValue(undefined)
    const applyButton = host.querySelector<HTMLButtonElement>('[data-testid="apply-book-mapping"]')

    expect(applyButton?.disabled).toBe(true)
    applyButton?.click()
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
    expect(host.querySelector<HTMLButtonElement>('[data-testid="apply-book-mapping"]')?.disabled).toBe(false)
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

  it('does not offer another paid send when a saved proposal is not yet readable', async () => {
    seedSelectedSemester()
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals').mockResolvedValue([])
    const catalog = useTeachingPrepCatalogStore()
    vi.spyOn(catalog, 'load').mockResolvedValue()
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.tasks = { [readyMappingTask().task_id]: readyMappingTask() }

    const { app, host } = await mountPanel()

    expect(host.querySelector('[data-testid="saved-proposal-missing"]')?.textContent)
      .toContain('请先放弃这份草稿')
    const sendButton = host.querySelector<HTMLButtonElement>('[data-testid="generate-mapping"]')
    expect(sendButton?.textContent).toContain('请先放弃当前草稿再发送')
    expect(sendButton?.disabled).toBe(true)
    expect(host.querySelector('[data-testid="abandon-saved-mapping"]')).toBeTruthy()
    expect(host.querySelector('[data-testid="reload-book-proposal"]')).toBeTruthy()
    expect(host.querySelector('[data-testid="open-import-from-book"]')).toBeTruthy()
    expect(catalog.load).not.toHaveBeenCalled()
    app.unmount()
  })

  it('reloads a readable draft without wiping the current send scope', async () => {
    seedSelectedSemester()
    const listed = bookProposal(textbookRecord.id)
    const list = vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([])
    const catalog = useTeachingPrepCatalogStore()
    const load = vi.spyOn(catalog, 'load').mockResolvedValue()
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.tasks = { [readyMappingTask().task_id]: readyMappingTask() }

    const { app, host } = await mountPanel()
    await vi.waitFor(() => expect(list).toHaveBeenCalled())
    list.mockResolvedValue([listed])

    host.querySelector<HTMLButtonElement>('[data-testid="reload-book-proposal"]')?.click()
    await vi.waitFor(() => {
      expect(host.querySelector('[data-testid="mapping-page-checker"]')).toBeTruthy()
    })
    expect(list).toHaveBeenCalledWith(semesterId)
    expect(load).not.toHaveBeenCalled()
    expect(host.querySelector('[data-testid="saved-proposal-missing"]')).toBeNull()
    app.unmount()
  })

  it('lets the teacher abandon an unread draft before sending again', async () => {
    seedSelectedSemester()
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals').mockResolvedValue([])
    vi.stubGlobal('confirm', () => true)
    const rejected = {
      ...bookProposal(textbookRecord.id),
      status: 'rejected' as const,
    }
    const reject = vi.spyOn(teachingPrepCatalogApi, 'rejectSemesterMappingProposal')
      .mockResolvedValue(rejected)
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.tasks = { [readyMappingTask().task_id]: readyMappingTask() }

    const { app, host } = await mountPanel()
    host.querySelector<HTMLButtonElement>('[data-testid="abandon-saved-mapping"]')?.click()
    await vi.waitFor(() => {
      expect(host.querySelector('[data-testid="generate-mapping"]')?.textContent)
        .toContain('确认发送（单次计费）')
    })
    expect(reject).toHaveBeenCalledWith({
      id: 'p'.repeat(32),
      revision: 1,
    })
    expect(host.querySelector('[data-testid="saved-proposal-missing"]')).toBeNull()
    app.unmount()
  })

  it('lets the teacher abandon a readable draft and send again', async () => {
    seedSelectedSemester()
    const current = bookProposal(textbookRecord.id)
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals').mockResolvedValue([current])
    vi.stubGlobal('confirm', () => true)
    const rejected = { ...current, status: 'rejected' as const }
    const reject = vi.spyOn(teachingPrepCatalogApi, 'rejectSemesterMappingProposal')
      .mockResolvedValue(rejected)
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.tasks = { [readyMappingTask().task_id]: readyMappingTask() }

    const { app, host } = await mountPanel({ proposals: [current] })
    expect(host.querySelector('[data-testid="mapping-page-checker"]')).toBeTruthy()
    host.querySelector<HTMLButtonElement>('[data-testid="abandon-saved-mapping"]')?.click()
    await vi.waitFor(() => {
      expect(host.querySelector('[data-testid="mapping-page-checker"]')).toBeNull()
    })
    expect(reject).toHaveBeenCalledWith({
      id: current.id,
      revision: current.revision,
    })
    const sendButton = host.querySelector<HTMLButtonElement>('[data-testid="generate-mapping"]')
    expect(sendButton?.textContent).toContain('确认发送（单次计费）')
    app.unmount()
  })

  it('re-prepares the send scope when the cached one belongs to another book', async () => {
    const curriculumId = 'c'.repeat(32)
    const workbook = material('b'.repeat(32), '教辅.pdf')
    const workbookRecord = record('w'.repeat(32), workbook, 'exercise_workbook')
    const catalog = useTeachingPrepCatalogStore()
    catalog.curricula = [{ id: curriculumId } as never]
    catalog.semesters = [{ id: semesterId, curriculum_id: curriculumId } as never]
    catalog.selectedCurriculumId = curriculumId
    catalog.selectedSemesterId = semesterId
    catalog.materials = [textbook, workbook]
    catalog.semesterMaterials = [textbookRecord, workbookRecord]
    catalog.lessonNodes = [lesson(lessonId)]
    catalog.semesterMappingProposals = []
    catalog.selectedMaterialId = workbook.id

    const workbookPreflight: SemesterMappingPreflight = {
      semester_id: semesterId,
      source_state_sha256: 'e'.repeat(64),
      will_call_model: true,
      model_available: true,
      model_label: '测试模型',
      model_destination_fingerprint: 'f'.repeat(64),
      material_count: 1,
      unit_count: 2,
      existing_lesson_count: 1,
      creates_initial_tree: false,
      automatic_retry: false,
      evidence_strategy: 'toc_calibrated',
      evidence_confidence: 'high',
      scanned_unit_count: 2,
      directory_page_image_count: 0,
      directory_page_images_sent: false,
      toc_entry_count: 1,
      anchor_count: 1,
      estimated_input_characters: 100,
      full_page_text_sent: false,
      evidence_issues: [],
    }
    vi.spyOn(teachingPrepCatalogApi, 'listReferencePptCollections').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight').mockResolvedValue(workbookPreflight)
    // 先为教辅真实准备一次发送范围：store 里的缓存合法地属于教辅
    await catalog.prepareSemesterMapping([workbookRecord.id])
    expect(catalog.currentSemesterMappingPreflight?.source_state_sha256).toBe('e'.repeat(64))

    const prepare = vi.spyOn(catalog, 'prepareSemesterMapping').mockResolvedValue(undefined)
    vi.spyOn(catalog, 'openMaterial').mockResolvedValue('loaded')

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(MaterialCorrespondence, {
      materialId: textbook.id,
      onNotice: () => {},
    })
    app.mount(host)
    await nextTick()
    await nextTick()

    // 教材的组件不得展示或复用教辅的发送范围，发送按钮保持不可用
    expect(host.textContent).not.toContain('发送范围：')
    const sendButton = [...host.querySelectorAll('button')]
      .find(button => button.textContent?.includes('确认发送'))
    expect(sendButton?.disabled).toBe(true)

    host.querySelector<HTMLElement>('[data-testid="ai-reinfer"] summary')?.click()
    await vi.waitFor(() => expect(prepare).toHaveBeenCalledWith([textbookRecord.id]))
    app.unmount()
  })
})
