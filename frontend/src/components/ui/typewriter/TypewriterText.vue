<!--
  打字机文本：新文本到达时逐字播放一次并带闪烁光标，播完直接定格全文。
  生产化自原型 ui-component-proposals · cards/Card4AiComment.vue 备选 B（setInterval 自绘版），
  播放驱动改为 rAF 时间戳，便于在 jsdom（无 requestAnimationFrame）或 speed<=0 时直接落终态。
-->
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  text: string
  tag?: string
  /** 每个字符的播放间隔（毫秒）；<=0 时直接显示全文 */
  speed?: number
}>(), {
  tag: 'span',
  speed: 30,
})

const displayed = ref('')
const done = ref(true)
let rafId: number | null = null

function prefersReducedMotion(): boolean {
  try {
    return typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

// vitest 的 jsdom 环境开启 pretendToBeVisual，rAF 存在但无真实帧节奏；测试渲染直接落终态。
function isJsdom(): boolean {
  return typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent)
}

function cancelPending(): void {
  if (rafId !== null) cancelAnimationFrame(rafId)
  rafId = null
}

function finish(): void {
  cancelPending()
  displayed.value = props.text
  done.value = true
}

function play(): void {
  cancelPending()
  if (
    !props.text
    || props.speed <= 0
    || typeof requestAnimationFrame === 'undefined'
    || isJsdom()
    || prefersReducedMotion()
  ) {
    finish()
    return
  }
  displayed.value = ''
  done.value = false
  const total = props.text.length
  let start: number | null = null
  const step = (timestamp: number) => {
    rafId = null
    if (start === null) start = timestamp
    const count = Math.min(total, Math.floor((timestamp - start) / props.speed))
    displayed.value = props.text.slice(0, count)
    if (count >= total) {
      done.value = true
      return
    }
    rafId = requestAnimationFrame(step)
  }
  rafId = requestAnimationFrame(step)
}

watch(() => props.text, play, { immediate: true })
onBeforeUnmount(cancelPending)
</script>

<template>
  <component :is="tag" class="typewriter-text">{{ displayed }}<span v-if="!done" class="typewriter-text__cursor" aria-hidden="true">▍</span></component>
</template>

<style scoped>
.typewriter-text__cursor {
  color: var(--primary);
  animation: typewriter-text-blink 0.9s step-end infinite;
}

@keyframes typewriter-text-blink {
  50% {
    opacity: 0;
  }
}
</style>
