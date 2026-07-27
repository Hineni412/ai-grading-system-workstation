<script setup lang="ts">
import { ref } from 'vue'

import type { QuestionBankRichBlock } from '../../api/question-bank'
import QuestionInlineSegment from './QuestionInlineSegment.vue'

withDefaults(defineProps<{
  blocks?: QuestionBankRichBlock[]
  fallback?: string | null
  emptyLabel?: string
  imageAlt?: string
  compact?: boolean
  dense?: boolean
  supplementalImageUrls?: string[]
}>(), {
  blocks: () => [],
  fallback: '',
  emptyLabel: '内容暂未录入',
  imageAlt: '题目图片',
  compact: false,
  dense: false,
  supplementalImageUrls: () => [],
})

const failedImages = ref(new Set<string>())

function markImageFailed(url: string): void {
  const next = new Set(failedImages.value)
  next.add(url)
  failedImages.value = next
}
</script>

<template>
  <div class="question-content" :class="{ 'is-compact': compact, 'is-dense': dense }">
    <template v-if="blocks.length">
      <div
        v-for="(block, blockIndex) in blocks"
        :key="`${blockIndex}:${block.kind ?? 'paragraph'}:${block.text.slice(0, 12)}`"
        class="question-content__block"
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

        <div v-if="block.asset_urls.length" class="question-content__media">
          <figure v-for="(url, imageIndex) in block.asset_urls" :key="url">
            <img
              v-if="!failedImages.has(url)"
              :src="url"
              :alt="`${imageAlt}${block.asset_urls.length > 1 ? ` ${imageIndex + 1}` : ''}`"
              loading="lazy"
              @error="markImageFailed(url)"
            >
            <figcaption v-else>图片暂时无法读取，文字内容仍可继续查看。</figcaption>
          </figure>
        </div>
      </div>
    </template>
    <p v-else-if="fallback" class="question-content__fallback">{{ fallback }}</p>
    <p v-else class="question-content__empty">{{ emptyLabel }}</p>

    <div v-if="supplementalImageUrls.length" class="question-content__media question-content__media--supplemental">
      <figure v-for="(url, imageIndex) in supplementalImageUrls" :key="url">
        <img
          v-if="!failedImages.has(url)"
          :src="url"
          :alt="`${imageAlt}${supplementalImageUrls.length > 1 ? ` ${imageIndex + 1}` : ''}`"
          loading="lazy"
          @error="markImageFailed(url)"
        >
        <figcaption v-else>图片暂时无法读取，文字内容仍可继续查看。</figcaption>
      </figure>
    </div>
  </div>
</template>

<style scoped>
.question-content {
  color: var(--color-text-primary, #1c2733);
  font-family: "Songti SC", "SimSun", serif;
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

.question-content__block + .question-content__block {
  margin-top: 10px;
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
  margin: 12px 0;
  max-width: 100%;
  overflow-x: auto;
}

.question-content table {
  border-collapse: collapse;
  font-variant-numeric: lining-nums tabular-nums;
  min-width: min(100%, 420px);
  table-layout: auto;
  width: max-content;
}

.question-content td {
  border: 1px solid var(--color-border, #e2e4e7);
  min-width: 72px;
  padding: 7px 10px;
  text-align: left;
  vertical-align: top;
  white-space: pre-wrap;
}

.question-content.is-dense .question-content__table-wrap {
  margin: 8px 0;
}

.question-content.is-dense table {
  min-width: min(100%, 340px);
}

.question-content.is-dense td {
  min-width: 54px;
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

.question-content__media img {
  background: #fff;
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 8px;
  display: block;
  height: auto;
  max-height: min(520px, 58vh);
  max-width: 100%;
  object-fit: contain;
  padding: 6px;
  width: auto;
}

.question-content.is-compact .question-content__media img {
  max-height: min(380px, 48vh);
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
  max-height: min(300px, 42vh);
  max-width: min(100%, 480px);
  padding: 4px;
}

.question-content.is-dense .question-content__media figcaption,
.question-content.is-dense .question-content__empty {
  font-size: 11px;
}

.question-content__media figcaption,
.question-content__empty {
  color: var(--color-text-tertiary, #6b7684);
  font-family: inherit;
  font-size: 13px;
  margin: 0;
}
</style>
