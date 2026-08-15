import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig, type ServerOptions } from 'vite'

import { neutralizeAdBlockBaitFilenames } from './vite.adblock-filenames'

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
  plugins: [vue(), tailwindcss(), neutralizeAdBlockBaitFilenames()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'echarts',
              test: /node_modules[\\/]echarts[\\/]/,
              priority: 2,
              includeDependenciesRecursively: false,
            },
            {
              name: 'zrender',
              test: /node_modules[\\/]zrender[\\/]/,
              priority: 2,
              includeDependenciesRecursively: false,
            },
          ],
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: serverConfig,
})
