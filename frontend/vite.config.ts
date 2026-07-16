import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, type ServerOptions } from 'vite'

export const allowedDevRoots = [
  fileURLToPath(new URL('.', import.meta.url)),
  fileURLToPath(new URL('../components/answer_region_editor', import.meta.url)),
]

export const serverConfig = {
  host: '127.0.0.1',
  fs: {
    allow: allowedDevRoots,
  },
  proxy: {
    '/api': {
      target: 'http://127.0.0.1:8000',
      changeOrigin: false,
    },
  },
} satisfies ServerOptions

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: serverConfig,
})
