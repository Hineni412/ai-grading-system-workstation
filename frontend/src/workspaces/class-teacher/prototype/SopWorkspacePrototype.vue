// PROTOTYPE — throwaway, ?variant= 切换，mock 数据，验收后删除
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppButton from '@/components/design-system/AppButton.vue'

import { createSopPrototypeState } from './mock'
import VariantA from './VariantA.vue'
import VariantB from './VariantB.vue'
import VariantC from './VariantC.vue'

const route = useRoute()
const router = useRouter()
const proto = createSopPrototypeState()

const variants = [
  { key: 'A', label: 'A — 纵向流程图', component: VariantA },
  { key: 'B', label: 'B — 阶段看板', component: VariantB },
  { key: 'C', label: 'C — 当前步骤聚焦', component: VariantC },
] as const

const currentIndex = computed(() => {
  const raw = route.query.variant
  const key = Array.isArray(raw) ? raw[0] : raw
  const index = variants.findIndex((item) => item.key === key)
  return index >= 0 ? index : 0
})
const current = computed(() => variants[currentIndex.value] ?? variants[0]!)

function switchVariant(delta: number): void {
  const next = variants[(currentIndex.value + delta + variants.length) % variants.length]
  if (!next) return
  void router.replace({ query: { ...route.query, variant: next.key } })
}

function onKeydown(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
  if (event.key === 'ArrowLeft') switchVariant(-1)
  if (event.key === 'ArrowRight') switchVariant(1)
}

function backToConversation(): void {
  void router.push('/class-teacher')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <main class="sop-proto">
    <header class="sop-proto__header">
      <div class="sop-proto__title">
        <div class="sop-proto__badges">
          <span class="proto-tag">PROTOTYPE · 一次性原型</span>
          <span class="state-badge">进行中 · 生成即生效</span>
        </div>
        <h1>张立璞与钱肖白 · 信息课冲突处理</h1>
        <div class="participants">
          <span class="chip">张立璞</span>
          <span class="chip">钱肖白</span>
        </div>
      </div>
      <div class="sop-proto__actions">
        <AppButton variant="secondary" :loading="proto.state.updating" loading-label="AI 正在更新" @click="proto.requestAiUpdate">
          让 AI 更新后续步骤
        </AppButton>
        <AppButton variant="danger" @click="proto.discardSop">弃用此 SOP</AppButton>
        <AppButton variant="secondary" @click="backToConversation">返回对话</AppButton>
      </div>
    </header>

    <component :is="current.component" :proto="proto" />

    <div v-if="proto.state.toast" class="proto-toast" role="status">{{ proto.state.toast }}</div>

    <div v-if="proto.state.discarded" class="discarded-mask">
      <div class="discarded-mask__panel">
        <h2>此 SOP 已弃用</h2>
        <p>弃用后它将从工作区移除；可回到对话让 AI 重新生成。（原型演示）</p>
        <div>
          <AppButton variant="secondary" @click="proto.state.discarded = false">撤销弃用（原型）</AppButton>
          <AppButton variant="primary" @click="backToConversation">返回对话</AppButton>
        </div>
      </div>
    </div>

    <nav class="variant-switcher" aria-label="原型变体切换">
      <button type="button" aria-label="上一个变体" @click="switchVariant(-1)">◀</button>
      <span>{{ current.label }}</span>
      <button type="button" aria-label="下一个变体" @click="switchVariant(1)">▶</button>
    </nav>
  </main>
</template>

<style scoped>
.sop-proto{position:relative;display:grid;gap:16px;align-content:start;min-height:100%;padding:16px clamp(12px,2.2vw,30px) 96px;background:var(--background);color:var(--foreground)}
.sop-proto__header{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:20px 24px;border:1px solid var(--border);border-top:3px solid var(--color-warning);border-radius:var(--radius);background:var(--card)}
.sop-proto__badges{display:flex;gap:8px;align-items:center}
.proto-tag{padding:2px 10px;border:2px dashed var(--destructive);border-radius:999px;color:var(--destructive);font-size:11px;font-weight:800;letter-spacing:.06em}
.state-badge{padding:2px 10px;border-radius:999px;background:var(--color-warning-subtle);color:var(--color-warning);font-size:12px;font-weight:700}
.sop-proto__title h1{margin:8px 0 6px;font-size:22px}
.participants{display:flex;gap:8px}
.chip{padding:2px 12px;border:1px solid var(--border);border-radius:999px;background:var(--muted);font-size:12px}
.sop-proto__actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.proto-toast{position:fixed;left:50%;bottom:84px;transform:translateX(-50%);z-index:60;padding:10px 18px;border-radius:var(--radius);background:var(--foreground);color:var(--background);font-size:13px;box-shadow:0 8px 24px rgb(0 0 0/.18)}
.discarded-mask{position:fixed;inset:0;z-index:70;display:grid;place-items:center;background:rgb(0 0 0/.45)}
.discarded-mask__panel{display:grid;gap:12px;justify-items:start;max-width:420px;padding:28px;border-radius:var(--radius);background:var(--card)}
.discarded-mask__panel h2{margin:0}
.discarded-mask__panel p{margin:0;color:var(--color-text-secondary);font-size:13px}
.discarded-mask__panel div{display:flex;gap:8px}
.variant-switcher{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:80;display:flex;align-items:center;gap:14px;padding:8px 14px;border:3px solid #facc15;border-radius:999px;background:#111827;color:#f9fafb;box-shadow:0 10px 30px rgb(0 0 0/.35)}
.variant-switcher span{font-size:13px;font-weight:700;letter-spacing:.04em}
.variant-switcher button{width:30px;height:30px;border:0;border-radius:50%;background:#facc15;color:#111827;font-size:13px;font-weight:800;cursor:pointer}
.variant-switcher button:hover{background:#fde047}
</style>
