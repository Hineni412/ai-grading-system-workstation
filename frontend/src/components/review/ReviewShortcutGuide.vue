<script setup lang="ts">
import { Keyboard } from '@lucide/vue'

withDefaults(defineProps<{ mode?: 'batch' | 'deep' }>(), { mode: 'batch' })
</script>

<template>
  <details class="review-shortcut-guide" :class="`review-shortcut-guide--${mode}`">
    <summary><Keyboard :size="14" aria-hidden="true" />快捷键</summary>
    <dl class="review-shortcut-guide__list">
      <template v-if="mode === 'batch'">
        <div>
          <dt><i class="review-shortcut-guide__swatch" aria-hidden="true" /></dt>
          <dd>红框：AI 拿不准或需要人工评分</dd>
        </div>
        <div>
          <dt><kbd>Tab</kbd> / <kbd>Shift+Tab</kbd></dt>
          <dd>下一个 / 上一个红框</dd>
        </div>
        <div>
          <dt><kbd>Enter</kbd></dt>
          <dd>下一个，最后一个确认本题</dd>
        </div>
        <div>
          <dt><kbd>J</kbd> / <kbd>K</kbd></dt>
          <dd>切换选中</dd>
        </div>
        <div>
          <dt><kbd>/</kbd></dt>
          <dd>搜索</dd>
        </div>
      </template>
      <template v-else>
        <div>
          <dt>数字</dt>
          <dd>给分</dd>
        </div>
        <div>
          <dt><kbd>Tab</kbd></dt>
          <dd>下一步</dd>
        </div>
        <div>
          <dt><kbd>Enter</kbd></dt>
          <dd>确认</dd>
        </div>
        <div>
          <dt><kbd>←</kbd> / <kbd>→</kbd></dt>
          <dd>换题</dd>
        </div>
        <div>
          <dt><kbd>↑</kbd> / <kbd>↓</kbd></dt>
          <dd>换人</dd>
        </div>
      </template>
    </dl>
  </details>
</template>
