import { createApp, nextTick, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  PersonalizedPaperInstance,
  TrainingScanBatch,
} from '../api/training'
import TrainingScanBatchPanel from '../components/training/TrainingScanBatchPanel.vue'

const trainingApiMock = vi.hoisted(() => ({
  createTrainingScanBatch: vi.fn(),
  uploadTrainingScan: vi.fn(),
  resolveTrainingScanPage: vi.fn(),
  cancelTrainingSubmission: vi.fn(),
}))

vi.mock('../api/training', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/training')>(),
  trainingApi: trainingApiMock,
}))

const paper = {
  paper_instance_id: 'a'.repeat(64),
  paper_batch_id: 'f'.repeat(64),
  draft_id: 'd'.repeat(64),
  draft_revision: 1,
  student_id: 'SYN-S01',
  student_code: 'S01',
  student_name: '合成学生',
  class_id: 'SYN-C01',
  series_version: 1,
  status: 'frozen',
  revision: 2,
  layout_version: 'personalized-paper-school-a4-v1',
  budget: {
    version: 'whole-paper-context-budget-v1',
    status: 'ready',
    context_window_tokens: 32768,
    question_count: 1,
    criterion_point_count: 2,
    image_count: 0,
    page_count: 2,
    page_count_is_estimate: false,
    estimated_input_tokens: 1000,
    estimated_output_tokens: 500,
    estimated_total_tokens: 1500,
    limits: { questions: 12, criterion_points: 120, images: 48, pages: 20 },
    blockers: [],
  },
  question_count: 1,
  criterion_point_count: 2,
  items: [],
  pages: [{ page_number: 1 }, { page_number: 2 }],
  downloads: {
    review_docx: null,
    reviewed_docx: null,
    frozen_pdf: '/api/training/paper.pdf',
  },
  created_at: '2026-07-30T08:00:00+00:00',
} satisfies PersonalizedPaperInstance

const emptyBatch = {
  batch_id: 'b'.repeat(64),
  paper_batch_id: 'f'.repeat(64),
  status: 'manual_review',
  revision: 1,
  duplicate_upload: false,
  submissions: [{
    submission_id: 'c'.repeat(64),
    paper_instance_id: paper.paper_instance_id,
    student_id: paper.student_id,
    student_code: paper.student_code,
    student_name: paper.student_name,
    class_id: paper.class_id,
    series_version: 1,
    status: 'manual_review',
    revision: 1,
    expected_total_pages: 2,
    missing_pages: [1, 2],
    issue_codes: [],
    assessment_started: false,
  }],
  pages: [],
  candidates: [{
    paper_instance_id: paper.paper_instance_id,
    student_id: paper.student_id,
    student_code: paper.student_code,
    student_name: paper.student_name,
    series_version: 1,
    total_pages: 2,
  }],
  history: [],
  created_at: '2026-07-30T08:00:00+00:00',
  updated_at: '2026-07-30T08:00:00+00:00',
} satisfies TrainingScanBatch

const anomalyBatch = {
  ...emptyBatch,
  revision: 2,
  pages: [{
    scan_page_id: 'e'.repeat(64),
    upload_id: '9'.repeat(64),
    upload_page_number: 1,
    submission_id: null,
    paper_instance_id: null,
    page_number: null,
    total_pages: null,
    issue_code: 'identity_unreadable',
    state: 'unassigned',
    rotation_degrees: 0,
    preview_url: `/api/training/scan-batches/${'b'.repeat(64)}/pages/${'e'.repeat(64)}/preview`,
  }],
} satisfies TrainingScanBatch

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

beforeEach(() => {
  vi.clearAllMocks()
  trainingApiMock.createTrainingScanBatch.mockResolvedValue(emptyBatch)
  trainingApiMock.uploadTrainingScan.mockResolvedValue(anomalyBatch)
})

afterEach(() => {
  mounted.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
})

describe('training scan batch panel', () => {
  it('creates an explicit expected set and shows full-page manual recovery', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(TrainingScanBatchPanel, { instances: [paper] })
    mounted.push(app)
    app.mount(host)
    await settle()

    const create = [...host.querySelectorAll('button')].find(
      (button) => button.textContent?.includes('建立扫描批次'),
    )
    create?.click()
    await settle()
    expect(trainingApiMock.createTrainingScanBatch).toHaveBeenCalledWith(
      [paper.paper_instance_id],
      expect.stringMatching(/^[0-9a-f]{32}$/),
    )

    const input = host.querySelector<HTMLInputElement>('input[type="file"]')
    const scan = new File(['scan'], 'scan.png', { type: 'image/png' })
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [scan],
    })
    input?.dispatchEvent(new Event('change'))
    await settle()
    const upload = [...host.querySelectorAll('button')].find(
      (button) => button.textContent?.includes('导入并归组'),
    )
    upload?.click()
    await settle()

    expect(trainingApiMock.uploadTrainingScan).toHaveBeenCalledWith(
      emptyBatch,
      scan,
      expect.stringMatching(/^[0-9a-f]{32}$/),
    )
    expect(host.textContent).toContain('页面身份无法读取')
    expect(host.textContent).toContain('人工匹配')
    expect(host.textContent).toContain('明确替换该页')
    expect(host.textContent).toContain('不会根据 OCR 自动确认')
    expect(host.querySelector('img')?.getAttribute('src')).toBe(
      anomalyBatch.pages[0]?.preview_url,
    )
  })
})
