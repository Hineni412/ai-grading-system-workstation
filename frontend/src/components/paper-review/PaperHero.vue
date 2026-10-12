<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PaperReview, PaperDimension } from '../../api/paper-review'
import { band, dimLine, pct, questionTypeName } from './display'
const props = defineProps<{ review: PaperReview; cls: string }>()
defineEmits<{ go: [id: string] }>()
const openKey = ref<PaperDimension['key'] | null>(null)
const open = computed(() => props.review.dimensions.find(d => d.key === openKey.value))
const ranked = computed(() => props.review.dimensions.filter(d => d.score != null).sort((a, b) => (b.score ?? 0) - (a.score ?? 0)))
const best = computed(() => ranked.value[0]), worst = computed(() => ranked.value[ranked.value.length - 1])
const axisCount = computed(() => props.review.dimensions.length)
function angle(i: number) { return -Math.PI / 2 + i * 2 * Math.PI / axisCount.value }
function pt(i: number, fraction: number) {
  const a = angle(i)
  return { x: 170 + 80 * fraction * Math.cos(a), y: 132 + 80 * fraction * Math.sin(a) }
}
function anchor(i: number) { const c = Math.cos(angle(i)); return Math.abs(c) < 0.2 ? 'middle' : c > 0 ? 'start' : 'end' }
function labelDy(i: number) { return Math.sin(angle(i)) > 0.2 ? 13 : 4 }
const alphaLine = computed(() => {
  const r = props.review.reliability
  if (!r) return '信度 α 数据不足'
  return `信度 α ${r.alpha.toFixed(2)} · ${r.alpha >= 0.8 ? '较好' : r.alpha >= 0.6 ? '可接受' : '偏低'}`
})
function partLabel(parent: string, part: string) { return part.startsWith(parent) ? part.slice(parent.length) : part }
function ring(value: number) { return props.review.dimensions.map((d, i) => `${pt(i, value / 100).x},${pt(i, value / 100).y}`).join(' ') }
const polygon = computed(() => props.review.dimensions.flatMap((d, i) => d.score == null ? [] : [`${pt(i, d.score / 100).x},${pt(i, d.score / 100).y}`]).join(' '))
const grade = computed(() => props.review.total == null ? '数据不足' : props.review.total >= 85 ? '优秀' : props.review.total >= 70 ? '良好' : props.review.total >= 60 ? '合格' : '需改进')
function toggle(key: PaperDimension['key']) { openKey.value = openKey.value === key ? null : key }
</script>
<template>
  <section class="pr-card pr-hero">
    <div class="pr-radar">
      <svg viewBox="0 0 340 268" role="img" aria-label="试卷评价维度雷达图">
        <polygon v-for="r in [20,40,60,80,100]" :key="r" :points="ring(r)" fill="none" stroke="#dfe5e1" />
        <g v-for="(d,i) in review.dimensions" :key="d.key" class="pr-axis" tabindex="0" role="button" :aria-label="d.name" @click="toggle(d.key)" @keydown.enter="toggle(d.key)">
          <line x1="170" y1="132" :x2="pt(i,1).x" :y2="pt(i,1).y" :stroke="d.score == null ? '#cdd5d1' : '#bfcac4'" :stroke-dasharray="d.score == null ? '4 4' : undefined" />
          <text :x="pt(i,1.18).x" :y="pt(i,1.18).y + labelDy(i)" :text-anchor="anchor(i)" :fill="d.score == null ? '#98a2a0' : '#59656b'" font-size="12">{{ d.name }}</text>
          <text :x="pt(i,1.18).x" :y="pt(i,1.18).y + labelDy(i) + 16" :text-anchor="anchor(i)" font-size="14" font-weight="700" :fill="d.score == null ? '#98a2a0' : '#202a30'">{{ d.score ?? '数据不足' }}</text>
        </g>
        <polygon :points="polygon" fill="rgba(23,107,117,.18)" stroke="#176b75" stroke-width="1.6" />
        <template v-for="(d,i) in review.dimensions" :key="`point-${d.key}`"><circle v-if="d.score != null" :cx="pt(i,d.score/100).x" :cy="pt(i,d.score/100).y" r="3" fill="#176b75" /></template>
      </svg>
      <p v-if="cls" class="pr-hint pr-center">雷达按全场计算</p>
    </div>
    <div class="pr-total">
      <div class="pr-total-num">{{ review.total ?? '—' }}<small>/100</small></div>
      <span class="pr-hint">加权平均分</span><span class="pr-chip" :class="band(review.total)">{{ grade }}</span>
      <p v-if="best">最强 <b class="pr-chip" :class="band(best.score)">{{ best.name }} {{ best.score }}</b></p>
      <p v-if="worst">最需关注 <b class="pr-chip" :class="band(worst.score)">{{ worst.name }} {{ worst.score }}</b></p>
      <span class="pr-hint">{{ review.students }} 名学生 · {{ review.items.length }} 个评分项</span>
      <span class="pr-hint" title="整卷分数的内部一致性（Cronbach α，按整题计算），不计入总分">{{ alphaLine }}</span>
      <span v-if="ranked.length < axisCount" class="pr-hint">{{ axisCount - ranked.length }} 项数据不足，未计入</span>
    </div>
    <div class="pr-dim-grid">
      <button v-for="d in review.dimensions" :key="d.key" class="pr-dim-card" :class="{ on: openKey === d.key }" :aria-expanded="openKey === d.key" @click="toggle(d.key)">
        <span class="pr-dim-head"><b>{{ d.name }}</b><small>权重 {{ d.weight }}%</small></span>
        <span class="pr-dim-score" :class="band(d.score)">{{ d.score ?? '数据不足' }}</span>
        <span class="pr-bar" :class="band(d.score)"><i :style="{ width: `${d.score ?? 0}%` }"></i></span>
        <span class="pr-dim-line" :title="dimLine(d)">{{ dimLine(d) }}</span>
      </button>
    </div>
  </section>
  <section v-if="open" class="pr-card pr-dim-panel">
    <button class="pr-close" aria-label="关闭维度详情" @click="openKey = null">×</button>
    <h3>{{ open.name }} · {{ open.score == null ? '数据不足' : `${open.score} 分` }}</h3>
    <p v-if="open.score == null" class="pr-hint">该维度数据不足，未计入总分。</p>
    <template v-else-if="open.key === 'coverage'">
      <div v-for="s in open.detail.sections" :key="s.section" class="pr-cov-row"><b>{{ s.section }}</b><span class="pr-bar"><i :style="{width: `${s.ratio*100}%`}"></i></span><b>{{ pct(s.ratio,0) }}</b><span><span v-for="g in s.suggestions.slice(0,2)" :key="g.in_key" class="pr-cov-swap">可考虑用 {{ g.in_name.replace(/^题型·/, '') }} 换 {{ g.out_name.replace(/^题型·/, '') }}</span></span></div>
      <a href="#paper-coverage">查看全部题型 ↓</a>
    </template>
    <template v-else-if="open.key === 'time_fit'">
      <div class="pr-gauge"><span class="pr-gauge-pointer" :style="{left: `${Math.min(open.detail.share,.45)/.45*100}%`}">▼ {{ pct(open.detail.share,0) }}</span><span class="pr-gauge-label" style="left:11.1%">5%</span><span class="pr-gauge-label" style="left:88.9%">40%</span></div>
      <p>最后一题 {{ open.detail.last_item }}：{{ open.detail.last_item_blank_mid }} 名中档生空白</p><p class="pr-hint">空白也可能因为不会做，不全是时间不够。</p>
      <template v-if="open.detail.order_inversions.length">
        <p class="pr-hint">题序倒挂（不计分）</p>
        <p v-for="inv in open.detail.order_inversions" :key="inv.earlier">{{ questionTypeName(inv.type) }} {{ inv.earlier }} 得分率 {{ pct(inv.earlier_rate, 0) }}，排在更容易的 {{ inv.later }}（{{ pct(inv.later_rate, 0) }}）前面</p>
      </template>
    </template>
    <template v-else-if="open.key === 'distribution'">
      <svg class="pr-hist" viewBox="0 0 560 160" role="img" aria-label="得分率直方图">
        <rect x="336" y="14" width="112" height="114" fill="rgba(47,125,92,.09)" /><text x="444" y="10" text-anchor="end" font-size="10" fill="#2f7d5c">均分目标 60至80%</text>
        <g v-for="(count,i) in open.detail.bins" :key="i"><rect :x="i*56+8" :y="128-100*count/Math.max(...open.detail.bins,1)" width="40" :height="100*count/Math.max(...open.detail.bins,1)" rx="3" :fill="i<4 ? '#d08a76' : '#176b75'" /><text :x="i*56+28" y="148" text-anchor="middle" font-size="10">{{ i*10 }}%</text><text :x="i*56+28" :y="123-100*count/Math.max(...open.detail.bins,1)" text-anchor="middle" font-size="10">{{ count || '' }}</text></g>
        <line :x1="open.detail.mean*560" :x2="open.detail.mean*560" y1="14" y2="128" stroke="#202a30" stroke-dasharray="4 3" />
      </svg><p class="pr-hint">红色 = 低于 40% · 阴影 = 均分目标区间 · 均分 {{ pct(open.detail.mean,0) }}</p>
      <div class="pr-mini-gauges"><div v-for="(value,key) in open.detail.parts" :key="key"><span>{{ key === 'mean' ? '均分' : key === 'spread' ? '拉开差距' : '两端人数' }} <b :class="band(value)">{{ value }}</b></span><span class="pr-bar" :class="band(value)"><i :style="{width: `${value}%`}"></i></span></div></div>
    </template>
    <template v-else-if="open.key === 'basic_hits'">
      <div v-for="group in [{name:'考出问题',ids:open.detail.hit_items,color:'good'}, {name:'太容易 >85%',ids:open.detail.too_easy,color:'na'}, {name:'没分出强弱',ids:open.detail.weak,color:'red'}]" :key="group.name"><p>{{ group.name }}（{{ group.ids.length }}）</p><button v-for="id in group.ids" :key="id" class="pr-chip" :class="group.color" @click="$emit('go',id)">{{ id }}</button></div>
    </template>
    <template v-else-if="open.key === 'discrimination'"><button v-for="id in open.detail.low" :key="id" class="pr-chip red" @click="$emit('go',id)">{{ id }}</button><p v-if="!open.detail.low.length">没有区分偏低的评分项。</p></template>
    <template v-else-if="open.key === 'independence'">
      <p v-if="!open.detail.pairs.length" class="pr-hint">没有发现同题型且作答联动的题。</p>
      <div v-for="p in open.detail.pairs" :key="`${p.a}-${p.b}`" class="pr-ind-row">
        <button class="pr-chip red" @click="$emit('go', p.a)">{{ p.a }}</button><button class="pr-chip red" @click="$emit('go', p.b)">{{ p.b }}</button>
        <span>{{ p.types.map(t => t.replace(/^题型·/, '')).join('、') }}</span><span class="pr-hint">作答联动 {{ p.q3.toFixed(2) }}</span>
      </div>
      <template v-if="open.detail.linked_other.length">
        <p class="pr-hint">作答联动但题型不同（不计分，待查原因）</p>
        <div v-for="p in open.detail.linked_other" :key="`other-${p.a}-${p.b}`" class="pr-ind-row">
          <button class="pr-chip na" @click="$emit('go', p.a)">{{ p.a }}</button><button class="pr-chip na" @click="$emit('go', p.b)">{{ p.b }}</button>
          <span class="pr-hint">作答联动 {{ p.q3.toFixed(2) }}</span>
        </div>
      </template>
      <template v-if="open.detail.chains.length">
        <p class="pr-hint">大题内连锁失分（提示，不扣分；前问结果被后问使用时属正常设计）</p>
        <p v-for="c in open.detail.chains" :key="c.parent" class="pr-ind-line">{{ c.parent }}：{{ partLabel(c.parent, c.from) }} 得 0 分的 {{ c.zero_from }} 人中 {{ c.zero_both }} 人 {{ partLabel(c.parent, c.to) }} 也得 0 分（其余学生 {{ pct(c.others_rate, 0) }}）</p>
      </template>
    </template>
  </section>
</template>
