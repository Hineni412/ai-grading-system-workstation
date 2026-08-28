<script setup lang="ts">
import { computed, ref } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'

import type { AffairDetail } from '../api/r1'

const props = defineProps<{
  affair: AffairDetail
  syncBusy: boolean
}>()
const emit = defineEmits<{
  'send-sync': [text: string]
  'open-student': [subjectId: string]
}>()

const aiInput = ref('')

interface StudentChip {
  subjectId: string
  name: string
  line: string
  hasPending: boolean
  confirmed: boolean
}

const studentChips = computed<StudentChip[]>(() => {
  const chips = new Map<string, StudentChip>()
  for (const draft of props.affair.profile_update_drafts ?? []) {
    if (draft.state === 'discarded') continue
    const existing = chips.get(draft.student_ref ?? draft.subject_id)
    const chip: StudentChip = existing ?? {
      subjectId: draft.student_ref ?? draft.subject_id,
      name: draft.display_name || '学生',
      line: '',
      hasPending: false,
      confirmed: false,
    }
    if (draft.state === 'pending') {
      chip.hasPending = true
      if (draft.record_summary) chip.line = firstLine(draft.record_summary)
    }
    if (draft.state === 'confirmed') chip.confirmed = true
    chips.set(chip.subjectId, chip)
  }
  return [...chips.values()]
})

function firstLine(text: string): string {
  const line = text.split('\n').map((item) => item.trim()).find(Boolean) ?? ''
  return line.length > 40 ? `${line.slice(0, 40)}…` : line
}

function send(): void {
  const text = aiInput.value.trim()
  if (!text || props.syncBusy) return
  emit('send-sync', text)
  aiInput.value = ''
}
</script>

<template>
  <aside class="rail">
    <section class="rail__block">
      <h3>还需要你补充</h3>
      <ul v-if="affair.to_verify?.length" class="verify-list">
        <li v-for="item in affair.to_verify" :key="item"><span class="dot" aria-hidden="true" /><span>{{ item }}</span></li>
      </ul>
      <p v-else class="muted">当前没有待补充事项。</p>
    </section>

    <section class="rail__block">
      <div class="ai-box">
        <input
          v-model="aiInput"
          :disabled="syncBusy"
          placeholder="直接说，AI 帮你归位，例如：双方已分开，无人受伤"
          @keyup.enter="send"
        >
        <AppButton variant="primary" :disabled="syncBusy || !aiInput.trim()" @click="send">发送</AppButton>
      </div>
      <p class="muted">发送后 AI 会给出流程修订建议，由你在右侧逐项决定。</p>
    </section>

    <section class="rail__block">
      <h3>学生档案 · 草稿待确认</h3>
      <p v-if="!studentChips.length" class="muted">当前没有待确认的学生档案更新。</p>
      <div v-for="chip in studentChips" :key="chip.subjectId" class="student-chip">
        <button type="button" class="student-chip__head" @click="emit('open-student', chip.subjectId)">
          <i v-if="chip.hasPending" class="pending-dot" aria-label="有待确认内容" />
          <strong>{{ chip.name }}</strong>
          <span class="draft-badge" :class="{ confirmed: chip.confirmed && !chip.hasPending }">{{ chip.hasPending ? '草稿待确认' : '已确认写入' }}</span>
        </button>
        <p v-if="chip.line">{{ chip.line }}</p>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.rail{display:flex;flex-direction:column;gap:18px;padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--muted)}
.rail__block h3{margin:0 0 10px;font-size:14px}
.verify-list{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.verify-list li{display:flex;align-items:baseline;gap:8px;font-size:13px}
.verify-list .dot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--color-warning)}
.ai-box{display:flex;gap:8px}
.ai-box input{flex:1;min-width:0;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;font-size:13px}
.ai-box input:focus-visible{border-color:var(--ring);box-shadow:var(--focus-ring);outline:0}
.muted{margin:6px 0 0;font-size:12px;color:var(--color-text-secondary)}
.student-chip{padding:10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.student-chip+.student-chip{margin-top:8px}
.student-chip__head{display:flex;align-items:center;gap:8px;padding:0;border:0;background:none;font:inherit;cursor:pointer}
.pending-dot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--color-warning)}
.draft-badge{padding:1px 8px;border-radius:999px;background:var(--color-warning-subtle);color:var(--color-warning);font-size:11px}
.draft-badge.confirmed{background:var(--color-success-subtle);color:var(--color-success)}
.student-chip p{margin:6px 0 0;font-size:12px;color:var(--color-text-secondary)}
</style>
