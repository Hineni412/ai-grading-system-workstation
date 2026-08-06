import { describe, expect, it } from 'vitest'

import { encodePcmWav } from '../intake/browserVoiceRecorder'

describe('local voice WAV encoding', () => {
  it('encodes 16 kHz mono PCM without persisting a file', async () => {
    const blob = encodePcmWav(new Float32Array([0, 0.5, -0.5]))
    const view = new DataView(await blob.arrayBuffer())
    const text = (offset: number, length: number) => String.fromCharCode(
      ...Array.from({ length }, (_, index) => view.getUint8(offset + index)),
    )

    expect(blob.type).toBe('audio/wav')
    expect(text(0, 4)).toBe('RIFF')
    expect(text(8, 4)).toBe('WAVE')
    expect(view.getUint16(22, true)).toBe(1)
    expect(view.getUint32(24, true)).toBe(16_000)
    expect(view.getUint16(34, true)).toBe(16)
  })
})
