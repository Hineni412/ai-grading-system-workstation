import { globalIgnores } from 'eslint/config'
import pluginPlaywright from 'eslint-plugin-playwright'
import pluginVue from 'eslint-plugin-vue'
import pluginVitest from '@vitest/eslint-plugin'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'

export default defineConfigWithVueTs(
  {
    name: 'app/files-to-lint',
    files: ['**/*.{vue,ts,mts,tsx}'],
  },
  globalIgnores([
    '**/dist/**',
    '**/coverage/**',
    '**/node_modules/**',
    '**/playwright-report/**',
    '**/test-results/**',
    'output/**',
    '**/.playwright-cli/**',
    // 本机生成的临时构建/预览产物，不是受检源码
    '**/.tmp-skill-build/**',
    '**/.tmp-skill-preview/**',
    // 仓库根目录的历史一次性脚本（CommonJS），不参与前端源码规则校验
    '.aihot-study.tmp.cjs',
    'proto-screenshot.cjs',
  ]),
  ...pluginVue.configs['flat/essential'],
  vueTsConfigs.recommended,
  {
    ...pluginPlaywright.configs['flat/recommended'],
    files: ['e2e/**/*.{test,spec}.{js,ts,jsx,tsx}'],
  },
  {
    ...pluginVitest.configs.recommended,
    files: ['src/**/__tests__/*'],
  },
  {
    // shadcn-vue CLI 生成的 ui 组件沿用其官方单名约定（Button、Card 等），
    // 不在此处强制 multi-word 规则，避免重命名破坏全部引用。
    name: 'app/shadcn-ui-components',
    files: ['src/components/ui/**/*.vue'],
    rules: {
      'vue/multi-word-component-names': 'off',
    },
  },
)
