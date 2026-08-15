// @vitest-environment node

import { describe, expect, it } from 'vitest'

import { neutralizeAdBlockBaitFileName } from '../../vite.adblock-filenames'
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

  it('rewrites hashed filenames that ad blockers treat as ads', () => {
    expect(neutralizeAdBlockBaitFileName('ConversationDesk-CaON4_AD.js'))
      .toBe('ConversationDesk-CaON4_xx.js')
    expect(neutralizeAdBlockBaitFileName('chunk-ad.js')).toBe('chunk-xx.js')
    expect(neutralizeAdBlockBaitFileName('AcademicAnalysisPanel-abc123.js'))
      .toBe('AcademicAnalysisPanel-abc123.js')
  })
})
