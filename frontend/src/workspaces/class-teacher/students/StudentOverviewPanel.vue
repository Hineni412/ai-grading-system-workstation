<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { intakeApi, type HandoffDraft, type IntakeConversation } from '../api/intake'
import {
  studentR1Api,
  type CurrentStudentProfile,
  type DirectorySubject,
  type StudentCard,
  type StudentProfileDimension,
  type StudentSupportFocus,
} from '../api/r1'

const props = defineProps<{ token: string; subject: DirectorySubject }>()
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

function stopPolling(): void {
  pollGeneration += 1
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
}

async function load(): Promise<void> {
  state.value = 'loading'
  error.value = ''
  try {
    card.value = await studentR1Api.studentCard(props.token, props.subject.subject_id)
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
      conversation.value = await intakeApi.startStudentConversation(props.token, props.subject.subject_id)
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
    await intakeApi.adopt(props.token, proposal.value, target)
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
  conversation.value = null
  proposal.value = null
  message.value = ''
  await load()
})
onMounted(() => { void load() })
onBeforeUnmount(stopPolling)
</script>

<template>
  <aside class="dossier" aria-label="学生当前档案">
    <header class="dossier__masthead">
      <div class="identity">
        <span class="identity__seal" aria-hidden="true">{{ subject.display_name.slice(0, 1) }}</span>
        <div>
          <small>学生当前档案</small>
          <h1>{{ subject.display_name }}</h1>
          <p>{{ subject.class_label || '未分班' }} · 学号 {{ subject.source_student_id }}</p>
        </div>
      </div>
      <div class="masthead__actions">
        <span>最近完善：{{ updatedLabel(profile.updated_at) }}</span>
        <button type="button" class="quiet" @click="emit('open', 'academic')">查看学业证据</button>
        <button type="button" class="close" aria-label="关闭学生档案" @click="emit('close')">×</button>
      </div>
    </header>

    <div v-if="state === 'loading'" class="page-state" role="status">正在展开学生当前档案…</div>
    <div v-else-if="state === 'error'" class="page-state">
      <strong>学生档案暂时无法读取</strong>
      <p>请重新读取；其他学生和原有资料没有改变。</p>
      <button type="button" @click="load">重新读取</button>
    </div>

    <main v-else class="dossier__body">
      <section class="ai-desk" aria-labelledby="ai-desk-title">
        <div class="ai-desk__heading">
          <div>
            <small>和 AI 一起完善这份档案</small>
            <h2 id="ai-desk-title">告诉我最近又了解到了什么</h2>
          </div>
          <p>不用先分类。AI 会结合下方当前档案，整理到合适维度，并提出少量值得继续了解的问题。</p>
        </div>

        <div v-if="conversation?.turns.length" class="dialogue" aria-live="polite">
          <article v-for="turn in conversation.turns" :key="turn.turn_id" class="dialogue__turn">
            <p class="teacher-quote">{{ turn.teacher_message }}</p>
            <div v-if="turn.assistant_message || turn.clarification_questions.length" class="ai-reply">
              <strong>AI 整理</strong>
              <p v-if="turn.assistant_message">{{ turn.assistant_message }}</p>
              <ul v-if="turn.clarification_questions.length">
                <li v-for="question in turn.clarification_questions" :key="question">{{ question }}</li>
              </ul>
            </div>
          </article>
        </div>

        <form class="composer" @submit.prevent="send">
          <textarea
            v-model="message"
            rows="4"
            maxlength="4000"
            :disabled="busy || conversation?.state === 'ai_running'"
            :placeholder="`例如：${subject.display_name}最近在小组任务中更愿意主动分工，但遇到意见冲突时容易直接退出讨论……`"
          />
          <footer>
            <span>{{ message.length }}/4000</span>
            <button type="submit" :disabled="!message.trim() || busy || conversation?.state === 'ai_running'">
              {{ conversation?.state === 'ai_running' ? '正在整理当前档案…' : '交给 AI 整理' }}
            </button>
          </footer>
        </form>
        <p v-if="notice" class="notice" role="status">{{ notice }}</p>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
      </section>

      <section v-if="proposal && proposedProfile" class="proposal" aria-labelledby="proposal-title">
        <header>
          <div><small>本轮拟更新</small><h2 id="proposal-title">把新认识并入当前档案</h2></div>
          <button type="button" :disabled="busy" @click="applyProposal">应用到当前档案</button>
        </header>
        <p class="proposal__summary">{{ proposedProfile.summary }}</p>
        <div class="proposal__grid">
          <article v-for="dimension in proposedProfile.dimensions" :key="dimension.key">
            <strong>{{ dimension.label }}</strong>
            <ul><li v-for="item in dimension.items" :key="item">{{ item }}</li></ul>
          </article>
        </div>
        <footer><span>应用后仍然只有一份当前档案，不会产生历史版本。</span><button type="button" class="quiet" :disabled="busy" @click="discardProposal">放弃本轮更新</button></footer>
      </section>

      <div class="profile-layout">
        <section class="profile-main" aria-labelledby="current-profile-title">
          <header class="section-heading">
            <div><small>此刻对这名学生的认识</small><h2 id="current-profile-title">当前结构化档案</h2></div>
            <span>{{ profile.dimensions.length }} 个有效维度</span>
          </header>
          <blockquote v-if="profile.summary" class="profile-summary">{{ profile.summary }}</blockquote>
          <div v-else class="empty-profile">
            <strong>这份档案还没有开始生长</strong>
            <p>在上方写下你已经了解的情况，AI 会形成第一份结构化档案。</p>
          </div>
          <div v-if="hasProfile" class="dimension-spine">
            <article v-for="dimension in profile.dimensions" :key="dimension.key" class="dimension">
              <h3>{{ dimension.label }}</h3>
              <ul><li v-for="item in dimension.items" :key="item">{{ item }}</li></ul>
            </article>
          </div>
        </section>

        <aside class="profile-side">
          <section class="support-focus">
            <header><small>与个人档案直接相连</small><h2>当前学生支持</h2></header>
            <article v-for="focus in profile.support_focus" :key="focus.key">
              <h3>{{ focus.title }}</h3>
              <p>{{ focus.need }}</p>
              <template v-if="focus.effective_methods.length"><strong>已经有效</strong><ul><li v-for="item in focus.effective_methods" :key="item">{{ item }}</li></ul></template>
              <template v-if="focus.next_actions.length"><strong>接下来尝试</strong><ul><li v-for="item in focus.next_actions" :key="item">{{ item }}</li></ul></template>
            </article>
            <div v-if="!profile.support_focus.length" class="compact-empty">暂无明确支持重点。继续对话后，AI 会结合学生档案提出建议。</div>
            <button type="button" class="support-link" @click="emit('open', 'support')">打开支持工作区</button>
          </section>

          <section class="questions">
            <header><small>后续谈话可以留意</small><h2>仍需了解</h2></header>
            <ol v-if="profile.open_questions.length"><li v-for="item in profile.open_questions" :key="item">{{ item }}</li></ol>
            <p v-else>当前没有尚待了解的问题。</p>
          </section>

          <section v-if="supportPlans.length" class="active-plans">
            <header><small>正在执行</small><h2>支持方案</h2></header>
            <article v-for="plan in supportPlans" :key="String(plan.support_plan_id || plan.goal)">
              <strong>{{ planText(plan, 'goal') }}</strong>
              <p>{{ planText(plan, 'support_actions') }}</p>
            </article>
          </section>
        </aside>
      </div>

      <details class="source-materials">
        <summary>参考材料 · {{ card?.existing_records.length || 0 }} 条原始记录</summary>
        <p>这些内容只作为完善当前档案的来源，不再占据学生页的主要位置。</p>
      </details>
    </main>
  </aside>
</template>

<style scoped>
.dossier{position:fixed;z-index:65;inset:var(--shell-topbar-height) 0 0;overflow:auto;background:#eef4f2;color:var(--color-text-primary)}
.dossier__masthead{position:sticky;z-index:4;top:0;display:flex;align-items:center;justify-content:space-between;gap:24px;padding:18px clamp(20px,4vw,64px);border-bottom:1px solid #c9d8d3;background:color-mix(in srgb,#f8fbfa 94%,transparent);backdrop-filter:blur(16px)}
.identity{display:flex;align-items:center;gap:16px}.identity__seal{display:grid;width:52px;height:52px;place-items:center;border-radius:16px;background:#0b5b5f;color:#fff;font-family:"STKaiti","KaiTi",serif;font-size:28px;box-shadow:inset 0 0 0 1px #ffffff40}.identity small,.section-heading small,.ai-desk small,.proposal small,.profile-side small{color:#0b6769;font-size:12px;font-weight:800;letter-spacing:.12em}.identity h1{margin:0;font-family:"STKaiti","KaiTi","Noto Serif SC",serif;font-size:30px;font-weight:700}.identity p{margin:2px 0 0;color:#60716d}.masthead__actions{display:flex;align-items:center;gap:10px;color:#60716d;font-size:13px}.quiet,.close,.page-state button,.support-link{min-height:38px;padding:0 14px;border:1px solid #b9cbc5;border-radius:10px;background:#fff;color:#174d4f;font:inherit}.close{width:40px;padding:0;font-size:26px}.dossier__body{display:grid;gap:24px;width:min(1380px,calc(100% - 40px));margin:0 auto;padding:28px 0 48px}.page-state{display:grid;min-height:60vh;place-content:center;justify-items:center;gap:8px;text-align:center}.page-state p{color:#60716d}
.ai-desk{overflow:hidden;border:1px solid #b9d0c9;border-radius:22px;background:#fdfefd;box-shadow:0 14px 45px #174d4f12}.ai-desk__heading{display:grid;grid-template-columns:minmax(320px,1fr) minmax(280px,520px);gap:24px;padding:24px 28px 18px;background:linear-gradient(105deg,#dceee9,#f7fbf9 68%)}.ai-desk h2,.section-heading h2,.proposal h2,.profile-side h2{margin:2px 0 0;font-size:24px}.ai-desk__heading>p{margin:4px 0;color:#526762;line-height:1.7}.dialogue{display:grid;gap:14px;max-height:360px;overflow:auto;padding:18px 28px 0}.dialogue__turn{display:grid;gap:10px}.teacher-quote{justify-self:end;max-width:78%;margin:0;padding:12px 16px;border-radius:16px 16px 4px 16px;background:#e7f0ee;line-height:1.65}.ai-reply{max-width:86%;padding:14px 18px;border-left:4px solid #0b6769;background:#f5f8f7}.ai-reply p,.ai-reply ul{margin:6px 0 0;line-height:1.65}.composer{margin:18px 28px 24px;border:1px solid #9fbdb5;border-radius:16px;background:#fff;box-shadow:0 8px 28px #174d4f0d}.composer textarea{box-sizing:border-box;width:100%;padding:16px;border:0;border-radius:16px 16px 0 0;outline:0;resize:vertical;background:transparent;font:inherit;line-height:1.65}.composer:focus-within{border-color:#0b6769;box-shadow:0 0 0 3px #0b67691c}.composer footer{display:flex;align-items:center;justify-content:space-between;padding:10px 12px 10px 16px;border-top:1px solid #e2ece8;color:#71817d;font-size:12px}.composer button,.proposal header>button{min-height:40px;padding:0 18px;border:0;border-radius:10px;background:#0b6769;color:white;font:inherit;font-weight:750}.composer button:disabled,.proposal button:disabled{opacity:.55}.notice,.error{margin:-10px 28px 20px;padding:10px 12px;border-radius:10px}.notice{background:#edf7f3;color:#176151}.error{background:#fff1ef;color:#a53d32}
.proposal{overflow:hidden;border:1px solid #d7b968;border-radius:20px;background:#fffdf6;box-shadow:0 12px 38px #7c65151a}.proposal>header{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:20px 24px;border-bottom:1px solid #eadba9;background:#fff8df}.proposal__summary{margin:0;padding:20px 24px;font-family:"STKaiti","KaiTi",serif;font-size:22px;line-height:1.65}.proposal__grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;padding:0 24px 20px}.proposal__grid article{padding:14px;border:1px solid #eadfb9;border-radius:12px;background:#fff}.proposal__grid ul{margin:8px 0 0;padding-left:20px}.proposal>footer{display:flex;align-items:center;justify-content:space-between;padding:12px 24px;border-top:1px solid #efe3bc;color:#75652f;font-size:13px}
.profile-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,360px);gap:22px;align-items:start}.profile-main,.profile-side>section{border:1px solid #c8d8d3;border-radius:20px;background:#fbfdfc}.profile-main{padding:26px}.section-heading{display:flex;align-items:end;justify-content:space-between;gap:20px;padding-bottom:18px;border-bottom:1px solid #dbe6e2}.section-heading>span{color:#667873;font-size:13px}.profile-summary{margin:22px 0;padding:20px 22px;border:0;border-radius:14px;background:#e4efec;color:#173f40;font-family:"STKaiti","KaiTi",serif;font-size:24px;line-height:1.7}.dimension-spine{position:relative;display:grid;gap:14px;margin-top:18px;padding-left:24px}.dimension-spine::before{position:absolute;inset:8px auto 8px 7px;width:2px;background:#89afa6;content:""}.dimension{position:relative;padding:17px 19px;border:1px solid #d5e2de;border-radius:14px;background:white}.dimension::before{position:absolute;top:23px;left:-23px;width:12px;height:12px;border:3px solid #eef4f2;border-radius:50%;background:#0b6769;content:""}.dimension h3{margin:0 0 8px;color:#154f51;font-size:17px}.dimension ul,.profile-side ul,.profile-side ol{margin:0;padding-left:20px;line-height:1.7}.empty-profile{display:grid;min-height:260px;place-content:center;text-align:center}.empty-profile p{color:#60716d}.profile-side{display:grid;gap:16px}.profile-side>section{overflow:hidden}.profile-side header{padding:18px 20px;border-bottom:1px solid #dbe6e2;background:#f3f8f6}.profile-side article{margin:14px;padding:16px;border:1px solid #d7e3df;border-radius:12px;background:#fff}.profile-side article h3{margin:0;color:#174d4f}.profile-side article p{margin:8px 0;line-height:1.6}.profile-side article strong{display:block;margin-top:12px;color:#526762;font-size:12px}.compact-empty,.questions>p{padding:18px 20px;color:#647570;line-height:1.6}.support-link{margin:0 14px 16px;width:calc(100% - 28px);border-color:#0b6769}.questions ol{padding:18px 38px 20px}.active-plans article strong{font-size:15px;color:#174d4f}.source-materials{padding:16px 20px;border:1px dashed #b9cbc5;border-radius:14px;background:#f8fbfa;color:#5d706b}.source-materials summary{cursor:pointer;font-weight:700}.source-materials p{margin-bottom:0}.dossier button:focus-visible,.dossier textarea:focus-visible,.source-materials summary:focus-visible{outline:3px solid #d49b2b;outline-offset:2px}
@media(max-width:900px){.dossier__masthead,.masthead__actions,.proposal>header,.proposal>footer{align-items:flex-start;flex-direction:column}.masthead__actions{width:100%}.close{position:absolute;top:18px;right:18px}.ai-desk__heading,.profile-layout{grid-template-columns:1fr}.dossier__body{width:min(100% - 24px,1380px)}.teacher-quote{max-width:92%}}
@media(max-width:560px){.dossier__masthead{padding:14px 16px}.identity__seal{width:44px;height:44px}.identity h1{font-size:25px}.masthead__actions>span,.masthead__actions>.quiet{display:none}.dossier__body{padding-top:14px}.ai-desk__heading,.dialogue,.profile-main{padding-inline:16px}.composer{margin-inline:16px}.proposal__grid{grid-template-columns:1fr;padding-inline:14px}.proposal__summary,.proposal>header,.proposal>footer{padding-inline:16px}.profile-summary{font-size:20px}.dimension-spine{padding-left:18px}.dimension::before{left:-17px}}
@media(prefers-reduced-motion:no-preference){.proposal{animation:proposal-in .28s ease-out}@keyframes proposal-in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}}
</style>
