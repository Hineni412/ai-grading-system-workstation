<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

import type { QuestionBankRichBlock } from '../../api/question-bank'
import QuestionInlineSegment from './QuestionInlineSegment.vue'

const props = withDefaults(defineProps<{
  blocks?: QuestionBankRichBlock[]
  fallback?: string | null
  emptyLabel?: string
  imageAlt?: string
  compact?: boolean
  dense?: boolean
  paperMediaFlow?: boolean
  /**
   * Mirrors the Word exporter's compact_standalone_images_with_text split:
   * true  — pair a standalone media group with the preceding text block
   *         (text left, images right, like choice/fill questions in docx);
   * false — keep media groups on their own row but right-aligned
   *         (like answer-space questions in docx);
   * undefined — legacy behavior (left-aligned strips, no pairing).
   */
  compactMediaWithText?: boolean
  supplementalImageUrls?: string[]
  mediaMode?: 'list' | 'detail' | 'paper' | 'review'
}>(), {
  blocks: () => [],
  fallback: '',
  emptyLabel: '内容暂未录入',
  imageAlt: '题目图片',
  compact: false,
  dense: false,
  paperMediaFlow: false,
  compactMediaWithText: undefined,
  supplementalImageUrls: () => [],
  mediaMode: 'list',
})

const failedImages = ref(new Set<string>())
const activeImageUrl = ref<string | null>(null)
const activeImageAlt = ref('')
const imageZoom = ref(1)
const imageViewer = ref<HTMLElement | null>(null)
type RenderGroupKind = 'plain' | 'full' | 'media' | 'media-right' | 'pair' | 'option-grid'
interface RenderGroup {
  key: string
  kind: RenderGroupKind
  blocks: QuestionBankRichBlock[]
}

const OPTION_LABEL_ONLY = /^(?:[A-Da-d][.．、]?){3,4}$/
const OPTION_ITEM_LABEL = /^(?:[A-Da-d][.．、]?)?$/

const renderGroups = computed<RenderGroup[]>(() => {
  const blocks = props.blocks
  if (!blocks.length) return []
  const groups: RenderGroup[] = []
  let index = 0
  while (index < blocks.length) {
    const optionGrid = consumeOptionGrid(blocks, index)
    if (optionGrid !== null) {
      groups.push(optionGrid.group)
      index = optionGrid.next
      continue
    }
    if (!props.paperMediaFlow) {
      groups.push({
        key: `full:${index}`,
        kind: 'full',
        blocks: [blocks[index]!],
      })
      index += 1
      continue
    }
    if (isPaperMediaBlock(blocks[index]!)) {
      const mediaStart = index
      const mediaBlocks: QuestionBankRichBlock[] = []
      while (index < blocks.length && isPaperMediaBlock(blocks[index]!)) {
        mediaBlocks.push(blocks[index]!)
        index += 1
      }
      groups.push({
        key: `media:${mediaStart}`,
        kind: 'media',
        blocks: mediaBlocks,
      })
      continue
    }
    groups.push({
      key: `full:${index}`,
      kind: 'full',
      blocks: [blocks[index]!],
    })
    index += 1
  }
  if (!props.paperMediaFlow) {
    if (groups.length === 1 && groups[0]!.kind !== 'option-grid') {
      return [{ key: 'plain', kind: 'plain', blocks: groups[0]!.blocks }]
    }
    return groups
  }
  return applyCompactMedia(groups)
})

function compactStem(text: string): string {
  return text.replace(/\s+/g, '')
}

function isOptionLabelBlock(block: QuestionBankRichBlock): boolean {
  return (
    block.kind !== 'table'
    && block.asset_urls.length === 0
    && OPTION_LABEL_ONLY.test(compactStem(block.text))
  )
}

function isOptionImageBlock(block: QuestionBankRichBlock): boolean {
  if (block.kind === 'table' || block.asset_urls.length === 0) return false
  return OPTION_ITEM_LABEL.test(compactStem(block.text))
}

function optionImageCount(blocks: readonly QuestionBankRichBlock[]): number {
  return blocks.reduce((total, block) => total + block.asset_urls.length, 0)
}

function consumeOptionGrid(
  blocks: readonly QuestionBankRichBlock[],
  start: number,
): { group: RenderGroup; next: number } | null {
  const first = blocks[start]
  if (first === undefined) return null
  if (isOptionLabelBlock(first)) {
    const images: QuestionBankRichBlock[] = []
    let cursor = start + 1
    while (cursor < blocks.length && isOptionImageBlock(blocks[cursor]!)) {
      images.push(blocks[cursor]!)
      cursor += 1
    }
    const count = optionImageCount(images)
    if (count >= 3 && count <= 4) {
      return {
        group: {
          key: `options:${start}`,
          kind: 'option-grid',
          blocks: [first, ...images],
        },
        next: cursor,
      }
    }
  }
  if (first.asset_urls.length >= 3 && first.asset_urls.length <= 4 && compactStem(first.text).length <= 32) {
    return {
      group: {
        key: `options:${start}`,
        kind: 'option-grid',
        blocks: [first],
      },
      next: start + 1,
    }
  }
  if (!isOptionImageBlock(first)) return null
  const images: QuestionBankRichBlock[] = []
  let cursor = start
  while (cursor < blocks.length && isOptionImageBlock(blocks[cursor]!)) {
    images.push(blocks[cursor]!)
    cursor += 1
  }
  const count = optionImageCount(images)
  if (count < 3 || count > 4) return null
  return {
    group: {
      key: `options:${start}`,
      kind: 'option-grid',
      blocks: images,
    },
    next: cursor,
  }
}

function optionGridCount(group: RenderGroup): number {
  return Math.min(4, Math.max(3, optionImageCount(group.blocks)))
}

// Mirrors word_renderer._compact_image_pairs / add_answer_space behavior for
// the HTML paper preview. Only runs when compactMediaWithText is explicitly set.
function applyCompactMedia(groups: RenderGroup[]): RenderGroup[] {
  if (props.compactMediaWithText === undefined) return groups
  if (!props.compactMediaWithText) {
    return groups.map((group) => (
      group.kind === 'media' ? { ...group, kind: 'media-right' as const } : group
    ))
  }
  const paired: RenderGroup[] = []
  for (const group of groups) {
    const previous = paired[paired.length - 1]
    const previousBlock = previous && previous.blocks.length === 1
      ? previous.blocks[0]
      : undefined
    if (
      group.kind === 'media'
      && previous
      && previous.kind === 'full'
      && previousBlock !== undefined
      && isTextOnlyBlock(previousBlock)
    ) {
      paired[paired.length - 1] = {
        key: `pair:${previous.key}`,
        kind: 'pair',
        blocks: [...previous.blocks, ...group.blocks],
      }
      continue
    }
    paired.push(group)
  }
  return paired
}

function isTextOnlyBlock(block: QuestionBankRichBlock): boolean {
  return (
    block.kind !== 'table'
    && block.asset_urls.length === 0
    && block.text.trim().length > 0
  )
}

// A long text block that also carries images (images were inline in the source
// document). In paper mode these mirror docx behavior too: paired text-left /
// images-right when compacting, right-aligned images otherwise.
function isInlineMediaBlock(block: QuestionBankRichBlock): boolean {
  return (
    props.paperMediaFlow
    && block.kind !== 'table'
    && block.asset_urls.length > 0
    && block.text.replace(/\s+/g, '').length > 32
  )
}

function isPaperMediaBlock(block: QuestionBankRichBlock): boolean {
  if (block.kind === 'table' || block.asset_urls.length === 0) return false
  return block.text.replace(/\s+/g, '').length <= 32
}

function markImageFailed(url: string): void {
  const next = new Set(failedImages.value)
  next.add(url)
  failedImages.value = next
}

function retryImage(url: string): void {
  const next = new Set(failedImages.value)
  next.delete(url)
  failedImages.value = next
}

async function openImage(url: string, alt: string): Promise<void> {
  activeImageUrl.value = url
  activeImageAlt.value = alt
  imageZoom.value = 1
  await nextTick()
  imageViewer.value?.focus()
}

function closeImage(): void {
  activeImageUrl.value = null
  activeImageAlt.value = ''
  imageZoom.value = 1
}

function adjustZoom(delta: number): void {
  imageZoom.value = Math.min(3, Math.max(.5, Number((imageZoom.value + delta).toFixed(2))))
}

function handleViewerKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    closeImage()
  } else if (event.key === '+' || event.key === '=') {
    adjustZoom(.25)
  } else if (event.key === '-') {
    adjustZoom(-.25)
  } else if (event.key === '0') {
    imageZoom.value = 1
  }
}
</script>

<template>
  <div
    class="question-content"
    :class="{
      'is-compact': compact,
      'is-dense': dense,
      'is-paper-media-flow': paperMediaFlow,
      [`is-media-${mediaMode}`]: true,
    }"
  >
    <template v-if="blocks.length">
      <div
        v-for="group in renderGroups"
        :key="group.key"
        class="question-content__render-group"
        :class="{
          'question-content__media-strip': group.kind === 'media' || group.kind === 'media-right',
          'question-content__media-strip--right': group.kind === 'media-right',
          'question-content__option-grid': group.kind === 'option-grid',
          'question-content__pair': group.kind === 'pair',
          'question-content__full-block': group.kind === 'full',
          'question-content__plain-blocks': group.kind === 'plain',
        }"
        :data-option-count="group.kind === 'option-grid' ? optionGridCount(group) : undefined"
        :style="group.kind === 'media' ? { display: 'flex', flexWrap: 'wrap' } : undefined"
      >
        <div
          v-for="(block, blockIndex) in group.blocks"
          :key="`${blockIndex}:${block.kind ?? 'paragraph'}:${block.text.slice(0, 12)}`"
          class="question-content__block"
          :class="{
            'question-content__pair-text': group.kind === 'pair' && blockIndex === 0,
            'question-content__pair-media': group.kind === 'pair' && blockIndex > 0,
            'question-content__option-label': (
              group.kind === 'option-grid' && block.asset_urls.length === 0
            ),
            'question-content__block--inline-media-paired':
              isInlineMediaBlock(block) && compactMediaWithText === true,
            'question-content__block--inline-media-right':
              isInlineMediaBlock(block) && compactMediaWithText === false,
          }"
        >
          <div v-if="block.kind === 'table' && block.rows?.length" class="question-content__table-wrap">
            <table>
              <tbody>
                <tr v-for="(row, rowIndex) in (block.rows ?? [])" :key="rowIndex">
                  <td v-for="(cell, cellIndex) in row.cells" :key="cellIndex">
                    <QuestionInlineSegment
                      v-for="(segment, segmentIndex) in cell.segments"
                      :key="segmentIndex"
                      :segment="segment"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else-if="block.segments?.length" class="question-content__text">
            <QuestionInlineSegment
              v-for="(segment, segmentIndex) in (block.segments ?? [])"
              :key="segmentIndex"
              :segment="segment"
            />
          </p>
          <p v-else-if="block.text" class="question-content__text">{{ block.text }}</p>

          <div
            v-if="block.asset_urls.length"
            class="question-content__media"
            :class="{
              'question-content__media--options': (
                group.kind === 'option-grid' && block.asset_urls.length >= 3
              ),
            }"
            :data-option-count="(
              group.kind === 'option-grid' && block.asset_urls.length >= 3
            ) ? Math.min(4, block.asset_urls.length) : undefined"
          >
            <figure v-for="(url, imageIndex) in block.asset_urls" :key="url">
              <button
                v-if="!failedImages.has(url)"
                type="button"
                class="question-content__image-button"
                :aria-label="`放大查看${imageAlt}${block.asset_urls.length > 1 ? ` ${imageIndex + 1}` : ''}`"
                @click="openImage(url, `${imageAlt}${block.asset_urls.length > 1 ? ` ${imageIndex + 1}` : ''}`)"
              >
                <img
                  :src="url"
                  :alt="`${imageAlt}${block.asset_urls.length > 1 ? ` ${imageIndex + 1}` : ''}`"
                  loading="lazy"
                  @error="markImageFailed(url)"
                >
              </button>
              <figcaption v-else>
                图片暂时无法读取。
                <button type="button" @click="retryImage(url)">重新加载</button>
              </figcaption>
            </figure>
          </div>
        </div>
      </div>
    </template>
    <p v-else-if="fallback" class="question-content__fallback">{{ fallback }}</p>
    <p v-else-if="!supplementalImageUrls.length" class="question-content__empty">{{ emptyLabel }}</p>

    <div v-if="supplementalImageUrls.length" class="question-content__media question-content__media--supplemental">
      <figure v-for="(url, imageIndex) in supplementalImageUrls" :key="url">
        <button
          v-if="!failedImages.has(url)"
          type="button"
          class="question-content__image-button"
          :aria-label="`放大查看${imageAlt}${supplementalImageUrls.length > 1 ? ` ${imageIndex + 1}` : ''}`"
          @click="openImage(url, `${imageAlt}${supplementalImageUrls.length > 1 ? ` ${imageIndex + 1}` : ''}`)"
        >
          <img
            :src="url"
            :alt="`${imageAlt}${supplementalImageUrls.length > 1 ? ` ${imageIndex + 1}` : ''}`"
            loading="lazy"
            @error="markImageFailed(url)"
          >
        </button>
        <figcaption v-else>
          图片暂时无法读取。
          <button type="button" @click="retryImage(url)">重新加载</button>
        </figcaption>
      </figure>
    </div>

    <Teleport to="body">
      <div
        v-if="activeImageUrl"
        ref="imageViewer"
        class="question-image-viewer"
        role="dialog"
        aria-modal="true"
        :aria-label="`${activeImageAlt}大图查看`"
        tabindex="-1"
        @click.self="closeImage"
        @keydown="handleViewerKey"
      >
        <div class="question-image-viewer__toolbar">
          <strong>{{ activeImageAlt }}</strong>
          <span>{{ Math.round(imageZoom * 100) }}%</span>
          <button type="button" aria-label="缩小图片" @click="adjustZoom(-.25)">−</button>
          <button type="button" aria-label="恢复图片原始缩放" @click="imageZoom = 1">复位</button>
          <button type="button" aria-label="放大图片" @click="adjustZoom(.25)">＋</button>
          <button type="button" aria-label="关闭大图" @click="closeImage">关闭</button>
        </div>
        <div class="question-image-viewer__canvas">
          <img
            :src="activeImageUrl"
            :alt="activeImageAlt"
            :style="{ transform: `scale(${imageZoom})` }"
          >
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.question-content {
  color: var(--color-text-primary);
  font-family: var(--font-family-document);
  font-size: 16px;
  line-height: 1.86;
  min-width: 0;
}

.question-content.is-compact {
  font-size: 14px;
  line-height: 1.68;
}

.question-content.is-dense {
  font-size: 13px;
  line-height: 1.62;
}

.question-content__render-group + .question-content__render-group {
  margin-top: 10px;
}

.question-content.is-dense .question-content__render-group + .question-content__render-group {
  margin-top: 6px;
}

.question-content__block + .question-content__block {
  margin-top: 10px;
}

.question-content__media-strip {
  align-items: flex-start;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.question-content__media-strip--right {
  justify-content: flex-end;
}

.question-content__pair {
  align-items: flex-start;
  display: flex;
  gap: 12px;
}

.question-content__pair .question-content__block {
  margin: 0;
}

.question-content__pair-text {
  flex: 1 1 auto;
  min-width: 0;
}

.question-content__pair-media {
  flex: 0 0 32%;
  max-width: 32%;
  min-width: 0;
}

.question-content__pair-media .question-content__media {
  justify-content: flex-end;
  margin-top: 0;
}

.question-content__block--inline-media-paired {
  align-items: flex-start;
  display: flex;
  gap: 12px;
}

.question-content__block--inline-media-paired .question-content__text {
  flex: 1 1 auto;
  min-width: 0;
}

.question-content__block--inline-media-paired .question-content__media {
  flex: 0 0 32%;
  justify-content: flex-end;
  margin-top: 0;
  max-width: 32%;
}

.question-content__block--inline-media-right .question-content__media {
  justify-content: flex-end;
}

.question-content__media-strip .question-content__block {
  flex: 0 1 280px;
  margin: 0;
  max-width: 320px;
  min-width: 160px;
}

.question-content__option-grid {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  min-width: 0;
  width: 100%;
}

.question-content__option-grid[data-option-count="3"] {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.question-content__option-grid .question-content__block {
  margin: 0;
  max-width: none;
  min-width: 0;
  width: auto;
}

.question-content__option-grid .question-content__block + .question-content__block {
  margin-top: 0;
}

.question-content__option-label {
  grid-column: 1 / -1;
}

.question-content__option-grid .question-content__media {
  display: block;
  margin-top: 0;
}

.question-content__media--options {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  width: 100%;
}

.question-content__media--options[data-option-count="3"] {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.question-content__option-grid .question-content__media figure,
.question-content__media--options figure {
  max-width: 100%;
  width: 100%;
}

.question-content__option-grid .question-content__image-button,
.question-content__media--options .question-content__image-button {
  width: 100%;
}

.question-content__block {
  min-width: 0;
  max-width: 100%;
}

.question-content__block + .question-content__block {
  margin-top: 10px;
}

.question-content__full-block {
  display: block;
  width: 100%;
}

.question-content.is-dense .question-content__block + .question-content__block {
  margin-top: 6px;
}

.question-content__text,
.question-content__fallback {
  margin: 0;
  font-variant-numeric: lining-nums tabular-nums;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.question-content :deep(sup),
.question-content :deep(sub) {
  font-size: .72em;
  line-height: 0;
  position: relative;
  vertical-align: baseline;
}

.question-content :deep(sup) {
  top: -.48em;
}

.question-content :deep(sub) {
  bottom: -.18em;
}

.question-content :deep(u) {
  text-decoration-thickness: 1px;
  text-underline-offset: .2em;
}

.question-content__table-wrap {
  display: block;
  margin: 12px 0;
  max-width: 100%;
  overflow: visible;
  width: 100%;
}

.question-content table {
  border-collapse: collapse;
  font-variant-numeric: lining-nums tabular-nums;
  max-width: 100%;
  min-width: 0;
  table-layout: fixed;
  width: 100%;
}

.question-content td {
  border: 1px solid var(--border);
  min-width: 0;
  overflow-wrap: anywhere;
  padding: 7px 10px;
  text-align: left;
  vertical-align: top;
  white-space: pre-wrap;
  word-break: break-word;
}

.question-content.is-dense .question-content__table-wrap {
  margin: 8px 0;
}

.question-content.is-dense table {
  min-width: 0;
}

.question-content.is-dense td {
  min-width: 0;
  padding: 4px 7px;
}

.question-content__media {
  align-items: flex-start;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 10px;
}

.question-content__media--supplemental {
  margin-top: 14px;
}

.question-content__media figure {
  margin: 0;
  max-width: 100%;
  width: fit-content;
}

.question-content__image-button {
  appearance: none;
  background: transparent;
  border: 0;
  color: inherit;
  cursor: zoom-in;
  display: block;
  margin: 0;
  max-width: 100%;
  padding: 0;
}

.question-content__image-button:focus-visible {
  border-radius: 8px;
  outline: 2px solid var(--color-accent);
  outline-offset: 3px;
}

.question-content__media img {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  display: block;
  height: auto;
  max-height: min(220px, 34vh);
  max-width: min(100%, 360px);
  object-fit: contain;
  padding: 6px;
  width: auto;
}

.question-content.is-compact .question-content__media img {
  max-height: min(200px, 32vh);
}

.question-content.is-dense .question-content__media {
  gap: 8px;
  margin-top: 6px;
}

.question-content.is-dense .question-content__media--supplemental {
  margin-top: 8px;
}

.question-content.is-dense .question-content__media img {
  border-radius: 6px;
  max-height: min(220px, 34vh);
  max-width: min(100%, 360px);
  padding: 4px;
}

.question-content.is-media-review .question-content__media img {
  max-height: min(180px, 30vh);
  max-width: min(100%, 280px);
}

.question-content.is-media-paper .question-content__media img {
  max-height: min(300px, 44vh);
  max-width: min(100%, 480px);
}

.question-content.is-media-detail .question-content__media img {
  max-height: min(360px, 52vh);
  max-width: min(100%, 720px);
}

.question-content.is-paper-media-flow .question-content__media img {
  max-height: 220px;
  max-width: min(100%, 360px);
}

.question-content.is-paper-media-flow .question-content__media-strip .question-content__media img {
  max-height: 165px;
  max-width: min(100%, 260px);
}

.question-content__option-grid .question-content__media img,
.question-content__media--options img {
  height: auto;
  max-height: 120px;
  max-width: 100%;
  width: 100%;
}

.question-content.is-dense .question-content__option-grid .question-content__media img,
.question-content.is-media-list .question-content__option-grid .question-content__media img,
.question-content.is-dense .question-content__media--options img,
.question-content.is-media-list .question-content__media--options img {
  max-height: 96px;
}

.question-content.is-media-review .question-content__option-grid .question-content__media img,
.question-content.is-media-review .question-content__media--options img {
  max-height: 110px;
}

.question-content.is-media-detail .question-content__option-grid .question-content__media img,
.question-content.is-media-paper .question-content__option-grid .question-content__media img,
.question-content.is-media-detail .question-content__media--options img,
.question-content.is-media-paper .question-content__media--options img {
  max-height: 160px;
}

.question-content.is-paper-media-flow .question-content__option-grid .question-content__media img {
  max-height: 120px;
  max-width: 100%;
}

@media (max-width: 520px) {
  .question-content__option-grid[data-option-count="4"],
  .question-content__media--options[data-option-count="4"] {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.question-content.is-dense .question-content__media figcaption,
.question-content.is-dense .question-content__empty {
  font-size: 11px;
}

.question-content__media figcaption,
.question-content__empty {
  color: var(--color-text-muted);
  font-family: inherit;
  font-size: 13px;
  margin: 0;
}

.question-content__media figcaption button {
  background: transparent;
  border: 0;
  color: var(--color-accent);
  cursor: pointer;
  font: inherit;
  font-weight: 650;
  padding: 2px 4px;
}

.question-content__media figcaption button:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

.question-image-viewer {
  background: color-mix(in srgb, var(--color-text-primary) 86%, transparent);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  inset: 0;
  padding: 18px;
  position: fixed;
  z-index: 1800;
}

.question-image-viewer__toolbar {
  align-items: center;
  background: var(--card);
  border-radius: var(--radius-control) 10px 0 0;
  display: flex;
  gap: 8px;
  min-width: 0;
  padding: 10px 12px;
}

.question-image-viewer__toolbar strong {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.question-image-viewer__toolbar span {
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
  min-width: 44px;
  text-align: right;
}

.question-image-viewer__toolbar button {
  background: var(--card);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  cursor: pointer;
  min-height: 34px;
  padding: 0 11px;
}

.question-image-viewer__canvas {
  align-items: flex-start;
  background:
    linear-gradient(45deg, var(--color-border-subtle) 25%, transparent 25%),
    linear-gradient(-45deg, var(--color-border-subtle) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, var(--color-border-subtle) 75%),
    linear-gradient(-45deg, transparent 75%, var(--color-border-subtle) 75%),
    var(--card);
  background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  background-size: 16px 16px;
  border-radius: 0 0 10px 10px;
  display: flex;
  justify-content: center;
  overflow: auto;
  padding: 24px;
}

.question-image-viewer__canvas img {
  background: var(--card);
  border: 0;
  border-radius: 0;
  display: block;
  height: auto;
  max-height: none;
  max-width: min(100%, 1400px);
  object-fit: contain;
  padding: 0;
  transform-origin: top center;
  width: auto;
}

@media (max-width: 720px) {
  .question-image-viewer {
    padding: 8px;
  }

  .question-image-viewer__toolbar {
    flex-wrap: wrap;
  }

  .question-image-viewer__toolbar strong {
    flex-basis: 100%;
  }

  .question-image-viewer__canvas {
    padding: 12px;
  }
}
</style>
