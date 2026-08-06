import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { decodeHandoffDraft, intakeApi, type IntakeConversation } from '../api/intake'
import { workApi, type WorkNode } from '../api/work'
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

async function mountDesk(startValue = conversation(), workNodes: WorkNode[] = []) {
  vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({
    homeroom_class: '一班', revision: 1, classes: ['一班', '二班'], source_revision: 'a'.repeat(64),
  })
  vi.spyOn(intakeApi, 'listConversations').mockResolvedValue([])
  vi.spyOn(intakeApi, 'startConversation').mockResolvedValue(startValue)
  vi.spyOn(intakeApi, 'speechCapabilities').mockResolvedValue({
    available: true, status: 'ready', engine: 'synthetic-local-speech', offline: true,
    sample_rate: 16000, max_duration_seconds: 60, max_audio_bytes: 2_100_000,
    accepted_content_type: 'audio/wav',
    cloud_audio: {
      available: true, status: 'ready', provider: 'volcengine_ark',
      model: 'doubao-seed-2-0-lite-260428', destination_fingerprint: 'fingerprint-1234',
    },
  })
  vi.spyOn(workApi, 'read').mockResolvedValue({
    as_of: '2026-08-05T00:00:00Z', start_date: '2026-08-04', end_date: '2026-08-10',
    nodes: workNodes, edges: [], today: workNodes, overdue: [], waiting: [], review_due: [],
    summary: { today: workNodes.length, overdue: 0, waiting: 0, review_due: 0 },
    view: 'week', cursor: null, source_version: 'synthetic',
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ConversationDesk)
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

    expect(host.querySelector<HTMLButtonElement>('.voice-mode button.is-active')!.textContent).toContain('本机转文字')
    const cloudMode = [...host.querySelectorAll<HTMLButtonElement>('.voice-mode button')]
      .find((item) => item.textContent?.includes('语音给模型'))!
    cloudMode.click()
    await settle()
    host.querySelector<HTMLButtonElement>('[aria-label="录制给模型"]')!.click()
    await settle()
    host.querySelector<HTMLButtonElement>('[aria-label="停止录音"]')!.click()
    await settle()

    expect(host.textContent).toContain('尚未发送')
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
    host.querySelector<HTMLButtonElement>('[aria-label="录制给模型"]')!.click(); await settle()
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

  it('can clear the default class without deleting roster history', async () => {
    const setHomeroom = vi.spyOn(intakeApi, 'setHomeroom').mockResolvedValue({
      homeroom_class: null, revision: 2, classes: ['一班', '二班'], source_revision: 'a'.repeat(64),
    })
    const host = await mountDesk()
    const select = host.querySelector<HTMLSelectElement>('.desk__tools select')!
    select.value = ''
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()

    expect(setHomeroom).toHaveBeenCalledWith(expect.objectContaining({ revision: 1 }), null)
    expect(host.textContent).toContain('学生和历史关系没有删除')
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

  it('shows real today and upcoming work from the existing work graph', async () => {
    const item: WorkNode = {
      node_id: 'node-near-001', kind: 'task', classification: 'ordinary',
      title: '合成近期检查任务', details: null, status: 'pending',
      due_date: '2026-08-06T16:00:00+08:00', revision: 1,
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    }
    const host = await mountDesk(conversation(), [item])

    expect(host.textContent).toContain('今日与接下来')
    expect(host.textContent).toContain('合成近期检查任务')
    expect(workApi.read).toHaveBeenCalledWith('week')
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
})
