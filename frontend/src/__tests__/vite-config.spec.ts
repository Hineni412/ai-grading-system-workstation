// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { isAdBlockBaitJsFileName, JS_CHUNK_FILE_NAME_PATTERN } from '../../vite.adblock-filenames';

describe('Vite server configuration', () => {

  it('emits JavaScript chunks with a suffix that ad blockers do not treat as ads', () => {
    expect(JS_CHUNK_FILE_NAME_PATTERN).toBe('assets/[name]-[hash]x.js')
    expect(isAdBlockBaitJsFileName('ConversationDesk-CaON4_AD.js')).toBe(true)
    expect(isAdBlockBaitJsFileName('ConversationDesk-CaON4_ADx.js')).toBe(false)
    expect(isAdBlockBaitJsFileName('chunk-ad.js')).toBe(true)
    expect(isAdBlockBaitJsFileName('AcademicAnalysisPanel-abc123.js')).toBe(false)
  })
})
