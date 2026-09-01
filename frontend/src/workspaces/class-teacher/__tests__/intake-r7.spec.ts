import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/errors'
import { decodeHandoffDraft, intakeApi, type IntakeConversation, type IntakeConversationSummary } from '../api/intake'
import { workApi, type WorkNode, type WorkSnapshot } from '../api/work'
import ConversationDesk from '../intake/ConversationDesk.vue'
import * as browserVoice from '../intake/browserVoiceRecorder'

const mounted: Array<ReturnType<typeof createApp>> = []

function conversation(state = 'collecting'): IntakeConversation {
  return {
    conversation_id: 'conversation-1234', revision: 1, state, homeroom_class: '一班',
    created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    turns: [], handoffs: [],
  }
}

async function settle(): Promise<void> {
  await Promise.resolve(); await Promise.resolve(); await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
}

async function mountDesk(
  startValue = conversation(),
  workNodes: WorkNode[] = [],
  cloudConfigured = true,
  listeners: Record<string, unknown> = {},
  recentItems: IntakeConversationSummary[] = [],
  workEdges: WorkSnapshot['edges'] = [],
) {
  vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({
    homeroom_class: '一班', revision: 1, classes: ['一班', '二班'], source_revision: 'a'.repeat(64),
  })
  vi.spyOn(intakeApi, 'listConversations').mockResolvedValue(recentItems)
  vi.spyOn(intakeApi, 'startConversation').mockResolvedValue(startValue)
  vi.spyOn(intakeApi, 'deleteConversation').mockResolvedValue({ conversation_id: 'conversation-1234', deleted: true })
  vi.spyOn(intakeApi, 'speechCapabilities').mockResolvedValue({
    available: true, status: 'ready', engine: 'synthetic-local-speech', offline: true,
    sample_rate: 16000, max_duration_seconds: 60, max_audio_bytes: 2_100_000,
    accepted_content_type: 'audio/wav',
    cloud_audio: cloudConfigured ? {
      available: true, status: 'ready', provider: 'configured_model',
      model: 'synthetic-audio-model', destination_fingerprint: 'fingerprint-1234',
    } : {
      available: false, status: 'profile_missing', provider: 'configured_model',
      model: null, destination_fingerprint: 'unconfigured-fingerprint',
    },
  })
  vi.spyOn(workApi, 'read').mockResolvedValue({
    as_of: '2026-08-05T00:00:00Z', start_date: '2026-08-04', end_date: '2026-08-10',
    nodes: workNodes, edges: workEdges, today: workNodes, overdue: [], waiting: [], review_due: [],
    summary: { today: workNodes.length, overdue: 0, waiting: 0, review_due: 0 },
    view: 'week', cursor: null, source_version: 'synthetic',
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ConversationDesk, listeners)
  app.mount(host); mounted.push(app)
  await settle()
  return host
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('B-UI-R7 conversation desk', () => {
  it('shows one conversation-first home with six domains and no legacy gate', async () => {
    const host = await mountDesk()
    const text = host.textContent ?? ''
    for (const label of ['成长记录', '学生支持', '冲突安全', '班级日常', '活动文化', '学校协同']) {
      expect(text).toContain(label)
    }
    expect(text).not.toContain('PIN')
    expect(text).not.toContain('解锁')
    expect(text).not.toContain('匿名预览')
  })

  it('sends directly, keeps body out of browser storage, and shows manual routing after failure', async () => {
    const host = await mountDesk()
    const failed: IntakeConversation = {
      ...conversation('failed'), revision: 2,
      turns: [{
        turn_id: 'turn-12345678', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-1234', teacher_message: '合成学生正文', assistant_message: null,
        clarification_questions: [], task_id: null, task_state: 'failed_before_dispatch',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
    }
    const append = vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(failed)
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    const textarea = host.querySelector('textarea')!
    textarea.value = '合成学生正文'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()

    expect(append).toHaveBeenCalledWith(expect.any(Object), '合成学生正文')
    expect(storage).not.toHaveBeenCalled()
    expect(host.textContent).toContain('无需再次调用 AI')
    expect(host.textContent).toContain('计划／日历')
  })

  it('records locally, fills the composer, and waits for the teacher to send', async () => {
    const recorder: browserVoice.VoiceRecorder = {
      start: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn().mockResolvedValue(new Blob(['synthetic-wave'], { type: 'audio/wav' })),
      cancel: vi.fn(),
    }
    vi.spyOn(browserVoice, 'browserVoiceRecordingSupported').mockReturnValue(true)
    vi.spyOn(browserVoice, 'createVoiceRecorder').mockReturnValue(recorder)
    const transcribe = vi.spyOn(intakeApi, 'transcribeSpeech').mockResolvedValue({
      text: '合成本地语音转写。', duration_seconds: 1.2,
      engine: 'synthetic-local-speech', audio_retained: false,
    })
    const append = vi.spyOn(intakeApi, 'appendTurn')
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    const host = await mountDesk()

    host.querySelector<HTMLButtonElement>('[aria-label="语音输入"]')!.click()
    await settle()
    expect(host.textContent).toContain('停止录音')
    const voiceStatus = host.querySelector<HTMLElement>('.voice-input__status')!
    const stopButton = host.querySelector<HTMLButtonElement>('[aria-label="停止录音"]')!
    expect(voiceStatus.textContent).toContain('正在录音')
    expect(voiceStatus.compareDocumentPosition(stopButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(host.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled).toBe(true)

    host.querySelector<HTMLButtonElement>('[aria-label="停止录音"]')!.click()
    await settle()

    expect(transcribe).toHaveBeenCalledWith(expect.any(Blob), expect.any(AbortSignal))
    expect(host.querySelector<HTMLTextAreaElement>('.composer textarea')!.value).toBe('合成本地语音转写。')
    expect(host.textContent).toContain('语音已转成文字并回填')
    expect(append).not.toHaveBeenCalled()
    expect(storage).not.toHaveBeenCalled()
  })

  it('defaults to local text and requires a second confirmation before sending audio to the model', async () => {
    const wav = new Blob(['synthetic-wave'], { type: 'audio/wav' })
    const recorder: browserVoice.VoiceRecorder = {
      start: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn().mockResolvedValue(wav),
      cancel: vi.fn(),
    }
    vi.spyOn(browserVoice, 'browserVoiceRecordingSupported').mockReturnValue(true)
    vi.spyOn(browserVoice, 'createVoiceRecorder').mockReturnValue(recorder)
    const transcribe = vi.spyOn(intakeApi, 'transcribeSpeech')
    const sendAudio = vi.spyOn(intakeApi, 'sendCloudAudio').mockResolvedValue({
      ...conversation('handoff_ready'), revision: 3,
      turns: [{
        turn_id: 'turn-audio-01', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-audio-01', teacher_message: '合成语音转写', assistant_message: '已形成草稿',
        clarification_questions: [], task_id: 'audio-operation-audio-01', task_state: 'response_persisted',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
    })
    const host = await mountDesk()

    const sharedMicrophone = host.querySelector<HTMLButtonElement>('.voice-input__button')!
    expect(sharedMicrophone.getAttribute('aria-label')).toBe('语音输入')
    expect(host.querySelector<HTMLButtonElement>('.voice-mode button.is-active')!.textContent).toContain('本机转文字')
    expect(host.textContent).toContain('要求当前模型支持语音输入')
    const cloudMode = [...host.querySelectorAll<HTMLButtonElement>('.voice-mode button')]
      .find((item) => item.textContent?.includes('语音给模型'))!
    cloudMode.click()
    await settle()
    expect(host.querySelector<HTMLButtonElement>('.voice-input__button')).toBe(sharedMicrophone)
    expect(sharedMicrophone.getAttribute('aria-label')).toBe('语音输入')
    sharedMicrophone.click()
    await settle()
    host.querySelector<HTMLButtonElement>('[aria-label="停止录音"]')!.click()
    await settle()

    expect(host.textContent).toContain('尚未发送')
    expect(host.querySelector<HTMLButtonElement>('.voice-input__button')).toBe(sharedMicrophone)
    expect(sharedMicrophone.getAttribute('aria-label')).toBe('语音输入')
    expect(host.querySelector('.voice-input__pending')).not.toBeNull()
    expect(sendAudio).not.toHaveBeenCalled()
    expect(transcribe).not.toHaveBeenCalled()

    const sendButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.includes('发送录音'))!
    sendButton.click()
    await settle()

    expect(sendAudio).toHaveBeenCalledTimes(1)
    expect(sendAudio).toHaveBeenCalledWith(
      expect.objectContaining({ conversation_id: 'conversation-1234', revision: 1 }),
      wav,
      expect.any(String),
      'fingerprint-1234',
      expect.any(AbortSignal),
    )
    expect(host.textContent).toContain('语音已转写并形成草稿')
    expect(host.textContent).not.toContain('尚未发送')
  })

  it('allows choosing cloud mode without a model whitelist and shows the support reminder', async () => {
    const host = await mountDesk(conversation(), [], false)
    const cloudMode = [...host.querySelectorAll<HTMLButtonElement>('.voice-mode button')]
      .find((item) => item.textContent?.includes('语音给模型'))!

    cloudMode.click()
    await settle()

    expect(cloudMode.classList.contains('is-active')).toBe(true)
    expect(host.textContent).toContain('要求当前模型支持语音输入')
    expect(host.textContent).not.toContain('整段原始录音会发给已配置模型')
    expect(host.querySelector('.notice')).toBeNull()
  })

  it('keeps failed cloud audio in memory for one local transcription and never resends it', async () => {
    const wav = new Blob(['synthetic-wave'], { type: 'audio/wav' })
    const recorder: browserVoice.VoiceRecorder = {
      start: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn().mockResolvedValue(wav),
      cancel: vi.fn(),
    }
    vi.spyOn(browserVoice, 'browserVoiceRecordingSupported').mockReturnValue(true)
    vi.spyOn(browserVoice, 'createVoiceRecorder').mockReturnValue(recorder)
    const sendAudio = vi.spyOn(intakeApi, 'sendCloudAudio').mockRejectedValue({
      code: 'class_teacher_cloud_audio_result_unknown',
    })
    const transcribe = vi.spyOn(intakeApi, 'transcribeSpeech').mockResolvedValue({
      text: '失败后保留的本机转写', duration_seconds: 1,
      engine: 'synthetic-local-speech', audio_retained: false,
    })
    const host = await mountDesk()

    const cloudMode = [...host.querySelectorAll<HTMLButtonElement>('.voice-mode button')]
      .find((item) => item.textContent?.includes('语音给模型'))!
    cloudMode.click(); await settle()
    host.querySelector<HTMLButtonElement>('[aria-label="语音输入"]')!.click(); await settle()
    host.querySelector<HTMLButtonElement>('[aria-label="停止录音"]')!.click(); await settle()
    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.includes('发送录音'))!.click()
    await settle()

    expect(sendAudio).toHaveBeenCalledTimes(1)
    expect(host.textContent).toContain('不会重复发送')
    expect(host.textContent).toContain('不再重复发送')
    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.includes('转为本机文字'))!.click()
    await settle()

    expect(transcribe).toHaveBeenCalledWith(wav, expect.any(AbortSignal))
    expect(sendAudio).toHaveBeenCalledTimes(1)
    expect(host.querySelector<HTMLTextAreaElement>('.composer textarea')!.value).toBe('失败后保留的本机转写')
  })

  it('keeps an over-limit voice transcript visible but prevents sending until edited', async () => {
    const recorder: browserVoice.VoiceRecorder = {
      start: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn().mockResolvedValue(new Blob(['synthetic-wave'], { type: 'audio/wav' })),
      cancel: vi.fn(),
    }
    vi.spyOn(browserVoice, 'browserVoiceRecordingSupported').mockReturnValue(true)
    vi.spyOn(browserVoice, 'createVoiceRecorder').mockReturnValue(recorder)
    vi.spyOn(intakeApi, 'transcribeSpeech').mockResolvedValue({
      text: '补充内容', duration_seconds: 1,
      engine: 'synthetic-local-speech', audio_retained: false,
    })
    const append = vi.spyOn(intakeApi, 'appendTurn')
    const host = await mountDesk()
    const textarea = host.querySelector<HTMLTextAreaElement>('.composer textarea')!
    textarea.value = '原'.repeat(3998)
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()

    host.querySelector<HTMLButtonElement>('[aria-label="语音输入"]')!.click()
    await settle()
    host.querySelector<HTMLButtonElement>('[aria-label="停止录音"]')!.click()
    await settle()

    expect(textarea.value).toBe(`${'原'.repeat(3998)}\n补充内容`)
    expect(host.textContent).toContain('超过 4000 字')
    expect(host.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled).toBe(true)
    expect(append).not.toHaveBeenCalled()
  })

  it('cancels a pending microphone permission request when the page closes', async () => {
    let allowMicrophone!: () => void
    const pendingPermission = new Promise<void>((resolve) => { allowMicrophone = resolve })
    const recorder: browserVoice.VoiceRecorder = {
      start: vi.fn().mockReturnValue(pendingPermission),
      stop: vi.fn(),
      cancel: vi.fn(),
    }
    vi.spyOn(browserVoice, 'browserVoiceRecordingSupported').mockReturnValue(true)
    vi.spyOn(browserVoice, 'createVoiceRecorder').mockReturnValue(recorder)
    const host = await mountDesk()

    host.querySelector<HTMLButtonElement>('[aria-label="语音输入"]')!.click()
    await nextTick()
    expect(host.querySelector<HTMLButtonElement>('[aria-label="等待麦克风"]')!.disabled).toBe(true)
    expect(host.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled).toBe(true)

    mounted.pop()!.unmount()
    allowMicrophone()
    await settle()

    expect(recorder.cancel).toHaveBeenCalled()
  })

  it('shows the assigned class as read-only text', async () => {
    const setHomeroom = vi.spyOn(intakeApi, 'setHomeroom')
    const host = await mountDesk()

    expect(host.querySelector('.desk__tools select')).toBeNull()
    expect(host.querySelector('.desk__homeroom')?.textContent).toContain('一班')
    expect(setHomeroom).not.toHaveBeenCalled()
  })

  it('does not start another empty conversation from the desk', async () => {
    const host = await mountDesk()
    const startConversation = vi.mocked(intakeApi.startConversation)
    expect(startConversation).toHaveBeenCalledTimes(1)

    const button = [...host.querySelectorAll('button')].find((item) => item.textContent?.includes('新对话'))
    button?.click()
    await settle()

    expect(startConversation).toHaveBeenCalledTimes(1)
  })

  it('lists at most five conversations that already have content', async () => {
    const recentItems = [
      ...Array.from({ length: 6 }, (_, index) => ({
        conversation_id: `conversation-filled-${index}`,
        revision: 1,
        state: 'collecting',
        homeroom_class: '一班',
        first_message: `合成事项${index}`,
        pending_count: 0,
        updated_at: '2026-08-05T00:00:00Z',
      })),
      {
        conversation_id: 'conversation-empty',
        revision: 1,
        state: 'collecting',
        homeroom_class: '一班',
        first_message: null,
        pending_count: 0,
        updated_at: '2026-08-05T00:00:00Z',
      },
    ]
    const host = await mountDesk(conversation(), [], true, {}, recentItems)
    const buttons = host.querySelectorAll('.recent-open')

    expect(host.querySelector('.recent header')?.textContent).toContain('新对话')
    expect(host.querySelector('.desk__masthead')?.textContent).not.toContain('新对话')
    expect(buttons).toHaveLength(5)
    expect(host.textContent).not.toContain('尚未发送内容')
    expect(host.textContent).not.toContain('合成事项5')
  })

  it('deletes a recent conversation after confirmation', async () => {
    const recentItems: IntakeConversationSummary[] = [{
      conversation_id: 'conversation-filled-0',
      revision: 1,
      state: 'collecting',
      homeroom_class: '一班',
      first_message: '合成事项0',
      pending_count: 1,
      updated_at: '2026-08-05T00:00:00Z',
    }]
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const host = await mountDesk(conversation(), [], true, {}, recentItems)
    host.querySelector<HTMLButtonElement>('.recent-delete')!.click()
    await settle()

    expect(intakeApi.deleteConversation).toHaveBeenCalledWith('conversation-filled-0')
    expect(host.textContent).toContain('这段对话已删除')
  })

  it('keeps the conversation when deletion is cancelled', async () => {
    const recentItems: IntakeConversationSummary[] = [{
      conversation_id: 'conversation-filled-0',
      revision: 1,
      state: 'collecting',
      homeroom_class: '一班',
      first_message: '合成事项0',
      pending_count: 1,
      updated_at: '2026-08-05T00:00:00Z',
    }]
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const host = await mountDesk(conversation(), [], true, {}, recentItems)
    host.querySelector<HTMLButtonElement>('.recent-delete')!.click()
    await settle()

    expect(intakeApi.deleteConversation).not.toHaveBeenCalled()
    expect(host.textContent).toContain('合成事项0')
  })

  it('does not describe an unknown result as still processing', async () => {
    const host = await mountDesk({
      ...conversation('failed'),
      turns: [{
        turn_id: 'turn-unknown-01', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-unknown-01', teacher_message: '合成未知结果正文', assistant_message: null,
        clarification_questions: [], task_id: 'task-unknown-01', task_state: 'result_unknown',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
    })
    expect(host.textContent).toContain('可能已经发出')
    expect(host.textContent).toContain('不会自动重发')
  })

  it('shows a stale student-reference warning on the preserved review draft', async () => {
    const host = await mountDesk({
      ...conversation('handoff_ready'),
      handoffs: [{
        handoff_id: 'handoff-stale-ref',
        draft_id: 'draft-stale-ref',
        work_item_id: 'work-stale-ref',
        turn_id: 'turn-stale-ref',
        domain: 'student_support',
        handling_mode: 'record',
        intent: 'append',
        destination_key: 'class_teacher.student.record',
        draft_revision: 1,
        adoption_state: 'pending',
        missing_fields: ['学生版本信息不一致，请重新选择'],
        subject_ref_count: 0,
        subject_id: null,
        auto_open_allowed: false,
      }],
    })

    const draftCard = host.querySelector<HTMLElement>('[data-work-item="work-stale-ref"]')
    expect(draftCard).not.toBeNull()
    expect(draftCard?.textContent).toContain('学生版本信息不一致，请重新选择')
    expect(draftCard?.textContent).toContain('学生个人档案')
    expect(draftCard?.textContent).toContain('请先在对话里确认是哪名学生')
    expect(draftCard?.textContent).not.toContain('登记草稿')
  })

  it('does not auto-open a student record draft and opens the profile card instead', async () => {
    const opened: unknown[] = []
    const profiles: unknown[] = []
    const host = await mountDesk(conversation(), [], true, {
      onOpenHandoff: (item: unknown) => opened.push(item),
      onOpenStudentProfile: (item: unknown) => profiles.push(item),
    })
    const ready: IntakeConversation = {
      ...conversation('handoff_ready'), revision: 2,
      turns: [{
        turn_id: 'turn-profile-01', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-profile-01', teacher_message: '合成学生家庭情况', assistant_message: '已整理到当前档案。',
        clarification_questions: ['在校表现有没有变化？'], task_id: 'task-profile-01', task_state: 'response_persisted',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
      handoffs: [{
        handoff_id: 'handoff-profile-01', draft_id: 'draft-profile-01', work_item_id: 'work-profile-01',
        turn_id: 'turn-profile-01', domain: 'student_support', handling_mode: 'record', intent: 'append',
        destination_key: 'class_teacher.student.record', draft_revision: 1, adoption_state: 'pending',
        missing_fields: [], subject_ref_count: 1, subject_id: 'subject-01', auto_open_allowed: true,
      }],
    }
    vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue({
      contract_version: 'teacher_workspace_handoff.v1', handoff_id: 'handoff-profile-01', work_item_id: 'work-profile-01',
      conversation_id: 'conversation-1234', turn_id: 'turn-profile-01', draft_id: 'draft-profile-01', draft_revision: 1,
      domain: 'student_support', handling_mode: 'record', intent: 'append',
      destination_key: 'class_teacher.student.record', adoption_id: 'adoption-profile-01', adoption_state: 'opened',
      content: { summary: '合成学生家庭情况' },
      subject_refs: [{ kind: 'student', id: 'subject-01', revision: '3' }],
      missing_fields: [], return_context: { destination_key: 'class_teacher.home', focus_ref: 'work-profile-01' },
    })
    vi.spyOn(intakeApi, 'adopt').mockRejectedValue(new ApiError({
      kind: 'conflict', status: 409, code: 'class_teacher_target_conflict',
      message: '学生资料已变化', details: {}, requestId: 'synthetic-request', retryable: false,
    }))
    vi.spyOn(intakeApi, 'conversation').mockResolvedValue(ready)
    const textarea = host.querySelector('textarea')!
    textarea.value = '合成学生家庭情况'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    host.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()

    expect(opened).toHaveLength(0)
    expect(host.textContent).toContain('学生个人档案')
    expect(host.textContent).toContain('自动并入未完成时，打开学生档案手动核对')
    host.querySelector<HTMLElement>('[data-work-item="work-profile-01"]')!.click()
    expect(profiles).toHaveLength(1)
  })

  it('still auto-opens a single plan draft', async () => {
    const opened: unknown[] = []
    const host = await mountDesk(conversation(), [], true, {
      onOpenHandoff: (item: unknown) => opened.push(item),
    })
    vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue({
      ...conversation('handoff_ready'), revision: 2,
      turns: [{
        turn_id: 'turn-plan-01', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-plan-01', teacher_message: '月底班会', assistant_message: '已整理为计划草稿。',
        clarification_questions: [], task_id: 'task-plan-01', task_state: 'response_persisted',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
      handoffs: [{
        handoff_id: 'handoff-plan-01', draft_id: 'draft-plan-01', work_item_id: 'work-plan-01',
        turn_id: 'turn-plan-01', domain: 'class_operations', handling_mode: 'plan_calendar', intent: 'plan',
        destination_key: 'class_teacher.plan.calendar', draft_revision: 1, adoption_state: 'pending',
        missing_fields: [], subject_ref_count: 0, subject_id: null, auto_open_allowed: true,
      }],
    })
    const textarea = host.querySelector('textarea')!
    textarea.value = '月底班会'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    host.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()

    expect(opened).toHaveLength(1)
  })

  it('does not open a student profile before the student is uniquely matched', async () => {
    const opened: unknown[] = []
    const host = await mountDesk({
      ...conversation('handoff_ready'),
      handoffs: [{
        handoff_id: 'handoff-unmatched',
        draft_id: 'draft-unmatched',
        work_item_id: 'work-unmatched',
        turn_id: 'turn-unmatched',
        domain: 'student_support',
        handling_mode: 'record',
        intent: 'append',
        destination_key: 'class_teacher.student.record',
        draft_revision: 1,
        adoption_state: 'pending',
        missing_fields: ['请选择一名同名学生'],
        subject_ref_count: 0,
        subject_id: null,
        auto_open_allowed: false,
      }],
    }, [], true, {
      onOpenStudentProfile: (item: unknown) => opened.push(item),
      onOpenHandoff: (item: unknown) => opened.push(item),
    })

    host.querySelector<HTMLElement>('[data-work-item="work-unmatched"]')!.click()
    await settle()
    expect(opened).toHaveLength(0)
    expect(host.textContent).toContain('请先在对话里确认是哪名学生，再打开个人档案')
  })

  it('shows only the current conflict draft after a follow-up revision', async () => {
    const base = {
      domain: 'conflict_safety' as const,
      handling_mode: 'sop' as const,
      intent: 'follow_up' as const,
      destination_key: 'class_teacher.affair.sop',
      draft_revision: 1,
      missing_fields: [] as string[],
      subject_ref_count: 2,
      subject_id: null,
      auto_open_allowed: false,
    }
    const host = await mountDesk({
      ...conversation('handoff_ready'),
      handoffs: [
        {
          ...base,
          handoff_id: 'handoff-old-conflict', draft_id: 'draft-old-conflict',
          work_item_id: 'old-conflict-work', turn_id: 'turn-old-conflict',
          adoption_state: 'stale',
        },
        {
          ...base,
          handoff_id: 'handoff-current-conflict', draft_id: 'draft-current-conflict',
          work_item_id: 'current-conflict-work', turn_id: 'turn-current-conflict',
          adoption_state: 'pending',
        },
      ],
    })

    // 过期的旧草稿仍要显示（带「已过期」标记）：隐藏会让教师在模型失败后
    // 彻底找不到已收集的上下文。
    const oldCard = host.querySelector('[data-work-item="old-conflict-work"]')
    expect(oldCard).not.toBeNull()
    expect(oldCard!.textContent).toContain('已过期')
    expect(host.querySelector('[data-work-item="current-conflict-work"]')).not.toBeNull()
    expect(host.querySelectorAll('.handoffs button')).toHaveLength(2)
  })

  it('waits for a running turn before accepting another message', async () => {
    const host = await mountDesk({
      ...conversation('ai_running'), revision: 2,
      turns: [{
        turn_id: 'turn-running-01', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-running-01', teacher_message: '合成运行中内容', assistant_message: null,
        clarification_questions: [], task_id: 'task-running-01', task_state: 'running',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
    })

    expect(host.querySelector<HTMLTextAreaElement>('.composer textarea')!.disabled).toBe(true)
    expect(host.querySelector<HTMLButtonElement>('.composer button[type="submit"]')!.disabled).toBe(true)
    expect(host.textContent).toContain('结果返回后可继续补充')
  })

  it('shows this week work from the existing work graph', async () => {
    const item: WorkNode = {
      node_id: 'node-near-001', kind: 'task', classification: 'ordinary',
      title: '合成近期检查任务', details: null, status: 'pending',
      due_date: '2026-08-06T16:00:00+08:00', revision: 1,
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    }
    const undated: WorkNode = {
      ...item, node_id: 'node-near-002', title: '合成无日期班务', due_date: null,
    }
    const host = await mountDesk(conversation(), [item, undated])

    expect(host.textContent).toContain('本周事务')
    expect(host.textContent).toContain('按时间看')
    expect(host.textContent).toContain('合成近期检查任务')
    expect(host.textContent).toContain('合成无日期班务')
    expect(workApi.read).toHaveBeenCalledWith('week')
  })

  it('caps the time panel later section and links to the calendar for the rest', async () => {
    const many: WorkNode[] = Array.from({ length: 7 }, (_, index) => ({
      node_id: `node-near-${String(index + 1).padStart(3, '0')}`, kind: 'task', classification: 'ordinary',
      title: `合成事项${index + 1}`, details: null, status: 'pending',
      due_date: '2026-08-06T16:00:00+08:00', revision: 1,
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    }))
    const host = await mountDesk(conversation(), many)

    const panelRows = [...host.querySelectorAll('.time-panel .time-sec button')]
      .map((node) => node.textContent ?? '')
    expect(panelRows.filter((label) => label.includes('合成事项')).length).toBe(3)
    expect(host.textContent).toContain('本周还有 4 项')
    expect(host.textContent).toContain('查看全部')
  })

  it('rejects an arbitrary destination before navigation', () => {
    expect(() => decodeHandoffDraft({
      contract_version: 'teacher_workspace_handoff.v1',
      handoff_id: 'handoff-12345', work_item_id: 'work-item-1234',
      conversation_id: 'conversation-1234', turn_id: 'turn-12345678',
      draft_id: 'draft-12345678', draft_revision: 1, domain: 'student_growth',
      handling_mode: 'record', intent: 'create', destination_key: 'https://example.invalid',
      adoption_id: 'adoption-1234', adoption_state: 'pending', content: {}, subject_refs: [],
      missing_fields: [], return_context: { destination_key: 'class_teacher.home', focus_ref: 'work-item-1234' },
    })).toThrow('无法识别')
  })

  function nearTask(id: string, title: string, extra: Partial<WorkNode> = {}): WorkNode {
    return {
      node_id: id, kind: 'task', classification: 'ordinary',
      title, details: null, status: 'pending',
      due_date: '2026-08-06T16:00:00+08:00', revision: 1,
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z', ...extra,
    }
  }
  function rowOf(host: HTMLElement, title: string): HTMLElement {
    const row = [...host.querySelectorAll<HTMLElement>('.week-strip .rowline')]
      .find((item) => item.textContent?.includes(title))
    if (!row) throw new Error(`row not found: ${title}`)
    return row
  }

  it('completes a card from the week strip and reloads the work list', async () => {
    const item = nearTask('node-qa-001', '合成可完成事项')
    const command = vi.spyOn(workApi, 'command').mockResolvedValue({})
    const host = await mountDesk(conversation(), [item])

    const done = rowOf(host, '合成可完成事项').querySelector<HTMLButtonElement>('.qa__done')
    expect(done).toBeTruthy()
    done!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(command).toHaveBeenCalledWith(item, 'update_status', { status: 'completed' })
    expect(workApi.read).toHaveBeenCalledTimes(2)
    expect(host.textContent).toContain('已完成并归档')
  })

  it('deletes a card only after the irreversible-loss confirmation', async () => {
    const item = nearTask('node-qa-002', '合成待删除事项')
    const command = vi.spyOn(workApi, 'command').mockResolvedValue({})
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const host = await mountDesk(conversation(), [item])

    rowOf(host, '合成待删除事项').querySelector<HTMLButtonElement>('.qa__del')!
      .dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    expect(command).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    rowOf(host, '合成待删除事项').querySelector<HTMLButtonElement>('.qa__del')!
      .dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    expect(command).toHaveBeenCalledWith(item, 'delete')
    expect(host.textContent).toContain('已彻底删除')
  })

  it('withholds the complete button from blocked cards and all quick actions from restricted ones', async () => {
    const blocker = nearTask('node-qa-003', '合成前置任务')
    const blocked = nearTask('node-qa-004', '合成等前置事项')
    const restricted = nearTask('node-qa-005', '学生支持待跟进', {
      kind: 'sop', classification: 'restricted_projection', projection_type: 'student_support',
    })
    const host = await mountDesk(conversation(), [blocker, blocked, restricted], true, {}, [], [
      { source_node_id: 'node-qa-003', target_node_id: 'node-qa-004', relation: 'depends_on' },
    ])

    const blockedRow = rowOf(host, '合成等前置事项')
    expect(blockedRow.querySelector('.qa__done')).toBeNull()
    expect(blockedRow.querySelector('.qa__del')).toBeTruthy()

    const restrictedRow = rowOf(host, '学生支持待跟进')
    expect(restrictedRow.querySelector('.qa')).toBeNull()

    // 普通未阻塞卡片两个按钮都在
    const normalRow = rowOf(host, '合成前置任务')
    expect(normalRow.querySelector('.qa__done')).toBeTruthy()
    expect(normalRow.querySelector('.qa__del')).toBeTruthy()
  })
})
