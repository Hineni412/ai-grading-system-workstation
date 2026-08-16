// @vitest-environment node

import { describe, expect, it } from 'vitest'

import {
  isAdBlockBaitJsFileName,
  JS_CHUNK_FILE_NAME_PATTERN,
} from '../../vite.adblock-filenames'
import { allowedDevRoots, serverConfig } from '../../vite.config'

describe('Vite server configuration', () => {
  it('exposes only the loopback API proxy', () => {
    expect(serverConfig).toEqual({
      host: '127.0.0.1',
      fs: { allow: allowedDevRoots },
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: false,
        },
      },
    })
    expect(allowedDevRoots).toHaveLength(2)
    expect(allowedDevRoots[0]?.split('\\').join('/')).toMatch(/\/frontend\/?$/)
    expect(allowedDevRoots[1]?.split('\\').join('/')).toMatch(/\/components\/answer_region_editor\/?$/)
  })

  it('emits JavaScript chunks with a suffix that ad blockers do not treat as ads', () => {
    expect(JS_CHUNK_FILE_NAME_PATTERN).toBe('assets/[name]-[hash]x.js')
    expect(isAdBlockBaitJsFileName('ConversationDesk-CaON4_AD.js')).toBe(true)
    expect(isAdBlockBaitJsFileName('ConversationDesk-CaON4_ADx.js')).toBe(false)
    expect(isAdBlockBaitJsFileName('chunk-ad.js')).toBe(true)
    expect(isAdBlockBaitJsFileName('AcademicAnalysisPanel-abc123.js')).toBe(false)
  })
})
