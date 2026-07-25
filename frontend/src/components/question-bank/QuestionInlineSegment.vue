<script setup lang="ts">
import type { QuestionBankRichInlineSegment } from '../../api/question-bank'

defineProps<{
  segment: QuestionBankRichInlineSegment
}>()
</script>

<template>
  <br v-if="segment.line_break">
  <sup v-if="segment.superscript">
    <u v-if="segment.underline">{{ segment.text }}</u>
    <template v-else>{{ segment.text }}</template>
  </sup>
  <sub v-else-if="segment.subscript">
    <u v-if="segment.underline">{{ segment.text }}</u>
    <template v-else>{{ segment.text }}</template>
  </sub>
  <u v-else-if="segment.underline">{{ segment.text }}</u>
  <template v-else>{{ segment.text }}</template>
</template>
