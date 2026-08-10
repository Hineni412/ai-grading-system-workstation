<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { intakeApi, type HandoffDraft, type IntakeConversation } from '../api/intake'
import {
  studentR1Api,
  type CurrentStudentProfile,
  type DirectorySubject,
  type StudentCard,
  type StudentProfileDimension,
  type StudentSupportFocus,
} from '../api/r1'

const props = defineProps<{ subject: DirectorySubject }>()
const emit = defineEmits<{ close: []; open: [panel: 'support' | 'academic'] }>()

const card = ref<StudentCard | null>(null)
const conversation = ref<IntakeConversation | null>(null)
const proposal = ref<HandoffDraft | null>(null)
const message = ref('')
const state = ref<'loading' | 'ready' | 'error'>('loading')
const busy = ref(false)
const notice = ref('')
const error = ref('')
let pollTimer: number | null = null
let pollGeneration = 0

const profile = computed<CurrentStudentProfile>(() => card.value?.current_profile ?? ({
  entry_id: null,
  revision: 0,
  summary: '',
  dimensions: [],
  open_questions: [],
  support_focus: [],
  updated_at: null,
}))
const proposedProfile = computed<CurrentStudentProfile | null>(() => {
  const raw = proposal.value?.content.profile_update
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const item = raw as Record<string, unknown>
  if (typeof item.summary !== 'string' || !Array.isArray(item.dimensions)) return null
  return {
    entry_id: profile.value.entry_id,
    revision: profile.value.revision,
    summary: item.summary,
    dimensions: item.dimensions as StudentProfileDimension[],
    open_questions: Array.isArray(item.open_questions) ? item.open_questions.map(String) : [],
    support_focus: Array.isArray(item.support_focus) ? item.support_focus as StudentSupportFocus[] : [],
    updated_at: profile.value.updated_at,
  }
})
const hasProfile = computed(() => Boolean(profile.value.summary || profile.value.dimensions.length))
const supportPlans = computed(() => card.value?.support_plans ?? [])
type DrawerTab = 'overview' | 'support' | 'academic'
const activeTab = ref<DrawerTab>('overview')
const dialog = ref<HTMLElement | null>(null)
const messageInput = ref<HTMLTextAreaElement | null>(null)
const sourceDetails = ref<HTMLDetailsElement | null>(null)
let previouslyFocused: HTMLElement | null = null
let previousBodyOverflow = ''

function selectTab(tab: DrawerTab): void {
  activeTab.value = tab
}

async function continueProfile(): Promise<void> {
  activeTab.value = 'support'
  await nextTick()
  messageInput.value?.focus()
}

async function showSourceMaterials(): Promise<void> {
  activeTab.value = 'support'
  await nextTick()
  if (sourceDetails.value) sourceDetails.value.open = true
  sourceDetails.value?.focus()
}

function closeDrawer(): void {
  emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeDrawer()
}

function stopPolling(): void {
  pollGeneration += 1
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
}

async function load(): Promise<void> {
  state.value = 'loading'
  error.value = ''
  try {
    card.value = await studentR1Api.studentCard(props.subject.subject_id)
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

async function loadProposal(next: IntakeConversation): Promise<void> {
  const handoff = [...next.handoffs]
    .reverse()
    .find(item => ['pending', 'opened'].includes(item.adoption_state) && item.destination_key === 'class_teacher.student.record')
  if (!handoff) {
    proposal.value = null
    return
  }
  proposal.value = await intakeApi.handoff(handoff.handoff_id)
}

function poll(id: string): void {
  stopPolling()
  const generation = pollGeneration
  const read = async () => {
    if (generation !== pollGeneration) return
    try {
      const next = await intakeApi.conversation(id)
      if (generation !== pollGeneration) return
      conversation.value = next
      if (next.state === 'ai_running') {
        pollTimer = window.setTimeout(read, 1400)
        return
      }
      pollTimer = null
      await loadProposal(next)
      notice.value = next.state === 'needs_input'
        ? 'AI 找到了值得继续了解的问题。可以直接回答，也可以先应用已经整理好的内容。'
        : proposal.value
          ? 'AI 已把这轮信息合并成当前档案草稿，请核对后应用。'
          : '本轮没有形成可应用的档案更新。'
    } catch {
      pollTimer = window.setTimeout(read, 2400)
    }
  }
  pollTimer = window.setTimeout(read, 700)
}

async function send(): Promise<void> {
  const outgoing = message.value.trim()
  if (!outgoing || busy.value || conversation.value?.state === 'ai_running') return
  busy.value = true
  error.value = ''
  notice.value = 'AI 正在结合这名学生的当前档案整理；可以离开页面，系统不会自动重复发送。'
  try {
    if (!conversation.value) {
      conversation.value = await intakeApi.startStudentConversation(props.subject.subject_id)
    }
    conversation.value = await intakeApi.appendTurn(conversation.value, outgoing)
    message.value = ''
    if (conversation.value.state === 'ai_running') poll(conversation.value.conversation_id)
    else await loadProposal(conversation.value)
  } catch {
    error.value = '这次整理没有完成。已输入内容仍在文本框中，请稍后继续。'
  } finally {
    busy.value = false
  }
}

async function applyProposal(): Promise<void> {
  if (!proposal.value || !proposedProfile.value || busy.value) return
  const target = proposal.value.subject_refs[0]?.revision
  if (!target) {
    error.value = '这份草稿没有绑定当前学生，请重新整理。'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await intakeApi.adopt(proposal.value, target)
    proposal.value = null
    await load()
    notice.value = '当前学生档案已经更新。下一轮对话会从这份新档案继续整理。'
    if (conversation.value) conversation.value = await intakeApi.conversation(conversation.value.conversation_id)
  } catch {
    error.value = '档案在保存前发生了变化。这份草稿仍保留，请刷新后重新核对。'
  } finally {
    busy.value = false
  }
}

async function discardProposal(): Promise<void> {
  if (!proposal.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await intakeApi.discard(proposal.value.handoff_id)
    proposal.value = null
    notice.value = '这轮档案更新已放弃，当前档案没有改变。'
    if (conversation.value) conversation.value = await intakeApi.conversation(conversation.value.conversation_id)
  } catch {
    error.value = '这轮更新暂时无法放弃，草稿仍然保留，没有写入当前档案。'
  } finally {
    busy.value = false
  }
}

function updatedLabel(value: string | null): string {
  if (!value) return '尚未建立'
  return value.replace('T', ' ').slice(0, 16)
}

function planText(plan: Record<string, unknown>, key: string): string {
  const value = plan[key]
  if (Array.isArray(value)) return value.map(String).join('；')
  return String(value ?? '')
}

watch(() => props.subject.subject_id, async () => {
  stopPolling()
  activeTab.value = 'overview'
  conversation.value = null
  proposal.value = null
  message.value = ''
  await load()
})
onMounted(() => {
  previouslyFocused = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null
  previousBodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKeydown)
  void load()
  void nextTick(() => dialog.value?.focus())
})
onBeforeUnmount(() => {
  stopPolling()
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = previousBodyOverflow
  previouslyFocused?.focus()
})
</script>

<template>
  <div class="dossier-layer">
    <button class="dossier-backdrop" type="button" aria-label="关闭学生档案" @click="closeDrawer" />
    <aside
      ref="dialog"
      class="dossier"
      role="dialog"
      aria-modal="true"
      aria-labelledby="student-dossier-title"
      tabindex="-1"
    >
      <span class="folder-tab" aria-hidden="true">学生当前档案</span>
      <header class="dossier__masthead">
        <div class="identity">
          <span class="identity__seal" aria-hidden="true">{{ subject.display_name.slice(0, 1) }}</span>
          <div>
            <h1 id="student-dossier-title">{{ subject.display_name }}</h1>
            <p>{{ subject.class_label || '未分班' }} · 学号 {{ subject.source_student_id }}</p>
          </div>
        </div>
        <button type="button" class="close" aria-label="关闭学生档案" @click="closeDrawer">×</button>
      </header>

      <nav class="dossier-tabs" aria-label="学生档案分区">
        <button type="button" :aria-selected="activeTab === 'overview'" @click="selectTab('overview')">当前概览</button>
        <button type="button" :aria-selected="activeTab === 'support'" @click="selectTab('support')">成长与支持</button>
        <button type="button" :aria-selected="activeTab === 'academic'" @click="selectTab('academic')">学业证据</button>
      </nav>

      <div v-if="state === 'loading'" class="page-state" role="status">正在展开学生当前档案…</div>
      <div v-else-if="state === 'error'" class="page-state">
        <strong>学生档案暂时无法读取</strong>
        <p>请重新读取；其他学生和原有资料没有改变。</p>
        <button type="button" @click="load">重新读取</button>
      </div>

      <main v-else class="dossier__scroll">
        <section v-show="activeTab === 'overview'" class="tab-panel overview-panel" aria-label="当前概览">
          <blockquote v-if="profile.summary" class="profile-summary">{{ profile.summary }}</blockquote>
          <div v-else class="empty-profile">
            <strong>这份档案还没有开始生长</strong>
            <p>到“成长与支持”写下已经了解的情况，形成第一份结构化档案。</p>
          </div>

          <div class="quick-stats">
            <div><strong>{{ profile.dimensions.length }}</strong><span>当前有效维度</span></div>
            <div><strong>{{ subject.confirmed_entry_count }}</strong><span>已确认记录</span></div>
            <div><strong>{{ updatedLabel(profile.updated_at) }}</strong><span>最近完善</span></div>
          </div>

          <header class="section-heading">
            <div><small>此刻对这名学生的认识</small><h2>当前结构化档案</h2></div>
          </header>
          <div v-if="hasProfile" class="dimension-grid">
            <article v-for="dimension in profile.dimensions" :key="dimension.key" class="dimension">
              <h3>{{ dimension.label }}</h3>
              <ul><li v-for="item in dimension.items" :key="item">{{ item }}</li></ul>
            </article>
          </div>
          <div v-if="profile.support_focus.length" class="support-preview">
            <small>当前最值得关注</small>
            <strong>{{ profile.support_focus[0]?.title }}</strong>
            <p>{{ profile.support_focus[0]?.need }}</p>
            <button type="button" @click="selectTab('support')">查看支持重点与下一步</button>
          </div>
        </section>

        <section v-show="activeTab === 'support'" class="tab-panel support-panel" aria-label="成长与支持">
          <section class="ai-desk" aria-labelledby="ai-desk-title">
            <div class="ai-desk__heading">
              <div><small>和 AI 一起完善这份档案</small><h2 id="ai-desk-title">告诉我最近又了解到了什么</h2></div>
              <p>不用先分类。AI 会结合当前档案整理到合适维度，并提出少量值得继续了解的问题。</p>
            </div>
            <div v-if="conversation?.turns.length" class="dialogue" aria-live="polite">
              <article v-for="turn in conversation.turns" :key="turn.turn_id" class="dialogue__turn">
                <p class="teacher-quote">{{ turn.teacher_message }}</p>
                <div v-if="turn.assistant_message || turn.clarification_questions.length" class="ai-reply">
                  <strong>AI 整理</strong><p v-if="turn.assistant_message">{{ turn.assistant_message }}</p>
                  <ul v-if="turn.clarification_questions.length"><li v-for="question in turn.clarification_questions" :key="question">{{ question }}</li></ul>
                </div>
              </article>
            </div>
            <form class="composer" @submit.prevent="send">
              <textarea
                ref="messageInput"
                v-model="message"
                rows="4"
                maxlength="4000"
                :disabled="busy || conversation?.state === 'ai_running'"
                :placeholder="`例如：${subject.display_name}最近在小组任务中更愿意主动分工，但遇到意见冲突时容易直接退出讨论……`"
              />
              <footer><span>{{ message.length }}/4000</span><button type="submit" :disabled="!message.trim() || busy || conversation?.state === 'ai_running'">{{ conversation?.state === 'ai_running' ? '正在整理当前档案…' : '交给 AI 整理' }}</button></footer>
            </form>
            <p v-if="notice" class="notice" role="status">{{ notice }}</p>
            <p v-if="error" class="error" role="alert">{{ error }}</p>
          </section>

          <section v-if="proposal && proposedProfile" class="proposal" aria-labelledby="proposal-title">
            <header><div><small>本轮拟更新</small><h2 id="proposal-title">把新认识并入当前档案</h2></div><button type="button" :disabled="busy" @click="applyProposal">应用到当前档案</button></header>
            <p class="proposal__summary">{{ proposedProfile.summary }}</p>
            <div class="proposal__grid"><article v-for="dimension in proposedProfile.dimensions" :key="dimension.key"><strong>{{ dimension.label }}</strong><ul><li v-for="item in dimension.items" :key="item">{{ item }}</li></ul></article></div>
            <footer><span>应用后仍然只有一份当前档案。</span><button type="button" class="quiet" :disabled="busy" @click="discardProposal">放弃本轮更新</button></footer>
          </section>

          <div class="support-grid">
            <section class="support-section">
              <header><small>与个人档案直接相连</small><h2>当前学生支持</h2></header>
              <article v-for="focus in profile.support_focus" :key="focus.key">
                <h3>{{ focus.title }}</h3><p>{{ focus.need }}</p>
                <template v-if="focus.effective_methods.length"><strong>已经有效</strong><ul><li v-for="item in focus.effective_methods" :key="item">{{ item }}</li></ul></template>
                <template v-if="focus.next_actions.length"><strong>接下来尝试</strong><ul><li v-for="item in focus.next_actions" :key="item">{{ item }}</li></ul></template>
              </article>
              <p v-if="!profile.support_focus.length" class="compact-empty">暂无明确支持重点。继续完善后，这里会显示已经有效的方法和下一步。</p>
            </section>
            <section class="support-section questions">
              <header><small>后续谈话可以留意</small><h2>仍需了解</h2></header>
              <ol v-if="profile.open_questions.length"><li v-for="item in profile.open_questions" :key="item">{{ item }}</li></ol>
              <p v-else class="compact-empty">当前没有尚待了解的问题。</p>
            </section>
            <section v-if="supportPlans.length" class="support-section active-plans">
              <header><small>正在执行</small><h2>支持方案</h2></header>
              <article v-for="plan in supportPlans" :key="String(plan.support_plan_id || plan.goal)"><strong>{{ planText(plan, 'goal') }}</strong><p>{{ planText(plan, 'support_actions') }}</p></article>
            </section>
          </div>
          <details ref="sourceDetails" class="source-materials" tabindex="-1">
            <summary>参考材料 · {{ card?.existing_records.length || 0 }} 条原始记录</summary>
            <p>这些内容只作为完善当前档案的来源，不再占据学生页的主要位置。</p>
          </details>
        </section>

        <section v-show="activeTab === 'academic'" class="tab-panel academic-panel" aria-label="学业证据">
          <div class="academic-intro"><small>保持名单上下文</small><h2>需要时再进入完整学业证据</h2><p>当前抽屉不重复堆放成绩与图表。完整页面会沿用这名学生，并展示现有考试证据、可比较变化和教师待处理事项。</p></div>
          <div class="quick-stats academic-stats">
            <div><strong>{{ subject.confirmed_entry_count }}</strong><span>已确认档案记录</span></div>
            <div><strong>{{ subject.attention_pending_count }}</strong><span>待处理关注项</span></div>
            <div><strong>{{ updatedLabel(subject.last_confirmed_at) }}</strong><span>最近确认记录</span></div>
          </div>
          <button type="button" class="primary wide" @click="emit('open', 'academic')">打开完整学业证据</button>
        </section>
      </main>

      <footer v-if="state === 'ready'" class="dossier-actions">
        <button type="button" class="quiet" @click="showSourceMaterials">查看 {{ card?.existing_records.length || 0 }} 条原始记录</button>
        <div>
          <button type="button" class="quiet" @click="emit('open', 'support')">支持工作区</button>
          <button v-if="activeTab !== 'academic'" type="button" class="primary" @click="continueProfile">继续完善档案</button>
          <button v-else type="button" class="primary" @click="emit('open', 'academic')">打开学业证据</button>
        </div>
      </footer>
    </aside>
  </div>
</template>

<style scoped>
.dossier-layer{position:fixed;z-index:65;inset:var(--shell-topbar-height,64px) 0 0;color:var(--color-text-primary)}
.dossier-backdrop{position:absolute;inset:0;border:0;background:#142a2d42;cursor:default}
.dossier{position:absolute;inset-block:0;right:0;display:grid;width:min(620px,92vw);grid-template-rows:auto auto minmax(0,1fr) auto;border:0;border-left:1px solid #b7cbc6;outline:0;background:#f8fbfa;box-shadow:-24px 0 80px #1130352e;animation:drawer-in .22s ease-out}
.folder-tab{position:absolute;top:78px;left:-42px;width:42px;padding:11px 9px;border-radius:10px 0 0 10px;background:#064e51;color:#fff;font-family:"STKaiti","KaiTi",serif;letter-spacing:.12em;text-align:center;writing-mode:vertical-rl;box-shadow:-7px 8px 18px #1130351f}
.dossier__masthead{display:flex;align-items:center;gap:13px;padding:18px 20px;border-bottom:1px solid #d8e2df;background:#fffffff5}.identity{display:flex;min-width:0;align-items:center;gap:13px;flex:1}.identity__seal{display:grid;width:46px;height:46px;place-items:center;flex:0 0 auto;border-radius:14px;background:#0b6769;color:#fff;font-family:"STKaiti","KaiTi",serif;font-size:25px}.identity h1{margin:0;font-size:22px}.identity p{margin:2px 0 0;color:#65777a;font-size:13px}.close{width:40px;height:40px;border:1px solid #c5d5d1;border-radius:10px;background:#fff;color:#173338;font:inherit;font-size:24px}
.dossier-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:10px 14px;border-bottom:1px solid #d8e2df;background:#f0f6f4}.dossier-tabs button{min-height:38px;border:0;border-radius:8px;background:transparent;color:#65777a;font:inherit}.dossier-tabs button[aria-selected=true]{background:#fff;color:#064e51;font-weight:800;box-shadow:0 2px 8px #103a3c14}
.dossier__scroll{overflow:auto;padding:18px 20px 24px}.tab-panel{display:grid;gap:17px}.page-state{display:grid;place-content:center;justify-items:center;gap:8px;padding:28px;text-align:center}.page-state p{color:#65777a}.page-state button,.quiet,.primary{min-height:40px;padding:0 14px;border-radius:9px;font:inherit;font-weight:750}.page-state button,.quiet{border:1px solid #acc6c0;background:#fff;color:#064e51}.primary{border:1px solid #0b6769;background:#0b6769;color:#fff}.wide{width:100%}
.profile-summary{margin:0;padding:16px 18px;border:0;border-left:4px solid #b98122;background:#fff4d8;color:#4a4027;font-family:"STKaiti","KaiTi","Microsoft YaHei",serif;font-size:20px;line-height:1.65}.empty-profile{display:grid;min-height:160px;place-content:center;padding:20px;text-align:center}.empty-profile p{color:#65777a}.quick-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.quick-stats>div{padding:11px 12px;border:1px solid #d8e2df;border-radius:10px;background:#fff}.quick-stats strong{display:block;color:#064e51;font-family:Bahnschrift,"Segoe UI",sans-serif;font-size:20px}.quick-stats span{color:#65777a;font-size:12px}.section-heading{padding-top:2px;border-bottom:1px solid #d8e2df}.section-heading small,.ai-desk small,.proposal small,.support-section small,.academic-intro small{color:#0b6769;font-size:12px;font-weight:800;letter-spacing:.1em}.section-heading h2,.ai-desk h2,.proposal h2,.support-section h2,.academic-intro h2{margin:2px 0 12px;font-size:20px}.dimension-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.dimension{padding:13px 14px;border:1px solid #d8e2df;border-radius:11px;background:#fff}.dimension h3{margin:0 0 8px;color:#064e51;font-size:16px}.dimension ul,.support-section ul,.support-section ol{margin:0;padding-left:19px;color:#385055;line-height:1.65}.support-preview{display:grid;gap:5px;padding:15px;border:1px solid #e5cf99;border-radius:11px;background:#fffaf0}.support-preview small{color:#866118;font-weight:800}.support-preview p{margin:0;color:#675b3e;line-height:1.55}.support-preview button{justify-self:start;padding:6px 0;border:0;background:transparent;color:#0b6769;font:inherit;font-weight:750}
.ai-desk{overflow:hidden;border:1px solid #b9d0c9;border-radius:16px;background:#fff}.ai-desk__heading{padding:18px 19px 14px;background:linear-gradient(105deg,#dceee9,#f7fbf9 70%)}.ai-desk__heading p{margin:5px 0 0;color:#526762;line-height:1.6}.dialogue{display:grid;gap:12px;max-height:280px;overflow:auto;padding:15px 18px 0}.teacher-quote{justify-self:end;max-width:88%;margin:0;padding:10px 13px;border-radius:14px 14px 4px 14px;background:#e7f0ee;line-height:1.6}.ai-reply{padding:12px 14px;border-left:4px solid #0b6769;background:#f5f8f7}.ai-reply p,.ai-reply ul{margin:5px 0 0;line-height:1.6}.composer{margin:15px 18px 18px;border:1px solid #9fbdb5;border-radius:13px;background:#fff}.composer textarea{box-sizing:border-box;width:100%;padding:13px;border:0;border-radius:13px 13px 0 0;outline:0;resize:vertical;background:transparent;font:inherit;line-height:1.6}.composer:focus-within{border-color:#0b6769;box-shadow:0 0 0 3px #0b67691c}.composer footer{display:flex;align-items:center;justify-content:space-between;padding:9px 10px 9px 13px;border-top:1px solid #e2ece8;color:#71817d;font-size:12px}.composer button,.proposal header>button{min-height:38px;padding:0 14px;border:0;border-radius:9px;background:#0b6769;color:#fff;font:inherit;font-weight:750}.notice,.error{margin:-8px 18px 16px;padding:9px 11px;border-radius:9px}.notice{background:#edf7f3;color:#176151}.error{background:#fff1ef;color:#a53d32}
.proposal{overflow:hidden;border:1px solid #d7b968;border-radius:15px;background:#fffdf6}.proposal>header{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:15px 17px;border-bottom:1px solid #eadba9;background:#fff8df}.proposal__summary{margin:0;padding:16px 17px;font-family:"STKaiti","KaiTi",serif;font-size:19px;line-height:1.6}.proposal__grid{display:grid;grid-template-columns:1fr 1fr;gap:9px;padding:0 17px 16px}.proposal__grid article{padding:11px;border:1px solid #eadfb9;border-radius:10px;background:#fff}.proposal__grid ul{margin:7px 0 0;padding-left:18px}.proposal>footer{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 17px;border-top:1px solid #efe3bc;color:#75652f;font-size:12px}
.support-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.support-section{overflow:hidden;border:1px solid #d8e2df;border-radius:13px;background:#fff}.support-section>header{padding:14px 16px;border-bottom:1px solid #e2ebe8;background:#f3f8f6}.support-section h2{margin-bottom:0}.support-section article{margin:12px;padding:13px;border:1px solid #d7e3df;border-radius:10px}.support-section article h3{margin:0;color:#174d4f}.support-section article p{margin:7px 0;line-height:1.55}.support-section article strong{display:block;margin-top:10px;color:#526762;font-size:12px}.compact-empty{margin:0;padding:16px;color:#647570;line-height:1.6}.questions ol{padding:14px 34px}.active-plans{grid-column:1/-1}.source-materials{padding:14px 16px;border:1px dashed #b9cbc5;border-radius:12px;background:#f8fbfa;color:#5d706b}.source-materials summary{cursor:pointer;font-weight:700}.source-materials p{margin-bottom:0}.academic-intro{padding:20px;border:1px solid #bdd4ce;border-radius:14px;background:#fff}.academic-intro p{margin:0;color:#526762;line-height:1.7}.academic-stats{margin-top:2px}
.dossier-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:13px 18px;border-top:1px solid #d8e2df;background:#fffffff7}.dossier-actions>div{display:flex;gap:8px}.dossier button:focus-visible,.dossier textarea:focus-visible,.source-materials:focus-visible,.source-materials summary:focus-visible{outline:3px solid #d49b2b;outline-offset:2px}
@keyframes drawer-in{from{transform:translateX(24px)}to{transform:none}}
@media(max-width:700px){.dossier{width:100%;border-left:0}.folder-tab{display:none}.dimension-grid,.support-grid,.proposal__grid{grid-template-columns:1fr}.active-plans{grid-column:auto}.dossier-actions{align-items:stretch;flex-direction:column}.dossier-actions>div{display:grid;grid-template-columns:1fr 1fr}.dossier-actions>.quiet{display:none}.quick-stats{grid-template-columns:1fr}.identity p{font-size:12px}.dossier__scroll{padding-inline:14px}}
@media(max-width:430px){.dossier__masthead{padding:14px}.identity__seal{width:42px;height:42px}.identity h1{font-size:20px}.dossier-tabs{padding-inline:8px}.dossier-tabs button{padding-inline:5px}.dossier-actions>div{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.dossier{animation:none}}
</style>
