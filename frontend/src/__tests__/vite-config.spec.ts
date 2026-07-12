// @vitest-environment node

import { describe, expect, it } from 'vitest'

import { serverConfig } from '../../vite.config'

describe('Vite server configuration', () => {
  it('exposes only the loopback API proxy', () => {
    expect(serverConfig).toEqual({
      host: '127.0.0.1',
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: false,
        },
      },
    })
  })
})
