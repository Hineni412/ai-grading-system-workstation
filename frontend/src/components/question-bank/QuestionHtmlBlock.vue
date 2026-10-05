<script lang="ts">
interface KatexApi {
  renderToString(tex: string, options?: { throwOnError?: boolean; displayMode?: boolean }): string
}
let katexLoading: Promise<KatexApi | null> | undefined
const formulaCache = new Map<string, string>()
function loadKatex(): Promise<KatexApi | null> {
  katexLoading ??= import('katex').then(module => module.default as KatexApi).catch(() => null)
  return katexLoading
}
</script>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import 'katex/dist/katex.min.css'

const props = withDefaults(defineProps<{
  html?: string
  text?: string
  inline?: boolean
  imageAlt?: string
  typesetText?: boolean
}>(), {
  imageAlt: '题目图片',
  html: '',
  text: '',
  inline: false,
  typesetText: false,
})

const emit = defineEmits<{
  (event: 'open-image', url: string): void
}>()

const host = ref<HTMLElement | null>(null)
const renderedHtml = computed(() => {
  const container = document.createElement('div')
  if (props.html) container.innerHTML = props.html
  else container.textContent = props.text
  if (props.inline) {
    container.querySelectorAll('img').forEach(node => node.remove())
    container.querySelectorAll('table').forEach(node => node.replaceWith(node.textContent ?? ''))
    container.querySelectorAll('p,div,br').forEach(node => node.replaceWith(...node.childNodes, ' '))
  }
  return container.innerHTML
})

async function hydrateFormulas(): Promise<void> {
  const root = host.value
  if (!root) return
  if (props.typesetText || props.text) {
    markDelimitedFormulas(root)
    markLinearFormulas(root)
  }
  const targets = root.querySelectorAll<HTMLElement>('.qm[data-latex]:not(.qm--done)')
  if (!targets.length) return
  const katex = await loadKatex()
  if (!katex) return
  for (const target of targets) {
    const latex = target.dataset.latex ?? ''
    try {
      const displayMode = !props.inline && target.classList.contains('qm--display')
      const key = `${displayMode}:${latex}`
      let html = formulaCache.get(key)
      if (html === undefined) {
        html = katex.renderToString(latex, { throwOnError: true, displayMode })
        if (formulaCache.size >= 256) formulaCache.delete(formulaCache.keys().next().value!)
        formulaCache.set(key, html)
      }
      target.innerHTML = html
      target.classList.add('qm--done')
    } catch {
      // Keep the linear fallback text the backend placed inside the span.
      target.classList.add('qm--done')
    }
  }
}

function markDelimitedFormulas(root: HTMLElement): void {
  for (const node of [...root.childNodes]) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent ?? ''
      const matches = [...text.matchAll(/\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]/g)]
      if (!matches.length) continue
      const fragment = document.createDocumentFragment()
      let offset = 0
      for (const match of matches) {
        fragment.append(text.slice(offset, match.index))
        const span = document.createElement('span')
        span.className = match[1] || match[4] ? 'qm qm--display' : 'qm'
        span.dataset.latex = match[1] ?? match[2] ?? match[3] ?? match[4] ?? ''
        span.textContent = match[0]
        fragment.append(span)
        offset = match.index + match[0].length
      }
      fragment.append(text.slice(offset))
      node.replaceWith(fragment)
    } else if (node instanceof HTMLElement && !node.classList.contains('qm')) {
      markDelimitedFormulas(node)
    }
  }
}

// Preserve nested radicands instead of stopping at the first closing parenthesis.
function convertRadicals(value: string): string {
  let result = ''
  for (let i = 0; i < value.length; i += 1) {
    if (value[i] !== '√') { result += value[i]; continue }
    if (value[i + 1] === '(') {
      let end = i + 2
      let depth = 1
      for (; end < value.length && depth; end += 1) {
        if (value[end] === '(') depth += 1
        if (value[end] === ')') depth -= 1
      }
      if (depth) { result += value.slice(i); break }
      result += `\\sqrt{${convertRadicals(value.slice(i + 2, end - 1))}}`
      i = end - 1
    } else {
      const term = value.slice(i + 1).match(/^(?:\d+(?:\.\d+)?|[A-Za-z])/)
      if (term) { result += `\\sqrt{${term[0]}}`; i += term[0].length }
      else result += '√'
    }
  }
  return result
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
    const pattern = /(?:<sup>[+\-−A-Za-z0-9]+<\/sup>|<sub>[A-Za-z0-9]+<\/sub>|&(?:lt|gt);|\\[A-Za-z]+|(?!(?:[A-D][.．、]))[A-Za-z0-9πθ√±∠△+\-−×÷=<>≤≥≠²³^_{}()./]|[ \t])+/g
    const updated = original.replace(pattern, fragment => {
      const prefix = fragment.match(/^[ .\t]*/)?.[0] ?? ''
      const suffix = fragment.match(/[ \t]*$/)?.[0] ?? ''
      const formula = fragment.slice(prefix.length, fragment.length - suffix.length)
      if (/&lt;\/?[A-Za-z]+\s+[A-Za-z]+=/.test(formula)) return fragment
      if (!/[=+×÷≤≥≠²³√±∠△^_]|\\[A-Za-z]+|<su[pb]>|&(?:lt|gt);/.test(formula)
        && !/[A-Za-z].*[-−]|[-−].*[A-Za-z]/.test(formula)) return fragment
      if (!/[A-Za-z0-9πθ]/.test(formula)) return fragment
      const text = document.createElement('span')
      text.innerHTML = formula.replace(/<sup>([^<]+)<\/sup>/g, '^{$1}').replace(/<sub>([^<]+)<\/sub>/g, '_{$1}')
      const latex = convertRadicals(text.textContent ?? '')
        // 连续下划线是填空占位符；原样进 KaTeX 会因裸 _ 解析失败而退化成纯文本。
        .replace(/_{2,}/g, (run) => `\\underline{\\hspace{${(run.length * 0.45).toFixed(2)}em}}`)
        .replace(/−/g, '-').replace(/²/g, '^{2}').replace(/³/g, '^{3}')
        .replace(/×/g, '\\times ').replace(/÷/g, '\\div ').replace(/≤/g, '\\leq ').replace(/≥/g, '\\geq ')
        .replace(/≠/g, '\\ne ').replace(/π/g, '\\pi ').replace(/θ/g, '\\theta ')
        .replace(/±/g, '\\pm ').replace(/∠/g, '\\angle ').replace(/△/g, '\\triangle ')
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
watch([renderedHtml, () => props.typesetText, () => props.inline], hydrateFormulas, { flush: 'post' })
</script>

<template>
  <!-- Controlled HTML or escaped plain text; dynamic tag is always native span/div. -->
  <!-- eslint-disable vue/no-v-html, vue/no-v-text-v-html-on-component -->
  <component
    :is="inline ? 'span' : 'div'"
    ref="host"
    class="question-html"
    :class="{ 'question-html--inline': inline }"
    v-html="renderedHtml"
    @click="handleClick"
    @error.capture="handleImageError"
  />
  <!-- eslint-enable vue/no-v-html, vue/no-v-text-v-html-on-component -->
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
.question-html--inline {
  white-space: nowrap;
}
.question-html--inline :deep(.qm--display) {
  display: inline;
  margin: 0;
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
  font-size: var(--font-size-dense);
  padding: 4px 6px;
}
</style>
