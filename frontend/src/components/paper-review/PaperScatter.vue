<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PaperReview, PaperPoint, Quadrant } from '../../api/paper-review'
import { pct, quadrantColors, quadrantNames } from './display'
const props = defineProps<{ review: PaperReview; cls: string }>()
defineEmits<{ go: [id: string] }>()
const parts = ref(false)
const points = computed<PaperPoint[]>(() => {
  if (parts.value) return props.review.items
  const grouped = new Set(props.review.groups.flatMap(g => g.parts))
  return [...props.review.items.filter(i => !grouped.has(i.id)), ...props.review.groups]
})
const present = computed(() => points.value.filter(p => p.r_rest != null))
const legend: {key: Quadrant; text: string}[] = [
  {key:'good',text:'多数人会，没掌握的人被筛出来'}, {key:'free',text:'几乎都对，看不出差别'},
  {key:'high',text:'做对的多是成绩好的学生'}, {key:'suspect',text:'较难，且好生差生都容易错；查题面或批改'},
]
function x(p: PaperPoint) { return 48 + p.rate * 836 }
function y(p: PaperPoint) { return 300 - (p.r_rest ?? 0) * 260 }
const labeled = computed(() => {
  const placed: {x: number; y: number; w: number; h: number}[] = []
  const offsets = [{dx:8,dy:-6,anchor:'start'}, {dx:8,dy:13,anchor:'start'}, {dx:-8,dy:-6,anchor:'end'}, {dx:0,dy:-12,anchor:'middle'}, {dx:0,dy:21,anchor:'middle'}]
  return present.value.map(point => {
    let labelX = x(point)+8, labelY = y(point)-6, anchor = 'start'
    for (const offset of offsets) {
      const tx = x(point)+offset.dx, ty = y(point)+offset.dy
      const w = point.id.length*7+4, h = 11
      const bx = offset.anchor==='end' ? tx-w : offset.anchor==='middle' ? tx-w/2 : tx
      const box = {x:bx,y:ty-h+2,w,h}
      const hit = placed.some(b=>box.x<b.x+b.w && box.x+box.w>b.x && box.y<b.y+b.h && box.y+box.h>b.y)
      if (!hit && box.x>=46 && box.x+box.w<=886) {
        labelX=tx; labelY=ty; anchor=offset.anchor; placed.push(box); break
      }
    }
    return {...point,labelX,labelY,anchor}
  })
})
</script>
<template>
  <section class="pr-card">
    <header><h2>难度与区分度</h2><label class="pr-hint"><input v-model="parts" type="checkbox" /> 按小问显示</label></header>
    <div class="pr-scatter-body">
      <svg viewBox="0 0 900 344" class="pr-scatter" role="img" :aria-label="`难度与区分度散点图，${present.length} 个点`">
        <rect x="633.2" y="40" width="250.8" height="156" fill="rgba(47,125,92,.06)" /><rect x="48" y="196" width="585.2" height="104" fill="rgba(179,64,42,.05)" />
        <line x1="633.2" x2="633.2" y1="40" y2="300" stroke="#bfcac4" stroke-dasharray="5 4" /><line x1="48" x2="884" y1="196" y2="196" stroke="#bfcac4" stroke-dasharray="5 4" />
        <text x="638" y="52" font-size="10" fill="#657176">0.7</text><text x="52" y="191" font-size="10" fill="#657176">0.4</text>
        <path d="M48 40V300H884" stroke="#cbd4cf" fill="none" />
        <g v-for="t in [0,.2,.4,.6,.8,1]" :key="t"><text :x="48+t*836" y="317" text-anchor="middle" font-size="10" fill="#657176">{{ t.toFixed(1) }}</text><text x="40" :y="300-t*260+3" text-anchor="end" font-size="10" fill="#657176">{{ t.toFixed(1) }}</text></g>
        <text x="466" y="339" text-anchor="middle" font-size="12" fill="#59656b">得分率（越右越容易）</text><text transform="rotate(-90 14 170)" x="14" y="170" text-anchor="middle" font-size="12" fill="#59656b">区分度（越高越能分出强弱）</text>
        <text x="54" y="55" font-size="12" fill="#496579">有难度·能区分</text><text x="878" y="55" font-size="12" text-anchor="end" fill="#2f7d5c">基础能区分</text><text x="54" y="293" font-size="12" fill="#b3402a">可疑</text><text x="878" y="293" font-size="12" text-anchor="end" fill="#7a8380">送分</text>
        <g v-for="p in labeled" :key="p.id" class="pr-scatter-point" role="button" tabindex="0" :aria-label="`${p.id}，得分率 ${pct(p.rate)}，区分度 ${p.r_rest}`" @click="$emit('go',p.id)" @keydown.enter="$emit('go',p.id)">
          <title>{{ p.id }} · 得分率 {{ pct(p.rate) }} · 区分度 {{ p.r_rest }}</title>
          <circle v-if="'parts' in p" :cx="x(p)" :cy="y(p)" r="9" fill="none" :stroke="quadrantColors[p.quadrant]" />
          <circle :cx="x(p)" :cy="y(p)" r="5" :fill="quadrantColors[p.quadrant]" />
          <text :x="p.labelX" :y="p.labelY" :text-anchor="p.anchor" font-size="11" fill="#59656b">{{ p.id }}</text>
        </g>
      </svg>
      <aside class="pr-legend"><div v-for="entry in legend" :key="entry.key"><b :style="{color:quadrantColors[entry.key]}">● {{ quadrantNames[entry.key] }}</b><p>{{ entry.text }}</p></div><p class="pr-hint">带外圈的点是合并大题。{{ cls ? '散点图按全场计算。' : '' }}{{ points.length - present.length ? `${points.length-present.length} 项数据不足，未画点。` : '' }}</p></aside>
    </div>
  </section>
</template>
