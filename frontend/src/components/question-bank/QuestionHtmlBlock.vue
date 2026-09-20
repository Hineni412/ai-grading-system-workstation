<script setup lang="ts">
import { onMounted, onUpdated, ref } from 'vue'
import 'katex/dist/katex.min.css'

interface KatexApi {
  renderToString(tex: string, options?: { throwOnError?: boolean; displayMode?: boolean }): string
}

const props = withDefaults(defineProps<{
  html: string
  imageAlt?: string
  typesetText?: boolean
}>(), {
  imageAlt: '题目图片',
  typesetText: false,
})

const emit = defineEmits<{
  (event: 'open-image', url: string): void
}>()

const host = ref<HTMLElement | null>(null)
let katexLoading: Promise<KatexApi | null> | undefined

function loadKatex(): Promise<KatexApi | null> {
  katexLoading ??= import('katex')
    .then((module) => module.default as KatexApi)
    .catch(() => null)
  return katexLoading
}

async function hydrateFormulas(): Promise<void> {
  const root = host.value
  if (!root) return
  if (props.typesetText) markLinearFormulas(root)
  const targets = root.querySelectorAll<HTMLElement>('.qm[data-latex]:not(.qm--done)')
  if (!targets.length) return
  const katex = await loadKatex()
  if (!katex) return
  for (const target of targets) {
    const latex = target.dataset.latex ?? ''
    try {
      target.innerHTML = katex.renderToString(latex, {
        throwOnError: true,
        displayMode: target.classList.contains('qm--display'),
      })
      target.classList.add('qm--done')
    } catch {
      // Keep the linear fallback text the backend placed inside the span.
      target.classList.add('qm--done')
    }
  }
}

// Some Word sources use ordinary runs + superscripts rather than OMML.
// Only the assistant opts into typesetting those explicit arithmetic runs.
// Chinese prose, option labels, dates and the original fallback stay intact.
function markLinearFormulas(root: HTMLElement): void {
  let run: Node[] = []
  const flush = () => {
    if (!run.length) return
    const original = run.map(node => {
      if (node instanceof HTMLElement) return node.outerHTML
      const span = document.createElement('span')
      span.textContent = node.textContent
      return span.innerHTML
    }).join('')
    const pattern = /(?:<sup>[+\-−A-Za-z0-9]+<\/sup>|<sub>[A-Za-z0-9]+<\/sub>|&(?:lt|gt);|(?!(?:[A-D][.．、]))[A-Za-z0-9πθ√+\-−×÷=<>≤≥≠²³^_{}()./]|[ \t])+/g
    const updated = original.replace(pattern, fragment => {
      const prefix = fragment.match(/^[ .\t]*/)?.[0] ?? ''
      const suffix = fragment.match(/[ \t]*$/)?.[0] ?? ''
      const formula = fragment.slice(prefix.length, fragment.length - suffix.length)
      if (!/[=+×÷≤≥≠²³√]|<su[pb]>|&(?:lt|gt);/.test(formula)
        && !/[A-Za-z].*[-−]|[-−].*[A-Za-z]/.test(formula)) return fragment
      if (!/[A-Za-z0-9πθ]/.test(formula)) return fragment
      const text = document.createElement('span')
      text.innerHTML = formula.replace(/<sup>([^<]+)<\/sup>/g, '^{$1}').replace(/<sub>([^<]+)<\/sub>/g, '_{$1}')
      const latex = (text.textContent ?? '').replace(/−/g, '-').replace(/²/g, '^{2}').replace(/³/g, '^{3}')
        .replace(/×/g, '\\times ').replace(/÷/g, '\\div ').replace(/≤/g, '\\leq ').replace(/≥/g, '\\geq ')
        .replace(/≠/g, '\\ne ').replace(/π/g, '\\pi ').replace(/θ/g, '\\theta ')
        .replace(/√\(([^()]*)\)/g, '\\sqrt{$1}').replace(/√([A-Za-z0-9]+)/g, '\\sqrt{$1}')
      const span = document.createElement('span')
      span.className = 'qm'
      span.dataset.latex = latex
      span.innerHTML = formula
      return prefix + span.outerHTML + suffix
    })
    if (updated !== original) {
      const template = document.createElement('template')
      template.innerHTML = updated
      run[0]!.parentNode!.insertBefore(template.content, run[0]!)
      run.forEach(node => node.parentNode?.removeChild(node))
    }
    run = []
  }
  for (const node of [...root.childNodes]) {
    if (node.nodeType === Node.TEXT_NODE || (node instanceof HTMLElement && /^(SUP|SUB)$/.test(node.tagName))) run.push(node)
    else {
      flush()
      if (node instanceof HTMLElement && !node.classList.contains('qm') && !/^(IMG|BR)$/.test(node.tagName)) markLinearFormulas(node)
    }
  }
  flush()
}

function handleClick(event: MouseEvent): void {
  const image = (event.target as HTMLElement).closest('img')
  if (image instanceof HTMLImageElement && image.src) {
    emit('open-image', image.src)
  }
}

function handleImageError(event: Event): void {
  const image = event.target
  if (!(image instanceof HTMLImageElement)) return
  const placeholder = document.createElement('span')
  placeholder.className = 'question-html__image-fallback'
  placeholder.textContent = '图片暂时无法读取。'
  image.replaceWith(placeholder)
}

onMounted(hydrateFormulas)
onUpdated(hydrateFormulas)
</script>

<template>
  <!-- Backend-generated controlled HTML (whitelisted tags, escaped text). -->
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div
    ref="host"
    class="question-html"
    v-html="props.html"
    @click="handleClick"
    @error.capture="handleImageError"
  />
</template>

<style scoped>
.question-html {
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.question-html :deep(.qm) {
  white-space: nowrap;
}

.question-html :deep(.qm--display) {
  display: block;
  margin: 6px 0;
  text-align: center;
}

.question-html :deep(.katex) {
  font-size: 1.04em;
}

.question-html :deep(img) {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: zoom-in;
  display: inline-block;
  height: auto;
  margin: 4px 6px 4px 0;
  max-height: min(220px, 34vh);
  max-width: min(100%, 360px);
  object-fit: contain;
  padding: 6px;
  vertical-align: middle;
}

.question-html :deep(table) {
  border-collapse: collapse;
  font-variant-numeric: lining-nums tabular-nums;
  margin: 8px 0;
  max-width: 100%;
  white-space: normal;
}

.question-html :deep(td) {
  border: 1px solid var(--border);
  min-width: 0;
  overflow-wrap: anywhere;
  padding: 7px 10px;
  text-align: left;
  vertical-align: top;
}

.question-html__image-fallback {
  color: var(--color-text-muted);
  display: inline-block;
  font-size: 13px;
  padding: 4px 6px;
}
</style>
