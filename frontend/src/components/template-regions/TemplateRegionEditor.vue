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
let editorPage: 'front' | 'back' = 'front'

function initialize(): void {
  const continuousDrawing = host.value?.querySelector('[data-role="editor-root"]')
    ?.classList.contains('is-create-mode') ?? false
  dispose?.()
  if (!host.value) return
  editorPage = props.activePage ?? props.modelValue.active_page ?? 'front'
  dispose = answerRegionEditor({
    parentElement: host.value,
    data: {
      editor_state: { ...props.modelValue, active_page: editorPage },
      continuous_drawing: continuousDrawing,
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
      if (name === 'editor_state') {
        editorPage = value.active_page ?? editorPage
        emit('update:modelValue', value)
      }
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
    props.readOnly],
  async () => { await nextTick(); initialize() },
  { deep: true },
)
watch(() => props.activePage, async page => {
  // A page emitted by this editor is an acknowledgement, not an external switch.
  if (page === undefined || page === editorPage) return
  await nextTick()
  initialize()
})
watch(
  () => [props.saveStatus, props.modelValue.revision],
  ([status, revision]) => {
    const output = host.value?.querySelector<HTMLElement>('[data-role="status-save"]')
    if (output) output.textContent = `${status || '草稿'} · 修订 ${revision}`
  },
)
onBeforeUnmount(() => dispose?.())
</script>

<template>
  <div ref="host" class="template-region-editor" v-html="editorMarkup"></div>
</template>
