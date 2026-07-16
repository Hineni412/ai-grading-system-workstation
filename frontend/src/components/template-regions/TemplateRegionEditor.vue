<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// @ts-expect-error The established editor is a framework-neutral JavaScript module.
import answerRegionEditor from '../../../../components/answer_region_editor/editor.js'
import editorMarkup from '../../../../components/answer_region_editor/editor.html?raw'

export interface EditorRegion {
  region_uuid: string
  page: 'front' | 'back'
  region_order: number
  x: number
  y: number
  w: number
  h: number
  mapped_question_id: string | null
  mapping_status: string
  is_confirmed?: boolean
  multi_region_confirmed?: boolean
}

export interface EditorState {
  revision: number
  active_page?: 'front' | 'back'
  regions: EditorRegion[]
  undo_stack?: EditorRegion[][]
  redo_stack?: EditorRegion[][]
  drawer_open?: boolean
}

export interface EditorIssue {
  code: string
  message: string
  region_uuid?: string | null
  question_id?: string | null
}

const props = defineProps<{
  modelValue: EditorState
  images: Record<'front' | 'back', { url: string; width: number; height: number }>
  manualQuestionOptions: Array<{ value: string; label: string }>
  automaticCandidates: string[]
  issues: EditorIssue[]
  readOnly?: boolean
  saveStatus?: string
  activePage?: 'front' | 'back'
}>()

const emit = defineEmits<{
  'update:modelValue': [value: EditorState]
  finish: [value: { revision: number }]
  exit: [value: { revision: number }]
}>()

const host = ref<HTMLElement | null>(null)
let dispose: (() => void) | undefined

function initialize(): void {
  dispose?.()
  if (!host.value) return
  dispose = answerRegionEditor({
    parentElement: host.value,
    data: {
      editor_state: { ...props.modelValue, active_page: props.activePage ?? props.modelValue.active_page },
      images: props.images,
      image_sizes: {
        front: [props.images.front.width, props.images.front.height],
        back: [props.images.back.width, props.images.back.height],
      },
      manual_question_options: props.manualQuestionOptions,
      automatic_candidates: props.automaticCandidates,
      validation_issues: props.issues,
      read_only: props.readOnly,
      save_status: props.saveStatus,
    },
    setStateValue(name: string, value: EditorState) {
      if (name === 'editor_state') emit('update:modelValue', value)
    },
    setTriggerValue(name: string, value: { revision: number }) {
      if (name === 'finish_requested') emit('finish', value)
      if (name === 'exit_requested') emit('exit', value)
    },
  })
}

onMounted(initialize)
watch(
  () => [props.images, props.manualQuestionOptions, props.automaticCandidates, props.issues,
    props.readOnly, props.saveStatus, props.activePage],
  async () => { await nextTick(); initialize() },
  { deep: true },
)
onBeforeUnmount(() => dispose?.())
</script>

<template>
  <div ref="host" class="template-region-editor" v-html="editorMarkup"></div>
</template>
