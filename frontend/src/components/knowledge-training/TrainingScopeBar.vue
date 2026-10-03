<script setup lang="ts">
import { ref, watch } from 'vue'
import '../../styles/knowledge-overview.css'
const props = defineProps<{ volumeLabel: string; sessionCount: number; classes: string[]; selectedClass: string; scoreFloor: number | null }>()
const emit = defineEmits<{ 'select-class': [name: string]; 'update-score-floor': [value: number | null] }>()
const floorText = ref('')
watch(() => props.scoreFloor, value => { floorText.value = value === null ? '' : String(Math.round(value * 100)) }, { immediate: true })
function applyFloor() {
  const value = String(floorText.value).trim() === '' ? null : Number(floorText.value)
  if (value !== null && (!Number.isFinite(value) || value < 0 || value > 100)) return
  const floor = value === null ? null : value / 100
  if (floor !== props.scoreFloor) emit('update-score-floor', floor)
}
</script>
<template>
  <div class="knowledge-overview-scope-bar training-scope-bar" role="group" aria-label="学生范围">
    <strong>{{ volumeLabel }}</strong><span>本学期 {{ sessionCount }} 场考试 + 已发布训练</span>
    <button type="button" :class="{ 'is-active': !selectedClass }" :aria-pressed="!selectedClass" @click="emit('select-class', '')">全部学生</button>
    <button v-for="name in classes" :key="name" type="button" :class="{ 'is-active': selectedClass === name }" :aria-pressed="selectedClass === name" @click="emit('select-class', name)">{{ /^\d+$/.test(name) ? `${name} 班` : name }}</button>
    <label>最低考试得分率<input v-model="floorText" class="app-input" aria-label="最低考试得分率" type="number" min="0" max="100" placeholder="不限" @blur="applyFloor" @keydown.enter="applyFloor">%</label>
  </div>
</template>
<style scoped>
.training-scope-bar{padding:var(--space-2) var(--space-3);gap:var(--space-2)}
strong{font-size:var(--font-size-dense)}label{display:flex;align-items:center;gap:var(--space-2);margin-left:auto;font-size:var(--font-size-caption);color:var(--color-text-secondary);white-space:nowrap}input{width:65px;min-height:28px;text-align:center;flex:none}
</style>
<style scoped>
.training-scope-bar input.app-input{width:65px;flex:0 0 65px}
</style>
