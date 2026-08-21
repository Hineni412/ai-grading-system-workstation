import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig, type ServerOptions } from 'vite'

import { JS_CHUNK_FILE_NAME_PATTERN } from './vite.adblock-filenames'

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
  plugins: [vue(), tailwindcss()],
  build: {
    rolldownOptions: {
      output: {
        chunkFileNames: JS_CHUNK_FILE_NAME_PATTERN,
        entryFileNames: JS_CHUNK_FILE_NAME_PATTERN,
        // 手动分包可能打乱跨 chunk 的模块执行顺序（echarts/zrender 与页面 chunk 之间
        // 会形成循环引用，共享辅助函数尚未初始化就被调用），保持源码执行顺序。
        strictExecutionOrder: true,
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
