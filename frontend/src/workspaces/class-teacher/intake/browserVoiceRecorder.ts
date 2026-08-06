export interface VoiceRecorder {
  start(): Promise<void>
  stop(): Promise<Blob>
  cancel(): void
}

const TARGET_SAMPLE_RATE = 16_000

function supportedMimeType(): string | undefined {
  for (const value of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']) {
    if (MediaRecorder.isTypeSupported(value)) return value
  }
  return undefined
}

export function browserVoiceRecordingSupported(): boolean {
  const environment = globalThis as unknown as {
    navigator?: { mediaDevices?: { getUserMedia?: unknown } }
    MediaRecorder?: unknown
    AudioContext?: unknown
    OfflineAudioContext?: unknown
  }
  return typeof environment.navigator?.mediaDevices?.getUserMedia === 'function'
    && typeof environment.MediaRecorder === 'function'
    && typeof environment.AudioContext === 'function'
    && typeof environment.OfflineAudioContext === 'function'
}

export function encodePcmWav(samples: Float32Array, sampleRate = TARGET_SAMPLE_RATE): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const writeText = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index))
  }
  writeText(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeText(8, 'WAVE')
  writeText(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeText(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index] ?? 0))
    view.setInt16(44 + index * 2, sample < 0 ? sample * 32768 : sample * 32767, true)
  }
  return new Blob([buffer], { type: 'audio/wav' })
}

async function convertRecordingToWav(recording: Blob): Promise<Blob> {
  const audioContext = new AudioContext()
  try {
    const decoded = await audioContext.decodeAudioData(await recording.arrayBuffer())
    const length = Math.max(1, Math.ceil(decoded.duration * TARGET_SAMPLE_RATE))
    const offline = new OfflineAudioContext(1, length, TARGET_SAMPLE_RATE)
    const source = offline.createBufferSource()
    source.buffer = decoded
    source.connect(offline.destination)
    source.start()
    const rendered = await offline.startRendering()
    return encodePcmWav(rendered.getChannelData(0), TARGET_SAMPLE_RATE)
  } finally {
    await audioContext.close()
  }
}

class BrowserVoiceRecorder implements VoiceRecorder {
  private stream: MediaStream | null = null
  private mediaRecorder: MediaRecorder | null = null
  private chunks: Blob[] = []
  private completion: Promise<Blob> | null = null
  private cancelled = false

  async start(): Promise<void> {
    if (!browserVoiceRecordingSupported()) throw new Error('voice_recording_unsupported')
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      video: false,
    })
    const mimeType = supportedMimeType()
    this.mediaRecorder = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined)
    this.chunks = []
    this.cancelled = false
    this.completion = new Promise<Blob>((resolve, reject) => {
      const active = this.mediaRecorder!
      active.addEventListener('dataavailable', (event) => {
        if (event.data.size) this.chunks.push(event.data)
      })
      active.addEventListener('error', () => {
        this.stopTracks()
        reject(new Error('voice_recording_failed'))
      }, { once: true })
      active.addEventListener('stop', () => {
        this.stopTracks()
        if (this.cancelled) {
          resolve(new Blob([], { type: 'audio/wav' }))
          return
        }
        const recording = new Blob(this.chunks, { type: active.mimeType || 'audio/webm' })
        void convertRecordingToWav(recording).then(resolve, reject)
      }, { once: true })
    })
    this.completion.catch(() => undefined)
    this.mediaRecorder.start(250)
  }

  async stop(): Promise<Blob> {
    if (!this.mediaRecorder || !this.completion) throw new Error('voice_recording_not_started')
    if (this.mediaRecorder.state !== 'inactive') this.mediaRecorder.stop()
    return this.completion
  }

  cancel(): void {
    this.cancelled = true
    if (this.mediaRecorder?.state !== 'inactive') this.mediaRecorder?.stop()
    else this.stopTracks()
  }

  private stopTracks(): void {
    for (const track of this.stream?.getTracks() ?? []) track.stop()
    this.stream = null
  }
}

export function createVoiceRecorder(): VoiceRecorder {
  return new BrowserVoiceRecorder()
}
