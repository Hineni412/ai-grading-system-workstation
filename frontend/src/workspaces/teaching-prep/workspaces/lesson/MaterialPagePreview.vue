<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import { teachingPrepCatalogApi } from '../../api/catalog'

const STAY_MS = 400
const POLL_MS = 400
const POLL_LIMIT = 40

const props = defineProps<{
  units: Array<{
    id?: string
    unit_index: number
    preview_url: string
    title?: string | null
    object_summary?: Record<string, unknown>
    revision?: number
  }>
  page: number
  rangeLabel?: string
  notice?: string
}>()

const emit = defineEmits<{
  'update:page': [number]
  'update:unit': [typeof props.units[number]]
  loaded: [number]
}>()

const indexes = computed(() => (
  [...new Set(props.units.map(item => item.unit_index))].sort((left, right) => left - right)
))
const current = computed(() => (
  props.units.find(item => item.unit_index === props.page) ?? null
))
const currentIndex = computed(() => indexes.value.indexOf(props.page))
const canPrev = computed(() => currentIndex.value > 0)
const canNext = computed(() => (
  currentIndex.value >= 0 && currentIndex.value < indexes.value.length - 1
))
const imageSrc = computed(() => {
  const url = current.value?.preview_url?.trim() ?? ''
  if (!url) return ''
  const summary = current.value?.object_summary ?? {}
  const compositor = String(summary.preview_compositor ?? '0')
  const kind = String(summary.preview_kind ?? 'structural')
  const revision = String(current.value?.revision ?? '0')
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}compose=${encodeURIComponent(compositor)}&kind=${encodeURIComponent(kind)}&rev=${encodeURIComponent(revision)}`
})

let stayTimer: ReturnType<typeof setTimeout> | null = null
let pollTimer: ReturnType<typeof setTimeout> | null = null
let upgradeToken = 0

function clearUpgradeTimers(): void {
  if (stayTimer != null) {
    clearTimeout(stayTimer)
    stayTimer = null
  }
  if (pollTimer != null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function go(delta: number): void {
  const position = currentIndex.value < 0 ? 0 : currentIndex.value
  const next = indexes.value[position + delta]
  if (next != null) emit('update:page', next)
}

function shouldUpgrade(unit: NonNullable<typeof current.value>): boolean {
  if (!unit.id) return false
  const kind = String(unit.object_summary?.preview_kind ?? '')
  if (kind !== 'structural') return false
  const status = String(unit.object_summary?.preview_render_status ?? '')
  if (status === 'failed') {
    const error = String(unit.object_summary?.preview_render_error_code ?? '')
    if (error === 'wps_preview_timeout') return false
    const attempts = Number(unit.object_summary?.preview_render_attempts ?? 0)
    if (error === 'wps_preview_failed' && attempts >= 2) return false
  }
  return true
}

async function upgradeCurrent(token: number): Promise<void> {
  const unit = current.value
  const page = props.page
  if (!unit?.id || !shouldUpgrade(unit) || token !== upgradeToken) return
  try {
    const queued = await teachingPrepCatalogApi.requestPptPreviewRender(unit.id)
    if (token !== upgradeToken || props.page !== page) return
    emit('update:unit', queued)
    let latest = queued
    for (let attempt = 0; attempt < POLL_LIMIT; attempt += 1) {
      const status = String(latest.object_summary.preview_render_status ?? '')
      const kind = String(latest.object_summary.preview_kind ?? '')
      if (kind === 'rendered' || status === 'failed' || status === 'completed') return
      await new Promise<void>((resolve) => {
        pollTimer = setTimeout(() => {
          pollTimer = null
          resolve()
        }, POLL_MS)
      })
      if (token !== upgradeToken || props.page !== page) return
      latest = await teachingPrepCatalogApi.getMaterialUnit(unit.id)
      if (token !== upgradeToken || props.page !== page) return
      emit('update:unit', latest)
    }
  } catch {
    if (token !== upgradeToken) return
  }
}

function scheduleUpgrade(): void {
  clearUpgradeTimers()
  upgradeToken += 1
  const token = upgradeToken
  const unit = current.value
  if (!unit || !shouldUpgrade(unit)) return
  stayTimer = setTimeout(() => {
    stayTimer = null
    void upgradeCurrent(token)
  }, STAY_MS)
}

watch(
  () => [props.page, current.value?.id ?? ''] as const,
  () => { scheduleUpgrade() },
  { immediate: true },
)

onBeforeUnmount(() => {
  upgradeToken += 1
  clearUpgradeTimers()
})
</script>

<template>
  <div class="tp-material-page-preview" data-testid="material-page-preview">
    <header>
      <strong>第 {{ page }} 页{{ current?.title ? ` · ${current.title}` : '原页' }}</strong>
      <span v-if="rangeLabel" class="tp-muted">{{ rangeLabel }}</span>
    </header>
    <p v-if="notice" class="tp-muted">{{ notice }}</p>
    <div class="tp-slide-stage">
      <img
        v-if="imageSrc"
        :src="imageSrc"
        :alt="`第 ${page} 页原页`"
        @load="emit('loaded', page)"
      >
      <div v-else class="tp-slide-stage__paper">
        <p>本页还没有图。没有原页时不建议按页码加入。</p>
      </div>
    </div>
    <div class="tp-inline-actions">
      <AppButton variant="ghost" :disabled="!canPrev" @click="go(-1)">上一页</AppButton>
      <AppButton variant="ghost" :disabled="!canNext" @click="go(1)">下一页</AppButton>
    </div>
  </div>
</template>
