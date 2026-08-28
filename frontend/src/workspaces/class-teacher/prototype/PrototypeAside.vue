// PROTOTYPE — throwaway, ?variant= 切换，mock 数据，验收后删除
<script setup lang="ts">
import { ref } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'

import type { SopPrototype } from './mock'

const props = withDefaults(defineProps<{ proto: SopPrototype; layout: 'rail' | 'strip'; studentClick?: 'inline' | 'drawer' }>(), {
  studentClick: 'inline',
})
const emit = defineEmits<{ 'open-student': [name: string] }>()

const aiInput = ref('')
const collapsed = ref(false)

function clickStudent(name: string): void {
  if (props.studentClick === 'drawer') emit('open-student', name)
  else props.proto.toggleStudent(name)
}

function send(): void {
  props.proto.sendAiInput(aiInput.value)
  aiInput.value = ''
}
</script>

<template>
  <aside v-if="layout === 'rail'" class="proto-aside">
    <section class="proto-aside__block">
      <h3>还需要你补充</h3>
      <ul class="supplement-list">
        <li v-for="item in proto.state.supplements" :key="item.id" :class="{ done: item.done }">
          <span class="dot" aria-hidden="true" />
          <span class="text">{{ item.text }}</span>
          <em v-if="item.done">已补充</em>
        </li>
      </ul>
    </section>
    <section class="proto-aside__block">
      <div class="ai-box">
        <input
          v-model="aiInput"
          placeholder="直接说，AI 帮你归位，例如：双方已分开，无人受伤"
          @keyup.enter="send"
        >
        <AppButton variant="primary" :disabled="!aiInput.trim()" @click="send">发送</AppButton>
      </div>
      <p v-if="proto.state.aiNotice" class="ai-notice" role="status">{{ proto.state.aiNotice }}</p>
    </section>
    <section class="proto-aside__block">
      <h3>学生档案 · 草稿待确认</h3>
      <div v-for="student in proto.state.students" :key="student.name" class="student-chip">
        <button type="button" class="student-chip__head" @click="clickStudent(student.name)">
          <strong>{{ student.name }}</strong>
          <span v-if="!student.confirmed" class="pending-dot" title="有 AI 待写入的档案内容" aria-label="有 AI 待写入的档案内容" />
          <span class="draft-badge" :class="{ confirmed: student.confirmed }">{{ student.confirmed ? '已确认写入' : '草稿待确认' }}</span>
        </button>
        <p>{{ student.line }}</p>
        <p v-if="studentClick === 'inline' && proto.state.expandedStudent === student.name" class="student-chip__summary">{{ student.summary }}</p>
      </div>
    </section>
  </aside>

  <section v-else class="proto-strip">
    <button type="button" class="proto-strip__toggle" @click="collapsed = !collapsed">
      <strong>还需要你补充 {{ proto.state.supplements.filter((i) => !i.done).length }} 项</strong>
      <span>{{ collapsed ? '展开 ▾' : '收起 ▴' }}</span>
    </button>
    <div v-show="!collapsed" class="proto-strip__body">
      <ul class="supplement-list supplement-list--inline">
        <li v-for="item in proto.state.supplements" :key="item.id" :class="{ done: item.done }">
          <span class="dot" aria-hidden="true" />
          <span class="text">{{ item.text }}</span>
          <em v-if="item.done">已补充</em>
        </li>
      </ul>
      <div class="ai-box ai-box--inline">
        <input
          v-model="aiInput"
          placeholder="直接说，AI 帮你归位，例如：双方已分开，无人受伤"
          @keyup.enter="send"
        >
        <AppButton variant="primary" :disabled="!aiInput.trim()" @click="send">发送</AppButton>
      </div>
      <p v-if="proto.state.aiNotice" class="ai-notice" role="status">{{ proto.state.aiNotice }}</p>
    </div>
  </section>
</template>

<style scoped>
.proto-aside{display:flex;flex-direction:column;gap:18px;padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--muted)}
.proto-aside__block h3{margin:0 0 10px;font-size:14px}
.supplement-list{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.supplement-list li{display:flex;align-items:baseline;gap:8px;font-size:13px}
.supplement-list .dot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--color-warning)}
.supplement-list li.done{color:var(--muted-foreground)}
.supplement-list li.done .dot{background:var(--color-success,#16a34a)}
.supplement-list li.done .text{text-decoration:line-through}
.supplement-list em{margin-left:auto;font-size:11px;font-style:normal;color:var(--color-success,#16a34a)}
.supplement-list--inline{display:flex;flex-wrap:wrap;gap:14px}
.ai-box{display:flex;gap:8px}
.ai-box input{flex:1;min-width:0;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;font-size:13px}
.ai-box input:focus-visible{border-color:var(--ring);box-shadow:var(--focus-ring);outline:0}
.ai-notice{margin:8px 0 0;font-size:12px;color:var(--primary)}
.student-chip{padding:10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.student-chip+.student-chip{margin-top:8px}
.student-chip__head{display:flex;align-items:center;gap:8px;padding:0;border:0;background:none;font:inherit;cursor:pointer}
.pending-dot{width:8px;height:8px;border-radius:50%;background:var(--color-warning);box-shadow:0 0 0 3px var(--color-warning-subtle)}
.draft-badge{padding:1px 8px;border-radius:999px;background:var(--color-warning-subtle);color:var(--color-warning);font-size:11px}
.draft-badge.confirmed{background:var(--color-success-subtle);color:var(--color-success)}
.student-chip p{margin:6px 0 0;font-size:12px;color:var(--color-text-secondary)}
.student-chip__summary{padding-top:6px;border-top:1px dashed var(--border);color:var(--foreground)}
.proto-strip{border:1px solid var(--border);border-radius:var(--radius);background:var(--muted)}
.proto-strip__toggle{display:flex;width:100%;align-items:center;justify-content:space-between;padding:10px 16px;border:0;background:none;font:inherit;cursor:pointer}
.proto-strip__toggle span{font-size:12px;color:var(--color-text-secondary)}
.proto-strip__body{display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px;padding:0 16px 14px}
.ai-box--inline{flex:1;min-width:260px}
.proto-strip .ai-notice{width:100%}
</style>
