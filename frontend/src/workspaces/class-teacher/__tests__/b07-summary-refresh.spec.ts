import { createApp, nextTick, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../components/PlanningInboxPanel.vue', () => ({
  default: { render: () => null },
}))
vi.mock('../components/CollectionInboxPanel.vue', () => ({
  default: { render: () => null },
}))
vi.mock('../components/SopWorkspacePanel.vue', () => ({
  default: { render: () => null },
}))
vi.mock('../components/ActionLedgerPanel.vue', () => ({
  default: { render: () => null },
}))

import { ApiError } from '../../../api/errors'
import { actionApi } from '../api/actions'
import { sopApi } from '../api/sop'
import {
  supportApi,
  type SupportRecord,
  type SupportSummary,
  type SupportSubject,
} from '../api/support'
import { vaultApi, type VaultStatus } from '../api/vault'
import SupportWorkspacePanel from '../components/SupportWorkspacePanel.vue'
import ClassTeacherWorkbenchView from '../views/ClassTeacherWorkbenchView.vue'

const mounted: App[] = []

const subject: SupportSubject = {
  subject_id: 'synthetic-subject',
  revision: 1,
  source_student_id: 'SYN-STU-A',
  display_name: '匿名学生 A',
  class_label: '合成七年级',
}

const lockedStatus: VaultStatus = {
  initialized: true,
  locked: true,
  idle_timeout_seconds: 300,
  retry_after_seconds: 0,
  format_version: 1,
}

function record(content: string, revision = 1): SupportRecord {
  return {
    record_id: 'synthetic-record',
    subject_id: subject.subject_id,
    record_kind: 'fact',
    state: 'active',
    current_revision: revision,
    content,
    scene: '2026-07-30 合成场景',
    source: '教师当场记录',
    counterexample: null,
    observed_at: '2026-07-30T01:00:00.000Z',
    review_at: null,
    expires_at: null,
  }
}

function summary(value?: SupportRecord): SupportSummary {
  return {
    subject_id: subject.subject_id,
    as_of: '2026-07-30T02:00:00.000Z',
    items: value
      ? [{
          record_id: value.record_id,
          revision: value.current_revision,
          record_kind: value.record_kind,
          content: value.content,
          scene: value.scene,
          source: value.source,
          basis: null,
          counterexample: value.counterexample,
          review_at: value.review_at,
          expires_at: value.expires_at,
        }]
      : [],
    source_record_count: value ? 1 : 0,
  }
}

async function flush(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function flushAll(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flush()
}

function setValue(control: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  control.value = value
  control.dispatchEvent(new Event('input', { bubbles: true }))
}

function submit(form: HTMLFormElement): void {
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
}

function mockSupportReads(
  initialRecords: SupportRecord[] = [],
): void {
  vi.spyOn(supportApi, 'listSubjects').mockResolvedValue([subject])
  vi.spyOn(supportApi, 'listRecords').mockResolvedValue(initialRecords)
  vi.spyOn(supportApi, 'listEvidence').mockResolvedValue([])
  vi.spyOn(supportApi, 'listAttention').mockResolvedValue([])
  vi.spyOn(supportApi, 'listSupportPlans').mockResolvedValue([])
  vi.spyOn(supportApi, 'listQuickInbox').mockResolvedValue([])
  vi.spyOn(actionApi, 'listPlans').mockResolvedValue([])
  vi.spyOn(sopApi, 'listTemplates').mockResolvedValue([])
}

async function mountSupport(
  initialRecords: SupportRecord[] = [],
): Promise<{ host: HTMLElement; errors: string[] }> {
  mockSupportReads(initialRecords)
  const host = document.createElement('div')
  document.body.append(host)
  const errors: string[] = []
  const app = createApp(SupportWorkspacePanel, {
    sessionToken: 'synthetic-token',
    onError: (message: string) => errors.push(message),
  })
  app.mount(host)
  mounted.push(app)
  await flushAll()
  return { host, errors }
}

function createRecordForm(host: HTMLElement): HTMLFormElement {
  const content = host.querySelector<HTMLTextAreaElement>(
    'textarea[placeholder="具体发生了什么"]',
  )
  const scene = host.querySelector<HTMLInputElement>('input[placeholder="时间与场景"]')
  const form = content?.closest<HTMLFormElement>('form')
  if (!content || !scene || !form) throw new Error('Missing support record form')
  setValue(content, '合成支持事实')
  setValue(scene, '2026-07-30 合成场景')
  return form
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('B07 current summary consistency', () => {
  it('refreshes the current summary after saving and blocks subject switching while writing', async () => {
    const created = record('合成支持事实')
    const existing = record('既有支持事实')
    let releaseCreate!: (value: SupportRecord) => void
    vi.spyOn(supportApi, 'getSummary')
      .mockResolvedValueOnce(summary(existing))
      .mockResolvedValueOnce(summary(created))
    vi.spyOn(supportApi, 'createRecord').mockImplementation(
      () => new Promise((resolve) => { releaseCreate = resolve }),
    )

    const { host, errors } = await mountSupport([existing])
    submit(createRecordForm(host))
    await flush()

    const subjectSelect = host.querySelector<HTMLSelectElement>('.subject-rail select')
    const reviseButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '修订并保留旧版')
    expect(subjectSelect?.disabled).toBe(true)
    expect(reviseButton?.disabled).toBe(true)

    releaseCreate(created)
    await flushAll()

    expect(errors).toEqual([])
    expect(supportApi.getSummary).toHaveBeenCalledTimes(2)
    expect(host.querySelector('.current-summary')?.textContent).toContain('合成支持事实')
  })

  it('refreshes the current summary after revising a saved record', async () => {
    const original = record('修订前正文')
    const revised = record('修订后的第 2 版正文', 2)
    let releaseRevision!: (value: SupportRecord) => void
    vi.spyOn(supportApi, 'getSummary')
      .mockResolvedValueOnce(summary(original))
      .mockResolvedValueOnce(summary(revised))
    const reviseRecord = vi.spyOn(supportApi, 'reviseRecord').mockImplementation(
      () => new Promise((resolve) => { releaseRevision = resolve }),
    )

    const { host, errors } = await mountSupport([original])
    const reviseButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '修订并保留旧版')
    if (!reviseButton) throw new Error('Missing revision button')
    reviseButton.click()
    await nextTick()

    const revisionForm = host.querySelector<HTMLFormElement>('.revision-form')
    const revisedContent = revisionForm?.querySelector<HTMLTextAreaElement>('textarea')
    const reason = revisionForm?.querySelector<HTMLInputElement>(
      'input[placeholder="本次修订原因"]',
    )
    if (!revisionForm || !revisedContent || !reason) throw new Error('Missing revision form')
    setValue(revisedContent, revised.content)
    setValue(reason, '补充准确事实')
    submit(revisionForm)
    submit(revisionForm)
    await flush()

    expect(reviseRecord).toHaveBeenCalledOnce()
    expect(revisionForm.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled)
      .toBe(true)

    releaseRevision(revised)
    await flushAll()

    expect(errors).toEqual([])
    expect(supportApi.getSummary).toHaveBeenCalledTimes(2)
    expect(host.querySelector('.current-summary')?.textContent).toContain(revised.content)
    expect(host.querySelector('.current-summary')?.textContent).not.toContain(original.content)
  })

  it('reports that the record was saved when the summary refresh fails', async () => {
    const created = record('已经保存但摘要读取失败')
    vi.spyOn(supportApi, 'getSummary')
      .mockResolvedValueOnce(summary())
      .mockRejectedValueOnce(new Error('synthetic summary read failure'))
    vi.spyOn(supportApi, 'createRecord').mockResolvedValue(created)

    const { host, errors } = await mountSupport()
    submit(createRecordForm(host))
    await flushAll()

    expect(host.querySelector('.timeline')?.textContent).toContain(created.content)
    expect(errors).toEqual([
      '支持记录已经保存，但当前有效摘要暂时无法重新读取，请稍后重试。',
    ])
    expect(errors[0]).not.toContain('现有记录没有改变')
  })

  it('keeps the post-save fact visible when the summary read locks the vault', async () => {
    mockSupportReads()
    const created = record('锁定前已经保存的支持事实')
    vi.spyOn(supportApi, 'getSummary')
      .mockResolvedValueOnce(summary())
      .mockRejectedValueOnce(new ApiError({
        kind: 'forbidden',
        status: 403,
        code: 'vault_locked',
        message: '保险箱已锁定',
        details: {},
        requestId: 'synthetic-request',
        retryable: false,
      }))
    vi.spyOn(supportApi, 'createRecord').mockResolvedValue(created)
    vi.spyOn(vaultApi, 'status').mockImplementation(async (token) => ({
      ...lockedStatus,
      locked: !token,
    }))
    vi.spyOn(vaultApi, 'unlock').mockResolvedValue({
      session_token: 'synthetic-session',
      idle_timeout_seconds: 300,
      recovery_key: null,
    })
    vi.spyOn(vaultApi, 'listBackups').mockResolvedValue([])
    vi.spyOn(vaultApi, 'lock').mockResolvedValue({})
    vi.spyOn(vaultApi, 'touch').mockResolvedValue({})

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(ClassTeacherWorkbenchView)
    app.mount(host)
    mounted.push(app)
    await flushAll()

    const password = host.querySelector<HTMLInputElement>('input[type="password"]')
    const unlockButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '解锁工作台')
    if (!password || !unlockButton) throw new Error('Missing vault unlock controls')
    setValue(password, '合成B07验收密码-足够长-001')
    await nextTick()
    unlockButton.click()
    await flushAll()

    submit(createRecordForm(host))
    await flushAll()

    expect(host.textContent).toContain(
      '支持记录已经保存，但当前有效摘要暂时无法重新读取；保险箱已锁定，请重新解锁后核对。',
    )
    expect(host.textContent).not.toContain('当前操作没有完成，现有数据没有改变')
  })
})
