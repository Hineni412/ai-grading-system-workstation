<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  supportApi,
  type SupportPlan,
  type SupportPlanOutcome,
  type SupportRecord,
} from '../api/support'
import type { DirectorySubject } from '../api/r1'
import { formatClassLabel } from '../format_class_label'
import AppButton from '@/components/design-system/AppButton.vue'

const props = defineProps<{ subject: DirectorySubject }>()
const emit = defineEmits<{ openProfile: [] }>()

const plans = ref<SupportPlan[]>([])
const records = ref<SupportRecord[]>([])
const failed = ref(false)
const busy = ref(false)
const message = ref('')

const planFormOpen = ref(false)
const planGoal = ref('')
const planActions = ref('')
const planReviewAt = ref('')
const aiDrafting = ref(false)
const aiDraftMessage = ref('')
const aiDraftFailed = ref(false)

const logFormPlanId = ref<string | null>(null)
const logDate = ref(new Date().toISOString().slice(0, 10))
const logContent = ref('')

const completePlanId = ref<string | null>(null)
const completeOutcome = ref<'effective' | 'ineffective'>('effective')
const completeResult = ref('')

const completedOpen = ref(false)

const activePlans = computed(() => plans.value.filter((plan) => plan.state === 'active'))
const completedPlans = computed(() => plans.value.filter((plan) => plan.state !== 'active'))
const duePlans = computed(() => activePlans.value.filter((plan) => {
  const at = new Date(plan.review_at).getTime()
  return !Number.isNaN(at) && at <= Date.now()
}))
const canSavePlan = computed(() => Boolean(
  planGoal.value.trim()
  && planActions.value.split('\n').map((line) => line.trim()).filter(Boolean).length
  && planReviewAt.value,
))

const outcomeLabels: Record<SupportPlanOutcome, string> = {
  effective: '有效',
  ineffective: '无效',
  continue: '继续观察',
}

function day(value: string | null | undefined): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 10)
  const pad = (unit: number) => String(unit).padStart(2, '0')
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}`
}

function logsFor(planId: string): SupportRecord[] {
  return records.value.filter((record) => record.plan_id === planId && record.state !== 'withdrawn')
}

async function load(): Promise<void> {
  failed.value = false
  try {
    const [planItems, recordItems] = await Promise.all([
      supportApi.listSupportPlans(props.subject.subject_id),
      supportApi.listRecords(props.subject.subject_id),
    ])
    plans.value = planItems
    records.value = recordItems
  } catch {
    failed.value = true
  }
}

function openPlanForm(): void {
  planFormOpen.value = true
  planGoal.value = ''
  planActions.value = ''
  planReviewAt.value = ''
  aiDraftMessage.value = ''
  aiDraftFailed.value = false
  message.value = ''
}

async function draftPlanWithAi(): Promise<void> {
  if (aiDrafting.value || busy.value) return
  aiDrafting.value = true
  aiDraftMessage.value = ''
  aiDraftFailed.value = false
  try {
    const draft = await supportApi.draftSupportPlan(props.subject.subject_id, crypto.randomUUID())
    planGoal.value = draft.goal
    planActions.value = draft.support_actions.join('\n')
    planReviewAt.value = draft.review_at
    aiDraftMessage.value = '已按当前档案起草，请核对修改；保存仍是你的决定。'
  } catch (error) {
    const detail = error instanceof Error && error.message ? `（${error.message}）` : ''
    aiDraftMessage.value = `起草失败，可重试或手动填写${detail}`
    aiDraftFailed.value = true
  } finally {
    aiDrafting.value = false
  }
}

async function createPlan(): Promise<void> {
  if (!canSavePlan.value || busy.value) return
  busy.value = true
  try {
    await supportApi.createSupportPlan(props.subject.subject_id, {
      goal: planGoal.value.trim(),
      support_actions: planActions.value.split('\n').map((line) => line.trim()).filter(Boolean),
      review_at: planReviewAt.value,
    })
    planFormOpen.value = false
    message.value = '方案已保存。行动和复查都由你执行，AI 不参与。'
    await load()
  } catch {
    message.value = '方案没有保存；现有方案与记录未改变。若其他页面已更新，请刷新后再核对。'
  } finally {
    busy.value = false
  }
}

function openLogForm(plan: SupportPlan): void {
  completePlanId.value = null
  logFormPlanId.value = plan.support_plan_id
  logDate.value = new Date().toISOString().slice(0, 10)
  logContent.value = ''
  message.value = ''
}

async function saveLog(plan: SupportPlan): Promise<void> {
  if (!logContent.value.trim() || !logDate.value || busy.value) return
  busy.value = true
  try {
    await supportApi.createRecord(props.subject.subject_id, {
      record_kind: 'fact',
      content: logContent.value.trim(),
      scene: '支持行动',
      source: '教师本人记录',
      basis: null,
      counterexample: null,
      category: 'general',
      observed_at: logDate.value,
      review_at: null,
      expires_at: null,
      plan_id: plan.support_plan_id,
    })
    logFormPlanId.value = null
    message.value = '已记到这个方案下。'
    await load()
  } catch {
    message.value = '这次行动没有记下；现有方案与记录未改变。若其他页面已更新，请刷新后再核对。'
  } finally {
    busy.value = false
  }
}

function openCompleteForm(plan: SupportPlan): void {
  logFormPlanId.value = null
  completePlanId.value = plan.support_plan_id
  completeOutcome.value = 'effective'
  completeResult.value = ''
  message.value = ''
}

async function completePlan(plan: SupportPlan, outcome: SupportPlanOutcome): Promise<void> {
  const result = completeResult.value.trim()
    || (outcome === 'continue' ? '继续观察，暂不下结论；之后可另建新一轮方案。' : '')
  if (!result || busy.value) return
  busy.value = true
  try {
    await supportApi.completeSupportPlan(plan.support_plan_id, plan.revision, result, outcome)
    completePlanId.value = null
    message.value = outcome === 'effective'
      ? '方案已完成；验证有效的做法已并入档案「已验证有效」支持重点。'
      : outcome === 'continue'
        ? '方案已按「继续观察」结束并留痕；之后可另建新一轮方案。'
        : '方案已完成，评价为无效；结果已留痕。'
    await load()
  } catch {
    message.value = '方案没有完成；现有方案与记录未改变。若其他页面已更新，请刷新后再核对。'
  } finally {
    busy.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <section class="action-panel">
    <header class="action-panel__header">
      <div>
        <p>当前学生</p>
        <h2>{{ subject.display_name }}</h2>
        <span>学号 {{ subject.source_student_id }} · {{ formatClassLabel(subject.class_label) }}</span>
      </div>
      <p>这里记录你为这名学生做了什么、有没有用。方案由 AI 起草、你核对保存；行动和效果由你记录。</p>
    </header>
    <p v-if="message" class="message" role="status">{{ message }}</p>
    <div v-if="failed" class="load-failed" role="alert">
      <p>支持方案暂时无法读取，现有方案与记录未受影响。</p>
      <AppButton variant="secondary" @click="load">重新读取</AppButton>
    </div>
    <template v-else>
      <aside v-if="duePlans.length" class="due-banner" role="status">
        <strong>{{ duePlans.length }} 个方案到了复查时间</strong>
        <span v-for="plan in duePlans" :key="plan.support_plan_id">
          {{ plan.goal }}（复查日期 {{ day(plan.review_at) }}）
        </span>
        <small>复查后在方案卡上点「完成并评效果」，或先记一次行动继续执行。</small>
      </aside>

      <section class="plans" aria-label="进行中的方案">
        <header class="plans__heading">
          <div><small>正在执行</small><h2>进行中的方案</h2></div>
          <AppButton v-if="!planFormOpen" variant="secondary" @click="openPlanForm">＋ 新建方案</AppButton>
        </header>

        <form v-if="planFormOpen" class="plan-form" @submit.prevent="createPlan">
          <div class="ai-draft">
            <AppButton variant="primary" :disabled="busy || aiDrafting" @click="draftPlanWithAi">
              {{ aiDrafting ? 'AI 正在读取档案并起草…' : '让 AI 起草方案' }}
            </AppButton>
            <span>AI 读取当前档案起草，你核对后保存</span>
          </div>
          <p v-if="aiDraftMessage" class="ai-draft__hint" :data-tone="aiDraftFailed ? 'error' : 'ok'" role="status">{{ aiDraftMessage }}</p>
          <label><span>目标</span><input v-model="planGoal" maxlength="1000" placeholder="例如：两周内形成稳定的任务核对习惯"></label>
          <label><span>行动清单（每行一条）</span><textarea v-model="planActions" rows="3" maxlength="4000" placeholder="例如：课前提供步骤卡"></textarea></label>
          <label><span>复查日期</span><input v-model="planReviewAt" type="date"></label>
          <footer>
            <span>保存仍是你的决定；保存后可在方案下记行动。</span>
            <div>
              <AppButton variant="primary" type="submit" :disabled="busy || aiDrafting || !canSavePlan">保存方案</AppButton>
              <AppButton variant="ghost" :disabled="busy" @click="planFormOpen = false">取消</AppButton>
            </div>
          </footer>
        </form>

        <p v-if="!activePlans.length && !planFormOpen" class="compact-empty">
          还没有进行中的方案。由你判断后建立，也可以让 AI 按当前档案起草。
        </p>

        <article v-for="plan in activePlans" :key="plan.support_plan_id" class="plan-card">
          <header class="plan-card__head">
            <div>
              <h3>{{ plan.goal }}</h3>
              <small>复查日期 {{ day(plan.review_at) }}<template v-if="duePlans.includes(plan)"> · 已到复查时间</template></small>
            </div>
          </header>
          <ul class="plan-card__actions">
            <li v-for="action in plan.support_actions" :key="action">{{ action }}</li>
          </ul>
          <div class="log-stream">
            <small>行动日志</small>
            <p v-if="!logsFor(plan.support_plan_id).length" class="compact-empty">还没有行动记录。</p>
            <div v-for="log in logsFor(plan.support_plan_id)" :key="log.record_id" class="log-item">
              <time>{{ day(log.observed_at) }}</time>
              <span>{{ log.content }}</span>
            </div>
          </div>
          <footer class="plan-card__ops">
            <AppButton variant="primary" :disabled="busy" @click="openLogForm(plan)">记一次行动</AppButton>
            <AppButton variant="secondary" :disabled="busy" @click="openCompleteForm(plan)">完成并评效果</AppButton>
          </footer>

          <form v-if="logFormPlanId === plan.support_plan_id" class="inline-form" @submit.prevent="saveLog(plan)">
            <label><span>日期</span><input v-model="logDate" type="date"></label>
            <label class="wide"><span>做了什么</span><textarea v-model="logContent" rows="3" maxlength="4000" placeholder="例如：课前发了步骤卡，学生当堂完成核对"></textarea></label>
            <footer>
              <div>
                <AppButton variant="primary" type="submit" :disabled="busy || !logContent.trim() || !logDate">保存行动</AppButton>
                <AppButton variant="ghost" :disabled="busy" @click="logFormPlanId = null">取消</AppButton>
              </div>
            </footer>
          </form>

          <form v-if="completePlanId === plan.support_plan_id" class="inline-form" @submit.prevent="completePlan(plan, completeOutcome)">
            <fieldset class="outcome-choice">
              <legend>效果</legend>
              <label><input v-model="completeOutcome" type="radio" value="effective">有效</label>
              <label><input v-model="completeOutcome" type="radio" value="ineffective">无效</label>
            </fieldset>
            <label class="wide"><span>结果说明</span><textarea v-model="completeResult" rows="3" maxlength="4000" placeholder="例如：复查时能独立核对步骤"></textarea></label>
            <footer>
              <span>评「有效」会把方案行动并入档案「已验证有效」；拿不准可继续观察，方案按原样结束，之后可另建新一轮方案。</span>
              <div>
                <AppButton variant="primary" type="submit" :disabled="busy || !completeResult.trim()">确认完成</AppButton>
                <AppButton variant="secondary" :disabled="busy" @click="completePlan(plan, 'continue')">继续观察</AppButton>
                <AppButton variant="ghost" :disabled="busy" @click="completePlanId = null">取消</AppButton>
              </div>
            </footer>
          </form>
        </article>
      </section>

      <section v-if="completedPlans.length" class="completed" aria-label="已完成方案">
        <button type="button" class="completed__toggle" :aria-expanded="completedOpen" @click="completedOpen = !completedOpen">
          已完成方案（{{ completedPlans.length }}）<span aria-hidden="true">{{ completedOpen ? '▴' : '▾' }}</span>
        </button>
        <template v-if="completedOpen">
          <article v-for="plan in completedPlans" :key="plan.support_plan_id" class="completed__item">
            <header>
              <strong>{{ plan.goal }}</strong>
              <span v-if="plan.outcome" class="outcome-badge" :data-outcome="plan.outcome">{{ outcomeLabels[plan.outcome] }}</span>
            </header>
            <p v-if="plan.result">{{ plan.result }}</p>
            <small>完成于 {{ day(plan.completed_at) }} · 原复查日期 {{ day(plan.review_at) }}</small>
          </article>
        </template>
      </section>

      <footer class="profile-entry">
        <button type="button" @click="emit('openProfile')">查看/补录观察记录 →</button>
        <span>观察记录收在学生档案的「成长与支持」里。</span>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.action-panel{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.action-panel__header{display:flex;align-items:end;justify-content:space-between;gap:var(--space-4);padding:var(--space-5);border-bottom:1px solid var(--border)}
.action-panel__header p{margin:0;color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.06em}
.action-panel__header h2{margin:2px 0}
.action-panel__header>p{max-width:420px;color:var(--color-text-secondary);font-size:var(--font-size-dense);font-weight:400;letter-spacing:0}
.message{margin:var(--space-3) var(--space-5) 0;padding:9px 11px;border-radius:var(--radius);background:var(--accent);color:var(--primary)}
.load-failed{display:grid;place-items:center;gap:var(--space-2);min-height:200px;padding:var(--space-6);text-align:center}
.load-failed p{color:var(--color-text-secondary)}
.due-banner{display:grid;gap:4px;margin:var(--space-3) var(--space-5) 0;padding:12px 14px;border-left:3px solid var(--color-warning);border-radius:0 var(--radius) var(--radius) 0;background:var(--color-warning-subtle)}
.due-banner span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.due-banner small{color:var(--muted-foreground)}
.plans{display:grid;gap:var(--space-3);padding:var(--space-4) var(--space-5)}
.plans__heading{display:flex;align-items:end;justify-content:space-between}
.plans__heading small{color:var(--primary);font-size:12px;font-weight:700;letter-spacing:.1em}
.plans__heading h2{margin:2px 0 0;font-size:18px}
.compact-empty{margin:0;padding:12px 0;color:var(--muted-foreground);line-height:1.6}
.plan-form,.inline-form{display:grid;gap:var(--space-3);padding:var(--space-3);border:1px dashed var(--border);border-radius:var(--radius);background:var(--muted)}
.plan-form footer,.inline-form footer{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);flex-wrap:wrap}
.plan-form footer>span,.inline-form footer>span{color:var(--color-text-secondary);font-size:12px}
.plan-form footer>div,.inline-form footer>div{display:flex;gap:var(--space-2)}
.ai-draft{display:flex;align-items:center;gap:var(--space-3);flex-wrap:wrap}
.ai-draft>span{color:var(--color-text-secondary);font-size:12px}
.ai-draft__hint{margin:0;padding:9px 11px;border-radius:var(--radius);background:var(--accent);color:var(--primary);font-size:var(--font-size-dense)}
.ai-draft__hint[data-tone="error"]{background:var(--color-warning-subtle);color:var(--color-warning)}
label{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}
.plan-card{display:grid;gap:var(--space-3);padding:var(--space-4);border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.plan-card__head h3{margin:0;font-size:16px}
.plan-card__head small{color:var(--muted-foreground)}
.plan-card__actions{margin:0;padding-left:19px;color:var(--color-text-secondary);line-height:1.65}
.log-stream{display:grid;gap:6px;padding:10px 12px;border-radius:var(--radius);background:var(--muted)}
.log-stream>small{color:var(--primary);font-weight:700}
.log-stream .compact-empty{padding:0}
.log-item{display:flex;gap:var(--space-3);align-items:baseline}
.log-item time{color:var(--muted-foreground);font-size:var(--font-size-dense);white-space:nowrap}
.plan-card__ops{display:flex;gap:var(--space-2)}
.outcome-choice{display:flex;gap:var(--space-4);margin:0;padding:0;border:0}
.outcome-choice legend{font-size:var(--font-size-dense);font-weight:650}
.outcome-choice label{display:flex;align-items:center;gap:6px;font-weight:400}
.completed{margin:0 var(--space-5) var(--space-3);border:1px solid var(--border);border-radius:var(--radius)}
.completed__toggle{display:flex;justify-content:space-between;width:100%;padding:12px var(--space-4);border:0;background:var(--muted);font:inherit;font-weight:650;cursor:pointer}
.completed__item{display:grid;gap:4px;margin:var(--space-3);padding:var(--space-3);border:1px solid var(--border);border-radius:var(--radius)}
.completed__item header{display:flex;align-items:center;gap:var(--space-2)}
.completed__item p{margin:0;color:var(--color-text-secondary);line-height:1.55}
.completed__item small{color:var(--muted-foreground)}
.outcome-badge{padding:0 8px;border-radius:999px;font-size:11px;font-weight:700;border:1px solid var(--border);color:var(--muted-foreground)}
.outcome-badge[data-outcome="effective"]{border-color:var(--primary);color:var(--primary)}
.outcome-badge[data-outcome="continue"]{border-color:var(--color-warning);color:var(--color-warning)}
.profile-entry{display:flex;align-items:center;gap:var(--space-3);padding:var(--space-3) var(--space-5) var(--space-4);border-top:1px solid var(--border)}
.profile-entry button{border:0;background:transparent;color:var(--primary);font:inherit;font-weight:650;cursor:pointer;padding:0}
.profile-entry button:hover{text-decoration:underline}
.profile-entry span{color:var(--muted-foreground);font-size:var(--font-size-dense)}
input,textarea{font:inherit}
@media(max-width:700px){.action-panel__header{flex-direction:column;align-items:start}.plan-card__ops{flex-wrap:wrap}}
</style>
